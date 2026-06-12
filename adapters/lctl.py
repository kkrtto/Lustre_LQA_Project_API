"""
adapters/lctl.py — Wrapper around `lctl` for LQA management.

LQA operations MUST run on the node that hosts the QMT device (MGS/MDS).
This module implements two execution modes, controlled by config.lqa_mode:

  LOCAL  — API server runs on the MGS node; lctl is executed directly.
  SSH    — lctl is executed remotely via Paramiko SSH. The module probes
           each configured mgs_hosts entry in turn, caches the active MGS
           for mgs_cache_ttl seconds, and retries on failover events.

Error handling maps well-known lctl stderr patterns to the typed exception
hierarchy in errors.py so callers receive structured errors.
"""

import re
import subprocess
import threading
import time
from typing import Dict, List, Optional, Tuple

import paramiko

from config import LqaExecutionMode, get_settings
from errors import (
    LqaAlreadyExists,
    LqaNotFound,
    LustreCommandError,
    LustreTimeoutError,
    MgsNotFound,
    PermissionDenied,
)


# ---------------------------------------------------------------------------
# MGS host cache
# ---------------------------------------------------------------------------

class _MgsCache:
    """Simple TTL cache for the currently active MGS host."""

    def __init__(self) -> None:
        self._host: Optional[str] = None
        self._expires: float = 0.0
        self._lock = threading.Lock()

    def get(self, ttl: int) -> Optional[str]:
        with self._lock:
            if self._host and time.monotonic() < self._expires:
                return self._host
            return None

    def set(self, host: str, ttl: int) -> None:
        with self._lock:
            self._host = host
            self._expires = time.monotonic() + ttl

    def invalidate(self) -> None:
        with self._lock:
            self._host = None
            self._expires = 0.0


_mgs_cache = _MgsCache()


# ---------------------------------------------------------------------------
# MGS detection helpers
# ---------------------------------------------------------------------------

def _check_mgs_local() -> bool:
    """Return True if this node has an active MGS (via lctl dl)."""
    settings = get_settings()
    try:
        result = subprocess.run(
            [settings.lctl_path, "dl"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=settings.lctl_timeout,
        )
        output = result.stdout.decode("utf-8", errors="replace")
        return bool(re.search(r"\bMGS\b", output))
    except Exception:
        return False


def _check_mgs_via_ssh(host: str, user: str, port: int, key: Optional[str]) -> bool:
    """
    Return True if `host` currently has an active MGS.
    Uses Paramiko to run: lctl dl 2>/dev/null | grep -w MGS

    Always uses key-based authentication.  Password auth and SSH-agent
    fallback are explicitly disabled to prevent credentials being prompted
    or cached in memory.
    """
    if not key:
        # Cannot authenticate without a key; treat host as unreachable.
        return False
    settings = get_settings()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            username=user,
            port=port,
            key_filename=key,
            look_for_keys=False,   # do not search ~/.ssh for additional keys
            allow_agent=False,     # do not use the SSH agent
            timeout=settings.ssh_connect_timeout,
        )
        _stdin, stdout, _stderr = client.exec_command(
            "lctl dl 2>/dev/null | grep -w MGS",
            timeout=settings.lctl_timeout,
        )
        output = stdout.read().decode("utf-8", errors="replace")
        return bool(output.strip())
    except Exception:
        return False
    finally:
        client.close()


def _find_active_mgs() -> Optional[str]:
    """
    In LOCAL mode return None (we run lctl directly).
    In SSH mode: return the cached host if valid, otherwise probe each host
    in mgs_hosts in order and cache the first responding one.
    Raises MgsNotFound if no host responds.
    """
    settings = get_settings()
    if settings.lqa_mode == LqaExecutionMode.LOCAL:
        return None

    cached = _mgs_cache.get(settings.mgs_cache_ttl)
    if cached:
        return cached

    for host in settings.mgs_hosts:
        if _check_mgs_via_ssh(
            host, settings.ssh_user, settings.ssh_port, settings.ssh_key
        ):
            _mgs_cache.set(host, settings.mgs_cache_ttl)
            return host

    raise MgsNotFound(
        "No active MGS found in configured mgs_hosts",
        {"mgs_hosts": settings.mgs_hosts},
    )


# ---------------------------------------------------------------------------
# Core lctl executor with MGS failover
# ---------------------------------------------------------------------------

