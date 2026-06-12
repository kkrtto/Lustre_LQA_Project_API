"""
tests/test_api_lqa.py — Integration tests for the LQA API.
"""

from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import FSNAME, LQA_NAME, MOUNTPOINT, make_run_result, quota_line

BASE = f"/api/v1/filesystems/{FSNAME}/lqas"


def _lctl_mock(stdout="", stderr="", returncode=0):
    return make_run_result(stdout=stdout, stderr=stderr, returncode=returncode)


# ── GET / — list all LQAs ─────────────────────────────────────────────────────

def test_list_lqas(client):
    def side_effect(cmd, **kwargs):
        arg = " ".join(cmd)
        # lqa_list --fsname F  (all) vs  lqa_list --fsname F --name N  (single)
        if "lqa_list" in arg and "--name" not in arg:
            return _lctl_mock(stdout="name: proj_A, proj_B\n")
        if "--name" in arg and "proj_A" in arg:
            return _lctl_mock(stdout="name: proj_A\nranges: 20001-30000\n")
        if "--name" in arg and "proj_B" in arg:
            return _lctl_mock(stdout="name: proj_B\nranges: 30001-40000\n")
        return _lctl_mock()

    with patch("subprocess.run", side_effect=side_effect):
        resp = client.get(BASE)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    names = {item["name"] for item in data}
    assert names == {"proj_A", "proj_B"}


def test_list_lqas_empty(client):
    with patch("subprocess.run", return_value=_lctl_mock(stdout="name: \n")):
        resp = client.get(BASE)
    assert resp.status_code == 200
    assert resp.json() == []


# ── POST / — create LQA ───────────────────────────────────────────────────────

def test_create_lqa_ok(client):
    with patch("subprocess.run", return_value=_lctl_mock()):
        resp = client.post(BASE, json={"name": "proj_A"})
    assert resp.status_code == 201
    assert resp.json()["name"] == "proj_A"


def test_create_lqa_invalid_name(client):
    resp = client.post(BASE, json={"name": "invalid name!"})
    assert resp.status_code == 422


