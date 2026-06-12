"""
tests/test_api_project_quota.py — Integration tests for the Project Quota API.

Uses FastAPI TestClient with /proc/mounts and subprocess.run patched so no
real Lustre installation is needed.
"""

from unittest.mock import patch, MagicMock

import pytest

from tests.conftest import FSNAME, MOUNTPOINT, quota_line, default_quota_line, make_run_result

BASE = f"/api/v1/filesystems/{FSNAME}/quotas/projects"


@pytest.fixture()
def mock_lfs(monkeypatch):
    """Patch subprocess.run for the lfs adapter."""
    mock = MagicMock(return_value=make_run_result(stdout=quota_line()))
    with patch("subprocess.run", mock):
        yield mock


# ── GET /{projid} ─────────────────────────────────────────────────────────────

def test_get_project_quota_ok(client, mock_lfs):
    resp = client.get(f"{BASE}/20001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["projid"] == 20001
    assert data["block_usage"] == "1k"         # human-readable string
    assert data["block_hardlimit"] == "10G"    # human-readable string


def test_get_project_quota_filesystem_not_mounted(client, mock_lfs):
    resp = client.get(f"/api/v1/filesystems/nonexistent/quotas/projects/20001")
    assert resp.status_code == 404
    assert resp.json()["code"] == "FILESYSTEM_NOT_FOUND"


# ── PUT /{projid} ─────────────────────────────────────────────────────────────

def test_set_project_quota_ok(client, mock_lfs):
    resp = client.put(f"{BASE}/20001", json={"block_hardlimit": 10 * 1024 ** 3})
    assert resp.status_code == 200


def test_set_project_quota_invalid_limits(client, mock_lfs):
    resp = client.put(f"{BASE}/20001", json={
        "block_softlimit": 2_000_000_000,
        "block_hardlimit": 1_000_000_000,
    })
    assert resp.status_code == 422  # Pydantic validation error


# ── DELETE /{projid} ──────────────────────────────────────────────────────────

def test_delete_project_quota(client, mock_lfs):
    resp = client.delete(f"{BASE}/20001")
    assert resp.status_code == 204


# ── GET /default ──────────────────────────────────────────────────────────────

def test_get_default_quota(client):
    with patch("subprocess.run", return_value=make_run_result(stdout=default_quota_line())):
        resp = client.get(f"{BASE}/default")
    assert resp.status_code == 200
    assert resp.json()["is_default"] is True


# ── GET /grace ────────────────────────────────────────────────────────────────

def test_get_grace(client):
    grace_output = "Block grace time: 7days; Inode grace time: 7days;\n"
    with patch("subprocess.run", return_value=make_run_result(stdout=grace_output)):
        resp = client.get(f"{BASE}/grace")
    assert resp.status_code == 200
    data = resp.json()
    assert data["block_grace"] == "7days"


def test_set_grace(client):
    with patch("subprocess.run", return_value=make_run_result(
        stdout="Block grace time: 14days; Inode grace time: 14days;\n"
    )):
        resp = client.put(f"{BASE}/grace", json={"block_grace": "14days"})
    assert resp.status_code == 200


# ── POST /{projid}/reset ──────────────────────────────────────────────────────

def test_reset_quota(client, mock_lfs):
    resp = client.post(f"{BASE}/20001/reset")
    assert resp.status_code == 200
    # Verify the reset call uses explicit 0 limits, not -r
    calls = [" ".join(c[0][0]) for c in mock_lfs.call_args_list]
    reset_call = next((c for c in calls if "setquota" in c and "-b" in c and "-B" in c), None)
    assert reset_call is not None
    assert "-r" not in reset_call