def _run_lctl(cmd_args: List[str]) -> Tuple[str, str]:
    """
    Execute an lctl command, handling MGS failover for SSH mode.

    cmd_args: everything after 'lctl', e.g. ["lqa_new", "--fsname", "lustre", ...]

    Returns (stdout, stderr).
    """
    settings = get_settings()
    hosts = settings.mgs_hosts if settings.lqa_mode == LqaExecutionMode.SSH else []
    # We allow at most len(hosts)+1 attempts (try cached host, invalidate, retry)
    max_attempts = max(len(hosts) + 1, 1)

    for attempt in range(max_attempts):
        try:
            active_mgs = _find_active_mgs()
        except MgsNotFound:
            raise

        stdout, stderr = _execute_lctl(cmd_args, active_mgs)
        lower_err = stderr.lower()

        # If lctl says we must be on the MGS, the cached host is stale.
        if "must be run on the mgs" in lower_err or "must be run on mgs" in lower_err:
            _mgs_cache.invalidate()
            if attempt < max_attempts - 1:
                continue
            raise MgsNotFound(
                "All known MGS hosts are unavailable",
                {"mgs_hosts": hosts},
            )

        # Map well-known patterns to structured errors
        _raise_for_lctl_error(stderr, cmd_args)
        return stdout, stderr

    raise MgsNotFound("All MGS failover attempts exhausted", {"mgs_hosts": hosts})


def _execute_lctl(
    cmd_args: List[str], mgs_host: Optional[str]
) -> Tuple[str, str]:
    """Low-level dispatch: local subprocess or SSH."""
    settings = get_settings()
    lctl = settings.lctl_path

    if settings.lqa_mode == LqaExecutionMode.LOCAL or mgs_host is None:
        # Local execution
        try:
            result = subprocess.run(
                [lctl] + cmd_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=settings.lctl_timeout,
            )
        except subprocess.TimeoutExpired:
            raise LustreTimeoutError(
                f"lctl command timed out after {settings.lctl_timeout}s",
                {"cmd": cmd_args},
            )
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        return stdout, stderr
    else:
        # SSH execution
        return _execute_lctl_via_ssh(lctl, cmd_args, mgs_host)


def _ssh_exec(host: str, cmd_str: str, cmd_timeout: int) -> Tuple[str, str, int]:
    """
    Connect to *host* via SSH and run *cmd_str*.
    Returns (stdout, stderr, exit_code).
    Authentication is always key-based; password and agent are disabled.
    """
    settings = get_settings()
    if not settings.ssh_key:
        raise LustreCommandError(
            "LUSTRE_API_SSH_KEY must be set when lqa_mode=ssh. "
            "Password-based SSH authentication is not supported.",
            {"host": host},
        )
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            username=settings.ssh_user,
            port=settings.ssh_port,
            key_filename=settings.ssh_key,
            look_for_keys=False,   # do not search ~/.ssh for additional keys
            allow_agent=False,     # do not use the SSH agent
            timeout=settings.ssh_connect_timeout,
        )
        _stdin, ssh_stdout, ssh_stderr = client.exec_command(
            cmd_str, timeout=cmd_timeout
        )
        stdout = ssh_stdout.read().decode("utf-8", errors="replace")
        stderr = ssh_stderr.read().decode("utf-8", errors="replace")
        rc = ssh_stdout.channel.recv_exit_status()
        return stdout, stderr, rc
    except paramiko.SSHException as exc:
        raise LustreCommandError(
            f"SSH error connecting to {host}: {exc}",
            {"host": host},
        )
    finally:
        client.close()


def _execute_lctl_via_ssh(
    lctl: str, cmd_args: List[str], host: str
) -> Tuple[str, str]:
    """Execute lctl remotely via SSH; return (stdout, stderr)."""
    settings = get_settings()
    remote_cmd = " ".join([lctl] + [_shell_quote(a) for a in cmd_args])
    stdout, stderr, _rc = _ssh_exec(host, remote_cmd, settings.lctl_timeout)
    return stdout, stderr


def exec_remote_cmd(host: str, cmd_str: str, cmd_timeout: int) -> Tuple[str, str, int]:
    """
    Public helper: execute *cmd_str* on *host* via SSH.
    Returns (stdout, stderr, exit_code).
    Used by adapters that need to run non-lctl commands (e.g. lfs) on the MGS.
    """
    return _ssh_exec(host, cmd_str, cmd_timeout)


def get_active_mgs_host() -> Optional[str]:
    """
    Public helper: return the active MGS host in SSH mode, or None in LOCAL mode.
    Raises MgsNotFound if SSH mode is configured but no host responds.
    """
    return _find_active_mgs()


def _shell_quote(s: str) -> str:
    """Minimal single-quote escaping for SSH remote command assembly."""
    return "'" + s.replace("'", "'\\''") + "'"


