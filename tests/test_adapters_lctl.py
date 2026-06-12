"""
tests/test_adapters_lctl.py — Unit tests for adapters/lctl.py.

Tests cover:
  • Output parsers (_parse_lqa_list_all, _parse_lqa_list_ranges)
  • MGS cache TTL and invalidation
  • Local execution path (LQA_MODE=local)
  • MGS failover behaviour (SSH mode simulated without real SSH)
"""

import time
from unittest.mock import MagicMock, patch

import pytest

import adapters.lctl as lctl
from errors import LqaAlreadyExists, LqaNotFound, MgsNotFound, PermissionDenied


# ── Output parsers ────────────────────────────────────────────────────────────

class TestParseLqaListAll:
    def test_empty_list(self):
        assert lctl._parse_lqa_list_all("name: \n") == []

    def test_single_name(self):
        assert lctl._parse_lqa_list_all("name: proj_A\n") == ["proj_A"]

    def test_multiple_names(self):
        result = lctl._parse_lqa_list_all("name: proj_A, proj_B, proj_C\n")
        assert result == ["proj_A", "proj_B", "proj_C"]

    def test_no_matching_line(self):
        assert lctl._parse_lqa_list_all("some other output\n") == []

    def test_strips_null_padding(self):
        # lctl sometimes pads names with \x00 bytes
        result = lctl._parse_lqa_list_all("name: proj_A\x00\n")
        assert result == ["proj_A"]


class TestParseLqaListRanges:
    def test_empty_ranges(self):
        assert lctl._parse_lqa_list_ranges("ranges: []\n") == []

    def test_single_range(self):
        result = lctl._parse_lqa_list_ranges("ranges: 20001-30000\n")
        assert result == [{"start": 20001, "end": 30000}]

    def test_multiple_ranges(self):
        result = lctl._parse_lqa_list_ranges("ranges: 20001-30000, 40001-50000\n")
        assert len(result) == 2
        assert result[1] == {"start": 40001, "end": 50000}

    def test_no_matching_line(self):
        assert lctl._parse_lqa_list_ranges("no ranges here\n") == []


# ── MgsCache ──────────────────────────────────────────────────────────────────

class TestMgsCache:
    def setup_method(self):
        self.cache = lctl._MgsCache()

    def test_set_and_get_within_ttl(self):
        self.cache.set("10.0.0.1", ttl=60)
        assert self.cache.get(ttl=60) == "10.0.0.1"

    def test_expired_returns_none(self):
        self.cache.set("10.0.0.1", ttl=60)
        # Force expiry by overwriting the timestamp
        self.cache._expires = time.monotonic() - 1
        assert self.cache.get(ttl=60) is None

    def test_invalidate(self):
        self.cache.set("10.0.0.1", ttl=60)
        self.cache.invalidate()
        assert self.cache.get(ttl=60) is None


# ── Local execution ───────────────────────────────────────────────────────────

def _local_run_result(stdout="", stderr="", returncode=0):
    r = MagicMock()
    r.stdout = stdout.encode()
    r.stderr = stderr.encode()
    r.returncode = returncode
    return r


@pytest.fixture()
def local_mode(monkeypatch):
    monkeypatch.setenv("LUSTRE_API_LQA_MODE", "local")
    from config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_lctl_lqa_list_all_local(local_mode):
    with patch("subprocess.run", return_value=_local_run_result(
        stdout="name: proj_A, proj_B\n"
    )):
        result = lctl.lctl_lqa_list_all("aifs")
    assert result == ["proj_A", "proj_B"]


def test_lctl_lqa_list_ranges_local(local_mode):
    with patch("subprocess.run", return_value=_local_run_result(
        stdout="name: proj_A\nranges: 20001-30000\n"
    )):
        result = lctl.lctl_lqa_list_ranges("aifs", "proj_A")
    assert result == [{"start": 20001, "end": 30000}]


def test_lctl_lqa_new_local(local_mode):
    with patch("subprocess.run", return_value=_local_run_result()) as mock:
        lctl.lctl_lqa_new("aifs", "proj_A")
    cmd = mock.call_args[0][0]
    assert "lqa_new" in cmd
    assert "--fsname" in cmd and "aifs" in cmd
    assert "--name" in cmd and "proj_A" in cmd


def test_lctl_lqa_add_range_format(local_mode):
    with patch("subprocess.run", return_value=_local_run_result()) as mock:
        lctl.lctl_lqa_add_range("aifs", "proj_A", 20001, 30000)
    cmd = mock.call_args[0][0]
    assert "lqa_add" in cmd
    assert "--range" in cmd
    idx = cmd.index("--range")
    assert cmd[idx + 1] == "20001-30000"


