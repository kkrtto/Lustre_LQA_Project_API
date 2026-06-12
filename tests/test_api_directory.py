"""
tests/test_api_directory.py — Integration tests for the Directory Project API.

Path handling: both relative paths (e.g. "proj_A/user1") and absolute paths
are accepted by the API.  Responses always contain absolute paths.
"""

from unittest.mock import patch

import pytest

from tests.conftest import (
    FSNAME,
    L1_PATH,
    L2_PATH,
    LQA_NAME,
    LQA_RANGE_END,
    LQA_RANGE_START,
    MOUNTPOINT,
    PROJID_L2,
    make_run_result,
)

BASE = f"/api/v1/filesystems/{FSNAME}/directories"

# Relative path forms used in the new tests
REL_L1 = LQA_NAME           # "proj_A"
REL_L2 = f"{LQA_NAME}/user1"  # "proj_A/user1"


# ---------------------------------------------------------------------------
# GET /project
# ---------------------------------------------------------------------------

def test_get_dir_project_relative_path(client):
    """Relative path is resolved to absolute; returns projid and inherit_flag."""
    lfs_output = f"20001 P {L1_PATH}\n"
    with patch("subprocess.run", return_value=make_run_result(stdout=lfs_output)):
        with patch("os.path.isdir", return_value=True):
            resp = client.get(f"{BASE}/project?path={REL_L1}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["projid"] == 20001
    assert data["inherit_flag"] is True
    assert data["path"] == L1_PATH  # response contains absolute path


def test_get_dir_project_absolute_path(client):
    """Absolute path is accepted unchanged."""
    lfs_output = f"20001 P {L1_PATH}\n"
    with patch("subprocess.run", return_value=make_run_result(stdout=lfs_output)):
        with patch("os.path.isdir", return_value=True):
            resp = client.get(f"{BASE}/project?path={L1_PATH}")
    assert resp.status_code == 200
    assert resp.json()["projid"] == 20001


def test_get_dir_project_not_a_directory(client):
    with patch("subprocess.run", return_value=make_run_result(stdout="")):
        with patch("os.path.isdir", return_value=False):
            resp = client.get(f"{BASE}/project?path={REL_L1}")
    assert resp.status_code == 404
    assert resp.json()["code"] == "DIRECTORY_NOT_FOUND"


def test_get_dir_project_path_outside_mountpoint(client):
    # Absolute path that escapes the mountpoint
    resp = client.get(f"{BASE}/project?path=/tmp/evil")
    assert resp.status_code == 400
    assert resp.json()["code"] == "PATH_NOT_UNDER_MOUNTPOINT"


# ---------------------------------------------------------------------------
# PUT /project
# ---------------------------------------------------------------------------

def test_set_dir_project_relative_path(client):
    """Relative path is accepted; response has absolute path."""
    lfs_output = f"0 - {L2_PATH}\n"
    with patch("subprocess.run", return_value=make_run_result(stdout=lfs_output)):
        with patch("os.path.isdir", return_value=True):
            resp = client.put(f"{BASE}/project", json={
                "path": REL_L2, "projid": PROJID_L2,
            })
    assert resp.status_code == 200
    data = resp.json()
    assert data["projid"] == PROJID_L2
    assert data["inherit_flag"] is True
    assert data["path"] == L2_PATH


def test_set_dir_project_absolute_path(client):
    """Absolute path is accepted."""
    lfs_output = f"0 - {L2_PATH}\n"
    with patch("subprocess.run", return_value=make_run_result(stdout=lfs_output)):
        with patch("os.path.isdir", return_value=True):
            resp = client.put(f"{BASE}/project", json={
                "path": L2_PATH, "projid": PROJID_L2,
            })
    assert resp.status_code == 200
    assert resp.json()["projid"] == PROJID_L2


def test_set_dir_project_traversal_rejected(client):
    """Path with \'..' is rejected at schema validation."""
    resp = client.put(f"{BASE}/project", json={
        "path": "proj_A/../../../etc/passwd", "projid": 1,
    })
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /project
# ---------------------------------------------------------------------------

def test_clear_dir_project_relative_path(client):
    with patch("subprocess.run", return_value=make_run_result(stdout="")):
        with patch("os.path.isdir", return_value=True):
            resp = client.delete(f"{BASE}/project?path={REL_L2}")
    assert resp.status_code == 204


def test_clear_dir_project_absolute_path(client):
    with patch("subprocess.run", return_value=make_run_result(stdout="")):
        with patch("os.path.isdir", return_value=True):
            resp = client.delete(f"{BASE}/project?path={L2_PATH}")
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# GET /unassigned  (lqa= parameter, real-time scan)
# ---------------------------------------------------------------------------

def test_get_unassigned_subdirs_ok(client, tmp_path):
    """
    L1 dir has 3 subdirs:
      user1 — projid 20001 (already assigned, within LQA range)
      user2 — projid 0     (unassigned)
      user3 — projid 0     (unassigned)
    Expected: unassigned_count=2, next_projid=20002
    """
    # Build a real tmpdir tree so lfs_list_subdirs (os.scandir path) works
    l1_real = tmp_path / LQA_NAME
    l1_real.mkdir()
    (l1_real / "user1").mkdir()
    (l1_real / "user2").mkdir()
    (l1_real / "user3").mkdir()
    l1_str = str(l1_real)
    u1 = str(l1_real / "user1")
    u2 = str(l1_real / "user2")
    u3 = str(l1_real / "user3")

    # Fake mountpoint to tmp_path
    import builtins, io
    real_open = builtins.open

    def fake_open(path, *args, **kwargs):
        if path == "/proc/mounts":
            return io.StringIO(
                f"192.168.1.1@o2ib:/{FSNAME} {str(tmp_path)} lustre rw 0 0\n"
            )
        return real_open(path, *args, **kwargs)

    # lfs_get_dir_projects_batch returns a dict {path: projid}
    fake_batch = {u1: 20001, u2: 0, u3: 0}

    with patch("builtins.open", fake_open):
        with patch("adapters.lctl.lctl_lqa_list_ranges",
                   return_value=[{"start": LQA_RANGE_START, "end": LQA_RANGE_END}]):
            with patch("adapters.lfs.lfs_list_subdirs", return_value=[u1, u2, u3]):
                with patch("adapters.lfs.lfs_get_dir_projects_batch",
                           return_value=fake_batch):
                    resp = client.get(f"{BASE}/unassigned?lqa={LQA_NAME}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["governing_lqa"] == LQA_NAME
    assert data["total_subdirs"] == 3
    assert data["unassigned_count"] == 2
    assert data["next_projid"] == 20002
    unassigned_paths = {item["path"] for item in data["unassigned_dirs"]}
    assert u2 in unassigned_paths
    assert u3 in unassigned_paths


def test_get_unassigned_lqa_not_found(client):
    """Returns 404 when the LQA does not exist."""
    from errors import LqaNotFound
    with patch("os.path.isdir", return_value=True):
        with patch("adapters.lctl.lctl_lqa_list_ranges",
                   side_effect=LqaNotFound("no such lqa")):
            resp = client.get(f"{BASE}/unassigned?lqa=nonexistent")
    assert resp.status_code == 404
    assert resp.json()["code"] == "LQA_NOT_FOUND"


# ---------------------------------------------------------------------------
# GET /next-projid
# ---------------------------------------------------------------------------

def test_get_next_projid_ok(client, tmp_path):
    """
    L2 subdirs: user1=20001, user2=20002 — next should be 20003.
    """
    l1_real = tmp_path / LQA_NAME
    l1_real.mkdir()
    u1 = str(l1_real / "user1")
    u2 = str(l1_real / "user2")

    import builtins, io
    real_open = builtins.open

    def fake_open(path, *args, **kwargs):
        if path == "/proc/mounts":
            return io.StringIO(
                f"192.168.1.1@o2ib:/{FSNAME} {str(tmp_path)} lustre rw 0 0\n"
            )
        return real_open(path, *args, **kwargs)

    fake_batch = {u1: 20001, u2: 20002}

    with patch("builtins.open", fake_open):
        with patch("adapters.lctl.lctl_lqa_list_ranges",
                   return_value=[{"start": LQA_RANGE_START, "end": LQA_RANGE_END}]):
            with patch("adapters.lfs.lfs_list_subdirs", return_value=[u1, u2]):
                with patch("adapters.lfs.lfs_get_dir_projects_batch",
                           return_value=fake_batch):
                    resp = client.get(f"{BASE}/next-projid?lqa={LQA_NAME}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["lqa_name"] == LQA_NAME
    assert data["next_projid"] == 20003
    assert 20001 in data["used_projids"]
    assert 20002 in data["used_projids"]


def test_get_next_projid_all_taken(client, tmp_path):
    """Returns next_projid=null when every projid in the range is used."""
    l1_real = tmp_path / LQA_NAME
    l1_real.mkdir()

    # Use a tiny range 20001-20002 and fill both
    u1 = str(l1_real / "user1")
    u2 = str(l1_real / "user2")

    import builtins, io
    real_open = builtins.open

    def fake_open(path, *args, **kwargs):
        if path == "/proc/mounts":
            return io.StringIO(
                f"192.168.1.1@o2ib:/{FSNAME} {str(tmp_path)} lustre rw 0 0\n"
            )
        return real_open(path, *args, **kwargs)

    fake_batch = {u1: 20001, u2: 20002}

    with patch("builtins.open", fake_open):
        with patch("adapters.lctl.lctl_lqa_list_ranges",
                   return_value=[{"start": 20001, "end": 20002}]):
            with patch("adapters.lfs.lfs_list_subdirs", return_value=[u1, u2]):
                with patch("adapters.lfs.lfs_get_dir_projects_batch",
                           return_value=fake_batch):
                    resp = client.get(f"{BASE}/next-projid?lqa={LQA_NAME}")

    assert resp.status_code == 200
    assert resp.json()["next_projid"] is None


def test_get_next_projid_lqa_not_found(client):
    from errors import LqaNotFound
    with patch("os.path.isdir", return_value=True):
        with patch("adapters.lctl.lctl_lqa_list_ranges",
                   side_effect=LqaNotFound("no such lqa")):
            resp = client.get(f"{BASE}/next-projid?lqa=ghost")
    assert resp.status_code == 404
    assert resp.json()["code"] == "LQA_NOT_FOUND"