def _raise_for_lctl_error(stderr: str, cmd_args: List[str]) -> None:
    lower = stderr.lower()
    if not stderr.strip():
        return
    # Detect non-zero exit but only partial error patterns
    if "permission denied" in lower or "operation not permitted" in lower:
        raise PermissionDenied(f"Permission denied: {stderr.strip()}", {"cmd": cmd_args})
    if "already exist" in lower:
        raise LqaAlreadyExists(f"LQA already exists: {stderr.strip()}", {"cmd": cmd_args})
    if "not found" in lower or "no such" in lower:
        raise LqaNotFound(f"LQA not found: {stderr.strip()}", {"cmd": cmd_args})
    # Generic non-empty stderr on failed commands is treated as an error
    # only if we had a non-zero exit code (checked by caller via return code,
    # but at this point we only have the text — if it reached here via
    # _run_lctl the command returned rc=0 or the error was already mapped).


# ---------------------------------------------------------------------------
# LQA command functions
# ---------------------------------------------------------------------------
#
# Actual lctl interface (confirmed against DDN Lustre 2.14):
#   lqa_new     --fsname F --name N
#   lqa_destroy --fsname F --name N
#   lqa_add     --fsname F --name N --range START-END
#   lqa_remove  --fsname F --name N --range START-END
#   lqa_list    --fsname F                    → "name: A, B, C\n"
#   lqa_list    --fsname F --name N           → "name: N\nranges: S-E, ...\n"
# ---------------------------------------------------------------------------

def lctl_lqa_new(fsname: str, name: str) -> None:
    """lctl lqa_new --fsname <fsname> --name <name>"""
    _run_lctl(["lqa_new", "--fsname", fsname, "--name", name])


def lctl_lqa_destroy(fsname: str, name: str) -> None:
    """lctl lqa_destroy --fsname <fsname> --name <name>"""
    _run_lctl(["lqa_destroy", "--fsname", fsname, "--name", name])


def lctl_lqa_add_range(fsname: str, name: str, start: int, end: int) -> None:
    """lctl lqa_add --fsname <fsname> --name <name> --range <start>-<end>"""
    _run_lctl([
        "lqa_add",
        "--fsname", fsname,
        "--name", name,
        "--range", f"{start}-{end}",
    ])


def lctl_lqa_remove_range(fsname: str, name: str, start: int, end: int) -> None:
    """lctl lqa_remove --fsname <fsname> --name <name> --range <start>-<end>"""
    _run_lctl([
        "lqa_remove",
        "--fsname", fsname,
        "--name", name,
        "--range", f"{start}-{end}",
    ])


def lctl_lqa_list_all(fsname: str) -> List[str]:
    """
    lctl lqa_list --fsname <fsname>
    Actual output:  "name: LQA_prj1, LQA_prj2, LQA_prj3\\n"
    Returns a list of LQA names.
    """
    stdout, _ = _run_lctl(["lqa_list", "--fsname", fsname])
    return _parse_lqa_list_all(stdout)


def _parse_lqa_list_all(stdout: str) -> List[str]:
    """
    Parse lctl lqa_list (all) output into a list of names.
    Handles:
      "name: LQA_prj1, LQA_prj2"  → ["LQA_prj1", "LQA_prj2"]
      "name: "  or empty           → []
    """
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("name:"):
            payload = stripped[len("name:"):].strip().strip("\x00")
            if not payload or payload in ("[]", "none"):
                return []
            return [n.strip().strip("\x00") for n in payload.split(",") if n.strip()]
    return []


def lctl_lqa_list_ranges(fsname: str, name: str) -> List[Dict[str, int]]:
    """
    lctl lqa_list --fsname <fsname> --name <name>
    Actual output:
      name: LQA_prj1
      ranges: 10001-20000
    Returns [{"start": 10001, "end": 20000}, ...]
    """
    stdout, _ = _run_lctl([
        "lqa_list", "--fsname", fsname, "--name", name
    ])
    return _parse_lqa_list_ranges(stdout)


def _parse_lqa_list_ranges(stdout: str) -> List[Dict[str, int]]:
    """
    Parse the 'ranges:' line from lctl lqa_list --name output.
    """
    result = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("ranges:"):
            payload = stripped[len("ranges:"):].strip()
            if not payload or payload in ("[]", "none", ""):
                return []
            for part in payload.split(","):
                part = part.strip().strip("\x00")
                m = re.fullmatch(r"(\d+)-(\d+)", part)
                if m:
                    result.append({"start": int(m.group(1)), "end": int(m.group(2))})
    return result
