"""
adapters/lfs.py — Wrapper around the `lfs` CLI for quota and project operations.

All subprocess calls use list-form arguments (no shell=True) to prevent
injection.

Quota display: all lfs quota GET commands use -h (human-readable) so block
values are returned as strings like "4k", "3.023G", "100G".

Quota limits: lfs setquota accepts either KB integers or unit strings (G/T/M/K).
  int input  → treated as bytes, converted to KB internally
  str input  → passed directly to lfs (e.g. "100G", "1T")
"""

import os
import re
import shlex
import subprocess
from typing import Dict, List, Optional, Tuple

from config import LqaExecutionMode, get_settings
from errors import LustreCommandError, LustreTimeoutError, PermissionDenied

# ---------------------------------------------------------------------------
# Compiled regexes
# ---------------------------------------------------------------------------

# Data row from `lfs quota -h -p <id> <mnt>` (human-readable, with usage column)
# Example:
#   /lustre/aifs/client      4k       0k      2G       -      11        0       0       -
_QUOTA_ROW_H_RE = re.compile(
    r"^(/\S+)\s+"    # filesystem path (must start with /)
    r"(\S+)\s+"     # block used  (human-readable, e.g. "4k", "3.023G"; may have "*")
    r"(\S+)\s+"     # block softlimit
    r"(\S+)\s+"     # block hardlimit
    r"(\S+)\s+"     # block grace
    r"(\S+)\s+"     # inodes used  (integer even in -h mode)
    r"(\S+)\s+"     # inode softlimit
    r"(\S+)\s+"     # inode hardlimit
    r"(\S+)"        # inode grace
)

# Data row from `lfs quota -h -P --default <mnt>` (no usage column)
# Example:
#   /lustre/aifs/client      0k   1638P       -       0       0       -
_DEFAULT_QUOTA_H_RE = re.compile(
    r"^(/\S+)\s+"    # filesystem path (must start with /)
    r"(\S+)\s+"     # block softlimit
    r"(\S+)\s+"     # block hardlimit
    r"(\S+)\s+"     # block grace
    r"(\S+)\s+"     # inode softlimit
    r"(\S+)\s+"     # inode hardlimit
    r"(\S+)"        # inode grace
)

# Data row from `lfs quota -a -p <mnt>` (iterate all project quotas, no -h, numeric KB)
# Example:
#   /lustre/aifs/client  30001  16384000  0  20971520  -  2  0  0  -
_ITER_ROW_RE = re.compile(
    r"^(\S+)\s+"    # filesystem
    r"(\d+)\s+"     # quota_id
    r"(\d+)\s+"     # kbytes used
    r"(\d+)\s+"     # block softlimit (KB)
    r"(\d+)\s+"     # block hardlimit (KB)
    r"(\S+)\s+"     # block grace
    r"(\d+)\s+"     # inodes used
    r"(\d+)\s+"     # inode softlimit
    r"(\d+)\s+"     # inode hardlimit
    r"(\S+)"        # inode grace
)

# Kept for backward compat (used by lfs_iterate_project_quotas which needs numerics)
_QUOTA_ROW_RE = _ITER_ROW_RE  # alias — not used for single-ID queries any more


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run(cmd: List[str]) -> Tuple[str, str]:
    """Execute an lfs command; return (stdout, stderr)."""
    settings = get_settings()

    # ── SSH mode: run the command on the remote MGS node ─────────────────
    if settings.lqa_mode == LqaExecutionMode.SSH:
        from adapters import lctl as _lctl_mod  # local import to avoid cycles
        host = _lctl_mod.get_active_mgs_host()
        remote_cmd = " ".join(shlex.quote(a) for a in cmd)
        stdout, stderr, rc = _lctl_mod.exec_remote_cmd(
            host, remote_cmd, settings.lfs_timeout
        )
        if rc != 0:
            if "Operation not permitted" in stderr or "Permission denied" in stderr:
                raise PermissionDenied(
                    f"Permission denied: {stderr.strip()}", {"cmd": cmd}
                )
            raise LustreCommandError(
                f"lfs command failed (rc={rc}): {stderr.strip()}",
                {"cmd": cmd, "stderr": stderr, "returncode": rc},
            )
        return stdout, stderr

    # ── LOCAL mode: run as a subprocess ──────────────────────────────────
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=settings.lfs_timeout,
        )
    except subprocess.TimeoutExpired:
        raise LustreTimeoutError(
            f"lfs command timed out after {settings.lfs_timeout}s",
            {"cmd": cmd},
        )

    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")

    if result.returncode != 0:
        if "Operation not permitted" in stderr or "Permission denied" in stderr:
            raise PermissionDenied(f"Permission denied: {stderr.strip()}", {"cmd": cmd})
        raise LustreCommandError(
            f"lfs command failed (rc={result.returncode}): {stderr.strip()}",
            {"cmd": cmd, "stderr": stderr, "returncode": result.returncode},
        )
    return stdout, stderr


