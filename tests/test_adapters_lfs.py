"""
tests/test_adapters_lfs.py — Unit tests for adapters/lfs.py.

All subprocess.run calls are intercepted so no real Lustre installation
is required.
"""

from unittest.mock import patch, MagicMock

import pytest

from errors import LustreCommandError, LustreTimeoutError, PermissionDenied
import adapters.lfs as lfs


def _mock_run(stdout="", stderr="", returncode=0):
    r = MagicMock()
    r.stdout = stdout.encode()
    r.stderr = stderr.encode()
    r.returncode = returncode
    return r


QUOTA_OUTPUT = (
    "Disk quotas for prj 20001 (pid 20001):\n"
    "     Filesystem  kbytes   quota   limit   grace   files   quota   limit   grace\n"
    "/lustre/aifs      1k       0k     10G       -      10        0   1000000       -\n"
)

DEFAULT_QUOTA_OUTPUT = (
    "Disk default prj quota:\n"
    "     Filesystem  bquota   blimit   bgrace   iquota   ilimit   igrace\n"
    "/lustre/aifs       0k   1638P       -       0        0        -\n"
)

GRACE_OUTPUT = (
    "Block grace time: 7days; Inode grace time: 7days;\n"
)


# ── _run error mapping ────────────────────────────────────────────────────────

def test_run_timeout():
    import subprocess
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=[], timeout=30)):
        with pytest.raises(LustreTimeoutError):
            lfs._run(["lfs", "quota"])


def test_run_permission_denied():
    with patch("subprocess.run", return_value=_mock_run(
        stderr="Permission denied", returncode=1
    )):
        with pytest.raises(PermissionDenied):
            lfs._run(["lfs", "quota"])


def test_run_generic_error():
    with patch("subprocess.run", return_value=_mock_run(
        stderr="some unknown error", returncode=1
    )):
        with pytest.raises(LustreCommandError):
            lfs._run(["lfs", "quota"])


def test_run_success():
    with patch("subprocess.run", return_value=_mock_run(stdout="ok")):
        out, err = lfs._run(["lfs", "quota"])
    assert out == "ok"


# ── Unit conversion ───────────────────────────────────────────────────────────

def test_kb_to_bytes():
    assert lfs._kb_to_bytes(1024) == 1024 * 1024


def test_bytes_to_kb_rounds_up():
    assert lfs._bytes_to_kb(1) == 1
    assert lfs._bytes_to_kb(1023) == 1
    assert lfs._bytes_to_kb(1024) == 1
    assert lfs._bytes_to_kb(1025) == 2
    assert lfs._bytes_to_kb(0) == 0


# ── Quota output parsing ──────────────────────────────────────────────────────

def test_parse_quota_row_h():
    data = lfs._parse_quota_row_h(QUOTA_OUTPUT)
    assert data["block_usage"] == "1k"        # human-readable string
    assert data["block_softlimit"] == "0k"
    assert data["block_hardlimit"] == "10G"
    assert data["inode_usage"] == 10          # still int
    assert data["block_grace"] == "-"


def test_parse_quota_row_h_bad_output():
    with pytest.raises(LustreCommandError):
        lfs._parse_quota_row_h("no matching lines here")


def test_parse_default_quota_row_h():
    data = lfs._parse_default_quota_row_h(DEFAULT_QUOTA_OUTPUT)
    assert data["block_softlimit"] == "0k"
    assert data["block_hardlimit"] == "1638P"
    assert data["block_usage"] == "0"          # default has no usage
    assert data["inode_softlimit"] == 0
    assert data["inode_hardlimit"] == 0


def test_bytes_to_human_zero():
    assert lfs.bytes_to_human(0) == "0"


def test_bytes_to_human_kb():
    assert lfs.bytes_to_human(4096) == "4k"


def test_bytes_to_human_gb():
    assert lfs.bytes_to_human(10 * 1024 ** 3) == "10G"


def test_bytes_to_human_fractional():
    # 3.023 GiB
    n = int(3.023 * 1024 ** 3)
    result = lfs.bytes_to_human(n)
    assert result.endswith("G")


# ── Grace output parsing ──────────────────────────────────────────────────────

def test_parse_grace_output():
    data = lfs._parse_grace_output(GRACE_OUTPUT)
    assert data["block_grace"] == "7days"
    assert data["inode_grace"] == "7days"


def test_parse_grace_output_missing():
    data = lfs._parse_grace_output("no grace lines")
    assert data["block_grace"] == "none"
    assert data["inode_grace"] == "none"


# ── lfs_get_project_quota ─────────────────────────────────────────────────────