def test_create_lqa_already_exists(client):
    with patch("subprocess.run", return_value=_lctl_mock(
        stderr="lqa already exist\n"
    )):
        resp = client.post(BASE, json={"name": "proj_A"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "LQA_ALREADY_EXISTS"


# ── GET /{name} ───────────────────────────────────────────────────────────────

def test_get_lqa_ok(client):
    with patch("subprocess.run", return_value=_lctl_mock(stdout="name: proj_A\nranges: 20001-30000\n")):
        resp = client.get(f"{BASE}/proj_A")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "proj_A"
    assert data["ranges"][0] == {"start": 20001, "end": 30000}
    assert "quota" in data  # may be None if quota not yet set


def test_get_lqa_not_found(client):
    with patch("subprocess.run", return_value=_lctl_mock(stderr="lqa not found\n")):
        resp = client.get(f"{BASE}/no_such_lqa")
    assert resp.status_code == 404


# ── DELETE /{name} ────────────────────────────────────────────────────────────

def test_destroy_lqa(client):
    with patch("subprocess.run", return_value=_lctl_mock()):
        resp = client.delete(f"{BASE}/proj_A")
    assert resp.status_code == 204


# ── POST /{name}/ranges ───────────────────────────────────────────────────────

def test_add_range_ok(client):
    """Adding a non-conflicting range returns the updated range list."""
    def side_effect(cmd, **kwargs):
        arg = " ".join(cmd)
        if "lqa_list" in arg and "--name" not in arg:
            return _lctl_mock(stdout="name: proj_A\n")
        if "lqa_list" in arg and "--name" in arg:
            return _lctl_mock(stdout="name: proj_A\nranges: 20001-30000\n")
        return _lctl_mock()

    with patch("subprocess.run", side_effect=side_effect):
        resp = client.post(f"{BASE}/proj_A/ranges", json={"start": 20001, "end": 30000})
    assert resp.status_code == 201


def test_add_range_conflict(client):
    """A range overlapping another LQA's range is rejected with 409."""
    def side_effect(cmd, **kwargs):
        arg = " ".join(cmd)
        if "lqa_list" in arg and "--name" not in arg:
            return _lctl_mock(stdout="name: proj_A, proj_B\n")
        if "proj_A" in arg and "--name" in arg:
            return _lctl_mock(stdout="name: proj_A\nranges:\n")
        if "proj_B" in arg and "--name" in arg:
            # proj_B already owns 20001-30000
            return _lctl_mock(stdout="name: proj_B\nranges: 20001-30000\n")
        return _lctl_mock()

    with patch("subprocess.run", side_effect=side_effect):
        # Try to add 25000-35000 to proj_A — overlaps proj_B's range
        resp = client.post(f"{BASE}/proj_A/ranges", json={"start": 25000, "end": 35000})
    assert resp.status_code == 409
    assert resp.json()["code"] == "RANGE_CONFLICT"


def test_add_range_invalid(client):
    """end < start is rejected by Pydantic (422)."""
    resp = client.post(f"{BASE}/proj_A/ranges", json={"start": 30000, "end": 20001})
    assert resp.status_code == 422


# ── DELETE /{name}/ranges/{range_str} ────────────────────────────────────────

def test_remove_range_ok(client):
    with patch("subprocess.run", return_value=_lctl_mock(stdout="ranges: []\n")):
        resp = client.delete(f"{BASE}/proj_A/ranges/20001-30000")
    assert resp.status_code == 200


def test_remove_range_bad_format(client):
    resp = client.delete(f"{BASE}/proj_A/ranges/invalid")
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_PARAMETER"


# ── GET /{name}/quota ─────────────────────────────────────────────────────────

def test_get_lqa_quota_granted(client):
    with patch("subprocess.run", return_value=make_run_result(stdout=quota_line())):
        resp = client.get(f"{BASE}/proj_A/quota")
    assert resp.status_code == 200
    data = resp.json()
    assert "block_granted" in data
    assert "block_grace" not in data
    assert "inode_softlimit" not in data
    assert data["actual_block_usage"] is None   # accurate_usage not requested
    assert data["usage_warning"] is None        # check_consistency not requested


def test_get_lqa_quota_accurate(client):
    # projids 20001 and 20002 both fall within LQA range 20001-30000 → both summed
    # projid 99999 is outside the range → excluded from sum
    iter_output = (
        f"/lustre/aifs  20001  1024  0  10485760  -  10  0  1000000  -\n"
        f"/lustre/aifs  20002  2048  0  10485760  -   5  0  1000000  -\n"
        f"/lustre/aifs  99999   512  0  10485760  -   1  0  1000000  -\n"
    )

    def side_effect(cmd, **kwargs):
        arg = " ".join(cmd)
        if "-a" in cmd and "-p" in cmd:
            return make_run_result(stdout=iter_output)
        if "lqa_list" in arg:
            return make_run_result(stdout="name: proj_A\nranges: 20001-30000\n")
        return make_run_result(stdout=quota_line())

    with patch("subprocess.run", side_effect=side_effect):
        resp = client.get(f"{BASE}/proj_A/quota?accurate_usage=true")
    assert resp.status_code == 200
    data = resp.json()
    # sum of 20001 + 20002 only (99999 excluded): (1024+2048)*1024 = 3M
    assert data["actual_block_usage"] == "3M"
    assert data["usage_warning"] is None         # check_consistency not requested
    assert "actual_usage_projids" not in data


def test_get_lqa_quota_consistency_warning(client):
    """check_consistency=true triggers the L2 scan; reports warning when a subdir has wrong projid."""
    import adapters.lfs as lfs_mod

    def side_effect(cmd, **kwargs):
        arg = " ".join(cmd)
        if "-a" in cmd and "-p" in cmd:
            return make_run_result(stdout="")   # no iterate quotas needed
        if "lqa_list" in arg:
            return make_run_result(stdout="name: proj_A\nranges: 20001-30000\n")
        return make_run_result(stdout=quota_line())

    # Patch the adapter-level functions that do filesystem scanning
    with patch("subprocess.run", side_effect=side_effect), \
         patch.object(lfs_mod, "lfs_list_subdirs", side_effect=[
             [f"{MOUNTPOINT}/proj_A"],
             [f"{MOUNTPOINT}/proj_A/user1", f"{MOUNTPOINT}/proj_A/user2"],
         ]), \
         patch.object(lfs_mod, "lfs_get_dir_projects_batch", return_value={
             f"{MOUNTPOINT}/proj_A/user1": 20001,
             f"{MOUNTPOINT}/proj_A/user2": 99999,
         }):
        resp = client.get(f"{BASE}/proj_A/quota?check_consistency=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data["actual_block_usage"] is None
    assert data["usage_warning"] is not None
    assert data["misconfigured_dirs"] == [f"{MOUNTPOINT}/proj_A/user2"]