def test_lctl_error_already_exists(local_mode):
    with patch("subprocess.run", return_value=_local_run_result(
        stderr="lqa already exist\n", returncode=0
    )):
        with pytest.raises(LqaAlreadyExists):
            lctl.lctl_lqa_new("aifs", "proj_A")


def test_lctl_error_not_found(local_mode):
    with patch("subprocess.run", return_value=_local_run_result(
        stderr="lqa not found\n", returncode=0
    )):
        with pytest.raises(LqaNotFound):
            lctl.lctl_lqa_destroy("aifs", "proj_A")


def test_lctl_error_permission_denied(local_mode):
    with patch("subprocess.run", return_value=_local_run_result(
        stderr="permission denied\n", returncode=0
    )):
        with pytest.raises(PermissionDenied):
            lctl.lctl_lqa_new("aifs", "proj_A")


# ── MGS failover (SSH mode) ───────────────────────────────────────────────────

@pytest.fixture()
def ssh_mode(monkeypatch):
    monkeypatch.setenv("LUSTRE_API_LQA_MODE", "ssh")
    monkeypatch.setenv("LUSTRE_API_MGS_HOSTS", "10.0.0.1,10.0.0.2")
    monkeypatch.setenv("LUSTRE_API_SSH_KEY", "/root/.ssh/id_ed25519")  # key required in ssh mode
    from config import get_settings
    get_settings.cache_clear()
    # Reset module-level cache between tests
    lctl._mgs_cache.invalidate()
    yield
    get_settings.cache_clear()
    lctl._mgs_cache.invalidate()


def test_ssh_mode_uses_first_responding_host(ssh_mode):
    """First host is probed; if it responds it is cached and used."""
    def fake_check_ssh(host, user, port, key):
        return host == "10.0.0.1"

    with patch.object(lctl, "_check_mgs_via_ssh", side_effect=fake_check_ssh):
        with patch.object(lctl, "_execute_lctl_via_ssh", return_value=(
            "name: \n", ""
        )):
            result = lctl.lctl_lqa_list_all("aifs")
    assert result == []
    # Cache should now hold the active host
    from config import get_settings
    assert lctl._mgs_cache.get(get_settings().mgs_cache_ttl) == "10.0.0.1"


def test_ssh_mode_falls_over_to_second_host(ssh_mode):
    """If the first host does not respond, the second is tried."""
    call_log = []

    def fake_check_ssh(host, user, port, key):
        call_log.append(host)
        return host == "10.0.0.2"  # only second host is active

    with patch.object(lctl, "_check_mgs_via_ssh", side_effect=fake_check_ssh):
        with patch.object(lctl, "_execute_lctl_via_ssh", return_value=(
            "name: proj_A\n", ""
        )):
            result = lctl.lctl_lqa_list_all("aifs")

    assert "10.0.0.1" in call_log
    assert "10.0.0.2" in call_log
    assert result == ["proj_A"]


def test_ssh_mode_no_active_host_raises(ssh_mode):
    """MgsNotFound is raised when no host responds."""
    with patch.object(lctl, "_check_mgs_via_ssh", return_value=False):
        with pytest.raises(MgsNotFound):
            lctl.lctl_lqa_list_all("aifs")


def test_ssh_mode_cache_invalidated_on_must_be_run_on_mgs(ssh_mode):
    """
    When lctl returns 'must be run on the MGS', the cache is invalidated
    and the next host is tried.
    """
    # First call: cached host returns wrong-node error; second call succeeds
    responses = [
        ("", "This command must be run on the MGS"),   # stale cached host
        ("name: proj_A\n", ""),                         # correct host
    ]

    def fake_check_ssh(host, user, port, key):
        return True  # both hosts "respond"

    call_count = [0]

    def fake_execute(lctl_path, cmd_args, host):
        i = call_count[0]
        call_count[0] += 1
        return responses[i] if i < len(responses) else ("name: \n", "")

    with patch.object(lctl, "_check_mgs_via_ssh", side_effect=fake_check_ssh):
        with patch.object(lctl, "_execute_lctl_via_ssh", side_effect=fake_execute):
            # Seed cache with first host
            lctl._mgs_cache.set("10.0.0.1", ttl=60)
            result = lctl.lctl_lqa_list_all("aifs")

    assert result == ["proj_A"]