def test_lfs_get_project_quota_calls_correct_command():
    with patch("subprocess.run", return_value=_mock_run(stdout=QUOTA_OUTPUT)) as mock:
        lfs.lfs_get_project_quota("/lustre/aifs", 20001)
    cmd = mock.call_args[0][0]
    assert "-h" in cmd
    assert "-p" in cmd
    assert "20001" in cmd
    assert "/lustre/aifs" in cmd



# ── lfs_set_project_quota ─────────────────────────────────────────────────────

def test_lfs_set_project_quota_byte_conversion():
    """10 GiB should be converted to 10485760 KB for the lfs command."""
    with patch("subprocess.run", return_value=_mock_run(stdout="")) as mock:
        lfs.lfs_set_project_quota(
            "/lustre/aifs", 20001,
            {"block_hardlimit": 10 * 1024 ** 3},   # 10 GiB in bytes
        )
    cmd = mock.call_args[0][0]
    bh_idx = cmd.index("-B")
    assert cmd[bh_idx + 1] == "10485760"


def test_lfs_set_project_quota_unit_string():
    """Unit strings like '100G' are passed directly to lfs without conversion."""
    with patch("subprocess.run", return_value=_mock_run(stdout="")) as mock:
        lfs.lfs_set_project_quota(
            "/lustre/aifs", 20001,
            {"block_hardlimit": "100G"},
        )
    cmd = mock.call_args[0][0]
    bh_idx = cmd.index("-B")
    assert cmd[bh_idx + 1] == "100G"


def test_lfs_set_project_quota_zero_unlimited():
    """A 0-byte limit means 'unlimited' and must pass '0' to lfs, not '1'."""
    with patch("subprocess.run", return_value=_mock_run(stdout="")) as mock:
        lfs.lfs_set_project_quota("/lustre/aifs", 20001, {"block_hardlimit": 0})
    cmd = mock.call_args[0][0]
    bh_idx = cmd.index("-B")
    assert cmd[bh_idx + 1] == "0"


# ── lfs_get_dir_project ───────────────────────────────────────────────────────

def test_lfs_get_dir_project_inherit_flag():
    output = "20001 P /lustre/aifs/proj_A\n"
    with patch("subprocess.run", return_value=_mock_run(stdout=output)):
        data = lfs.lfs_get_dir_project("/lustre/aifs/proj_A")
    assert data["projid"] == 20001
    assert data["inherit_flag"] is True


def test_lfs_get_dir_project_no_inherit():
    output = "20001 - /lustre/aifs/proj_A\n"
    with patch("subprocess.run", return_value=_mock_run(stdout=output)):
        data = lfs.lfs_get_dir_project("/lustre/aifs/proj_A")
    assert data["inherit_flag"] is False


def test_lfs_get_dir_project_unassigned():
    output = "0 - /lustre/aifs/proj_A/user1\n"
    with patch("subprocess.run", return_value=_mock_run(stdout=output)):
        data = lfs.lfs_get_dir_project("/lustre/aifs/proj_A/user1")
    assert data["projid"] == 0


def test_lfs_get_dir_project_bad_output():
    with patch("subprocess.run", return_value=_mock_run(stdout="garbage\n")):
        with pytest.raises(LustreCommandError):
            lfs.lfs_get_dir_project("/lustre/aifs/proj_A")


# ── lfs_set_dir_project ───────────────────────────────────────────────────────

def test_lfs_set_dir_project_uses_srp_flags():
    """lfs project must be called with combined -srp flags."""
    with patch("subprocess.run", return_value=_mock_run()) as mock:
        lfs.lfs_set_dir_project("/lustre/aifs/proj_A", 20001)
    cmd = mock.call_args[0][0]
    assert "-srp" in cmd
    assert "20001" in cmd


# ── lfs_iterate_project_quotas ────────────────────────────────────────────────

ITER_OUTPUT = (
    "/lustre/aifs  20001  1024  0  10485760  -  10  0  1000000  -\n"
    "/lustre/aifs  20002  2048  0  10485760  -  5   0  1000000  -\n"
)


def test_lfs_iterate_project_quotas():
    with patch("subprocess.run", return_value=_mock_run(stdout=ITER_OUTPUT)):
        result = lfs.lfs_iterate_project_quotas("/lustre/aifs")
    assert 20001 in result
    assert 20002 in result
    assert result[20001]["block_usage"] == 1024 * 1024
    assert result[20002]["inode_usage"] == 5


def test_lfs_iterate_project_quotas_empty():
    with patch("subprocess.run", return_value=_mock_run(stdout="")):
        result = lfs.lfs_iterate_project_quotas("/lustre/aifs")
    assert result == {}