def _kb_to_bytes(kb: int) -> int:
    return kb * 1024


def _bytes_to_kb(b: int) -> int:
    """Round up to the next 1-KB boundary (Lustre quota granularity)."""
    if b <= 0:
        return 0
    return max(1, (b + 1023) // 1024)


def bytes_to_human(n: int) -> str:
    """
    Convert bytes to a human-readable string matching lfs quota -h scale.
    Used for accurate_usage display when summing per-project KB values.
    """
    if n == 0:
        return "0"
    _UNITS = [(1 << 50, "P"), (1 << 40, "T"), (1 << 30, "G"),
              (1 << 20, "M"), (1 << 10, "k")]
    for threshold, unit in _UNITS:
        if n >= threshold:
            val = n / threshold
            return f"{int(val)}{unit}" if val == int(val) else f"{val:.3f}{unit}"
    return f"{n}B"


def _parse_inode_str(s: str) -> int:
    """Parse an inode count from -h output (plain integer even in -h mode)."""
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0


def _parse_quota_row_h(stdout: str) -> Dict:
    """
    Parse the data row from `lfs quota -h` output (regular project/LQA query).
    Returns block fields as human-readable strings, inode fields as ints.
    """
    for line in stdout.splitlines():
        m = _QUOTA_ROW_H_RE.match(line.strip())
        if m:
            return {
                "block_usage":     m.group(2).rstrip("*"),  # strip over-quota marker
                "block_softlimit": m.group(3),
                "block_hardlimit": m.group(4),
                "block_grace":     m.group(5),
                "inode_usage":     _parse_inode_str(m.group(6)),
                "inode_softlimit": _parse_inode_str(m.group(7)),
                "inode_hardlimit": _parse_inode_str(m.group(8)),
                "inode_grace":     m.group(9),
            }
    raise LustreCommandError("Failed to parse quota output", {"stdout": stdout})


def _parse_default_quota_row_h(stdout: str) -> Dict:
    """
    Parse the data row from `lfs quota -h -P --default` (no usage column).
    Returns block fields as strings, inode fields as ints, block_usage as "0".
    """
    for line in stdout.splitlines():
        m = _DEFAULT_QUOTA_H_RE.match(line.strip())
        if m:
            return {
                "block_usage":     "0",   # default quota has no tracked usage
                "block_softlimit": m.group(2),
                "block_hardlimit": m.group(3),
                "block_grace":     m.group(4),
                "inode_usage":     0,
                "inode_softlimit": _parse_inode_str(m.group(5)),
                "inode_hardlimit": _parse_inode_str(m.group(6)),
                "inode_grace":     m.group(7),
            }
    raise LustreCommandError("Failed to parse default quota output", {"stdout": stdout})


def _parse_quota_row(stdout: str) -> Dict:
    """
    DEPRECATED internal parser for numeric (non -h) output.
    Kept for lfs_iterate_project_quotas which needs numeric KB values for summation.
    """
    # Reuse _QUOTA_ROW_RE (the old non-h pattern) to handle the non-h iterate output.
    # Note: _QUOTA_ROW_RE is now an alias for _ITER_ROW_RE which has an extra projid col.
    # For single-ID output without projid, use a fresh pattern here.
    _SINGLE_ROW_RE = re.compile(
        r"^(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S+)"
    )
    for line in stdout.splitlines():
        m = _SINGLE_ROW_RE.match(line.strip())
        if m:
            return {
                "block_usage":     _kb_to_bytes(int(m.group(2))),
                "block_softlimit": _kb_to_bytes(int(m.group(3))),
                "block_hardlimit": _kb_to_bytes(int(m.group(4))),
                "block_grace":     m.group(5),
                "inode_usage":     int(m.group(6)),
                "inode_softlimit": int(m.group(7)),
                "inode_hardlimit": int(m.group(8)),
                "inode_grace":     m.group(9),
            }
    raise LustreCommandError("Failed to parse quota output", {"stdout": stdout})


def _limit_arg(v) -> str:
    """
    Convert a quota limit value to the argument string for lfs setquota.
    int: treated as bytes, converted to KB (Lustre internal unit).
    str: unit string like '100G' or '1T', passed directly to lfs.
    """
    if isinstance(v, str):
        return v  # already validated unit string
    return str(_bytes_to_kb(v)) if v > 0 else "0"


# ---------------------------------------------------------------------------
# Project quota
# ---------------------------------------------------------------------------

def lfs_get_project_quota(mnt: str, projid: int) -> Dict:
    """lfs quota -h -p <projid> <mnt>"""
    settings = get_settings()
    cmd = [settings.lfs_path, "quota", "-h", "-p", str(projid), mnt]
    stdout, _ = _run(cmd)
    return _parse_quota_row_h(stdout)


def lfs_set_project_quota(mnt: str, projid: int, req: Dict) -> None:
    """lfs setquota -p <projid> [-b bs] [-B bh] [-i is] [-I ih] <mnt>"""
    settings = get_settings()
    cmd = [settings.lfs_path, "setquota", "-p", str(projid)]
    if req.get("block_softlimit") is not None:
        cmd += ["-b", _limit_arg(req["block_softlimit"])]
    if req.get("block_hardlimit") is not None:
        cmd += ["-B", _limit_arg(req["block_hardlimit"])]
    if req.get("inode_softlimit") is not None:
        cmd += ["-i", str(req["inode_softlimit"])]
    if req.get("inode_hardlimit") is not None:
        cmd += ["-I", str(req["inode_hardlimit"])]
    cmd.append(mnt)
    _run(cmd)


def lfs_delete_project_quota(mnt: str, projid: int) -> None:
    """lfs setquota -p <projid> --delete <mnt>"""
    settings = get_settings()
    cmd = [settings.lfs_path, "setquota", "-p", str(projid), "--delete", mnt]
    _run(cmd)


def lfs_reset_project_quota(mnt: str, projid: int) -> None:
    """lfs setquota -p <projid> -b 0 -B 0 -i 0 -I 0 <mnt>
    Sets all limits to 0 (unlimited), which is the standard way to clear limits.
    """
    settings = get_settings()
    cmd = [settings.lfs_path, "setquota", "-p", str(projid),
           "-b", "0", "-B", "0", "-i", "0", "-I", "0", mnt]
    _run(cmd)


def lfs_get_default_project_quota(mnt: str) -> Dict:
    """lfs quota -h -P --default <mnt>"""
    settings = get_settings()
    cmd = [settings.lfs_path, "quota", "-h", "-P", "--default", mnt]
    stdout, _ = _run(cmd)
    return _parse_default_quota_row_h(stdout)


def lfs_set_default_project_quota(mnt: str, req: Dict) -> None:
    """lfs setquota -P [-b bs] [-B bh] [-i is] [-I ih] <mnt>"""
    settings = get_settings()
    cmd = [settings.lfs_path, "setquota", "-P"]
    if req.get("block_softlimit") is not None:
        cmd += ["-b", _limit_arg(req["block_softlimit"])]
    if req.get("block_hardlimit") is not None:
        cmd += ["-B", _limit_arg(req["block_hardlimit"])]
    if req.get("inode_softlimit") is not None:
        cmd += ["-i", str(req["inode_softlimit"])]
    if req.get("inode_hardlimit") is not None:
        cmd += ["-I", str(req["inode_hardlimit"])]
    cmd.append(mnt)
    _run(cmd)


# ---------------------------------------------------------------------------
# Grace times
# ---------------------------------------------------------------------------

_GRACE_RE = re.compile(r"(block|inode)\s+grace\s+time:\s*(\S+)", re.IGNORECASE)


def _parse_grace_output(stdout: str) -> Dict:
    block_grace = "none"
    inode_grace = "none"
    for m in _GRACE_RE.finditer(stdout):
        val = m.group(2).rstrip(";")
        if m.group(1).lower() == "block":
            block_grace = val
        else:
            inode_grace = val
    return {"block_grace": block_grace, "inode_grace": inode_grace}


def lfs_get_grace_time(mnt: str) -> Dict:
    """lfs quota -t -p <mnt>"""
    settings = get_settings()
    cmd = [settings.lfs_path, "quota", "-t", "-p", mnt]
    stdout, _ = _run(cmd)
    return _parse_grace_output(stdout)


def lfs_set_grace_time(mnt: str, req: Dict) -> None:
    """lfs setquota -t -p [-b bgrace] [-i igrace] <mnt>"""
    settings = get_settings()
    cmd = [settings.lfs_path, "setquota", "-t", "-p"]
    if req.get("block_grace"):
        cmd += ["-b", req["block_grace"]]
    if req.get("inode_grace"):
        cmd += ["-i", req["inode_grace"]]
    cmd.append(mnt)
    _run(cmd)


# ---------------------------------------------------------------------------
# LQA quota
# ---------------------------------------------------------------------------

def lfs_get_lqa_quota(mnt: str, lqa_name: str) -> Dict:
    """lfs quota -h -P --lqa <name> <mnt>"""
    settings = get_settings()
    cmd = [settings.lfs_path, "quota", "-h", "-P", "--lqa", lqa_name, mnt]
    stdout, _ = _run(cmd)
    return _parse_quota_row_h(stdout)


def lfs_set_lqa_quota(mnt: str, lqa_name: str, req: Dict) -> None:
    """lfs setquota -P --lqa <name> [-b bs] [-B bh] <mnt>  (no inode limits for LQA)"""
    settings = get_settings()
    cmd = [settings.lfs_path, "setquota", "-P", "--lqa", lqa_name]
    if req.get("block_softlimit") is not None:
        cmd += ["-b", _limit_arg(req["block_softlimit"])]
    if req.get("block_hardlimit") is not None:
        cmd += ["-B", _limit_arg(req["block_hardlimit"])]
    cmd.append(mnt)
    _run(cmd)


def lfs_get_lqa_grace(mnt: str, lqa_name: str) -> Dict:
    """lfs quota -t -P --lqa <name> <mnt>"""
    settings = get_settings()
    cmd = [settings.lfs_path, "quota", "-t", "-P", "--lqa", lqa_name, mnt]
    stdout, _ = _run(cmd)
    return _parse_grace_output(stdout)


def lfs_set_lqa_grace(mnt: str, lqa_name: str, req: Dict) -> None:
    """lfs setquota -t -P --lqa <name> [-b bgrace] [-i igrace] <mnt>"""
    settings = get_settings()
    cmd = [settings.lfs_path, "setquota", "-t", "-P", "--lqa", lqa_name]
    if req.get("block_grace"):
        cmd += ["-b", req["block_grace"]]
    if req.get("inode_grace"):
        cmd += ["-i", req["inode_grace"]]
    cmd.append(mnt)
    _run(cmd)


# ---------------------------------------------------------------------------
# Iterate all project quotas (for accurate LQA usage calculation)
# ---------------------------------------------------------------------------

def lfs_iterate_project_quotas(mnt: str) -> Dict[int, Dict]:
    """
    Execute `lfs quota -a -p <mnt>` to retrieve all project quota records
    in a single ioctl (LUSTRE_Q_ITERQUOTA).

    Returns: {projid: {"block_usage": bytes, "block_softlimit": bytes, ...}}

    Note: The `-a` flag iterates all quota IDs and adds a quota_id column
    before the standard columns. If this exact format differs on your Lustre
    version, adjust _ITER_ROW_RE accordingly.
    """
    settings = get_settings()
    cmd = [settings.lfs_path, "quota", "-a", "-p", mnt]
    stdout, _ = _run(cmd)
    result: Dict[int, Dict] = {}
    for line in stdout.splitlines():
        m = _ITER_ROW_RE.match(line.strip())
        if m:
            projid = int(m.group(2))
            result[projid] = {
                "block_usage":     _kb_to_bytes(int(m.group(3))),
                "block_softlimit": _kb_to_bytes(int(m.group(4))),
                "block_hardlimit": _kb_to_bytes(int(m.group(5))),
                "block_grace":     m.group(6),
                "inode_usage":     int(m.group(7)),
                "inode_softlimit": int(m.group(8)),
                "inode_hardlimit": int(m.group(9)),
                "inode_grace":     m.group(10),
            }
    return result


# ---------------------------------------------------------------------------
# Directory project operations
# ---------------------------------------------------------------------------

def lfs_get_dir_project(path: str) -> Dict:
    """
    lfs project -d <path>
    Output example: "20001 P /lustre/aifs/client/LQA_prj3/prj30001"
    Returns: {"projid": int, "inherit_flag": bool, "path": str}
    """
    settings = get_settings()
    cmd = [settings.lfs_path, "project", "-d", path]
    stdout, _ = _run(cmd)
    for line in stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2:
            try:
                return {
                    "projid":       int(parts[0]),
                    "inherit_flag": parts[1].strip() == "P",
                    "path":         parts[2].strip() if len(parts) > 2 else path,
                }
            except (ValueError, IndexError):
                continue
    raise LustreCommandError(
        "Failed to parse lfs project output", {"stdout": stdout, "path": path}
    )


def lfs_get_dir_projects_batch(paths: List[str]) -> Dict[str, int]:
    """
    lfs project -d <path1> <path2> ... — query projids for many directories
    in a single subprocess/SSH call, avoiding per-directory overhead.

    Returns: {absolute_path: projid}   (projid == 0 means not set)

    Any path that cannot be parsed is recorded with projid 0.
    """
    if not paths:
        return {}
    settings = get_settings()
    cmd = [settings.lfs_path, "project", "-d"] + paths
    try:
        stdout, _ = _run(cmd)
    except Exception:
        return {p: 0 for p in paths}
    result: Dict[str, int] = {p: 0 for p in paths}  # default: unset
    for line in stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 3:
            try:
                projid = int(parts[0])
                path = parts[2].strip()
                result[path] = projid
            except (ValueError, IndexError):
                continue
    return result


def lfs_set_dir_project(path: str, projid: int) -> None:
    """
    lfs project -srp <projid> <path>
      -s  set the PROJID_INHERIT flag on directories
      -r  apply recursively to all descendants
      -p  set the given projid
    """
    settings = get_settings()
    cmd = [settings.lfs_path, "project", "-srp", str(projid), path]
    _run(cmd)


def lfs_clear_dir_project(path: str) -> None:
    """
    lfs project -Cr <path>
      -C  clear the inherit flag and reset projid to 0
      -r  apply recursively
    """
    settings = get_settings()
    cmd = [settings.lfs_path, "project", "-Cr", path]
    _run(cmd)


# ---------------------------------------------------------------------------
# Directory listing (SSH-aware)
# ---------------------------------------------------------------------------

def lfs_list_subdirs(path: str) -> List[str]:
    """
    Return the absolute paths of the immediate subdirectories of *path*.

    In LOCAL mode this uses os.scandir (no subprocess).
    In SSH mode a remote ``find`` is executed on the MGS node so that the
    caller never needs direct filesystem access to the Lustre mount.
    """
    settings = get_settings()
    if settings.lqa_mode == LqaExecutionMode.SSH:
        from adapters import lctl as _lctl_mod
        host = _lctl_mod.get_active_mgs_host()
        # find is available on all Linux nodes; -print0 / xargs not needed
        # because we only have one path argument (from config, not user input).
        cmd_str = (
            f"find {shlex.quote(path)} -maxdepth 1 -mindepth 1 -type d"
        )
        stdout, stderr, rc = _lctl_mod.exec_remote_cmd(
            host, cmd_str, settings.lfs_timeout
        )
        if rc != 0:
            if "Permission denied" in stderr:
                raise PermissionDenied(
                    f"Permission denied reading directory '{path}'", {"path": path}
                )
            raise LustreCommandError(
                f"Failed to list subdirectories of '{path}'",
                {"path": path, "stderr": stderr},
            )
        return [line.strip() for line in stdout.splitlines() if line.strip()]
    else:
        try:
            return [
                e.path
                for e in os.scandir(path)
                if e.is_dir(follow_symlinks=False)
            ]
        except PermissionError as exc:
            raise PermissionDenied(
                f"Permission denied reading directory '{path}'", {"path": path}
            ) from exc
