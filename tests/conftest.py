"""
tests/conftest.py — Shared fixtures for the Lustre Project Quota API test suite.

All tests run without a real Lustre mount.  The strategy is:
  • Patch subprocess.run so lfs/lctl calls never reach the OS.
  • Patch /proc/mounts to return a synthetic Lustre entry.
  • Use a tmp-file-backed AssignmentStore so each test gets a clean store.
  • Use FastAPI's TestClient for HTTP-layer tests.

The get_settings() lru_cache is cleared between tests so environment
overrides in individual tests take effect cleanly.
"""

import json
import os
import tempfile
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ── Constants used across tests ───────────────────────────────────────────────
FSNAME = "aifs"
MOUNTPOINT = "/lustre/aifs"
LQA_NAME = "proj_A"
L1_PATH = f"{MOUNTPOINT}/{LQA_NAME}"
L2_PATH = f"{L1_PATH}/user1"
PROJID_L1 = 20001
PROJID_L2 = 20002
LQA_RANGE_START = 20001
LQA_RANGE_END = 30000

FAKE_MOUNTS = (
    f"192.168.1.1@o2ib:/{FSNAME} {MOUNTPOINT} lustre rw 0 0\n"
    "sysfs /sys sysfs rw 0 0\n"
)

# Standard lfs quota -h output for a single project quota (9 data columns)
# Columns: filesystem  block_used  bsoft  bhard  bgrace  inodes  isoft  ihard  igrace
def quota_line(
    mnt=MOUNTPOINT,
    block_used="1k",
    b_soft="0k",
    b_hard="10G",
    b_grace="-",
    inodes=10,
    i_soft=0,
    i_hard=1000000,
    i_grace="-",
):
    return (
        f"{mnt}  {block_used}  {b_soft}  {b_hard}  {b_grace}  "
        f"{inodes}  {i_soft}  {i_hard}  {i_grace}\n"
    )


# Default project quota -h output (7 data columns, no usage column)
# Columns: filesystem  bsoft  bhard  bgrace  isoft  ihard  igrace
def default_quota_line(
    mnt=MOUNTPOINT,
    b_soft="0k",
    b_hard="1638P",
    b_grace="-",
    i_soft=0,
    i_hard=0,
    i_grace="-",
):
    return (
        f"{mnt}  {b_soft}  {b_hard}  {b_grace}  "
        f"{i_soft}  {i_hard}  {i_grace}\n"
    )


def make_run_result(stdout: str = "", stderr: str = "", returncode: int = 0):
    r = MagicMock()
    r.stdout = stdout.encode()
    r.stderr = stderr.encode()
    r.returncode = returncode
    return r


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_settings_cache(monkeypatch):
    """Clear the get_settings() lru_cache before every test and ensure
    settings from a local .env file do not leak into tests."""
    from config import get_settings
    get_settings.cache_clear()
    # Redirect the .env file to /dev/null so any local .env in the repo root
    # does not inject values into tests.  Individual tests can still override
    # individual settings with their own monkeypatch.setenv calls.
    monkeypatch.setenv("LUSTRE_API__ENV_FILE", "/dev/null")
    yield
    get_settings.cache_clear()


@pytest.fixture()
def tmp_store_path(tmp_path) -> str:
    return str(tmp_path / "assignments.json")


@pytest.fixture(autouse=True)
def patch_store(tmp_store_path, monkeypatch):
    """
    Override the assignment store singleton so every test gets a fresh,
    isolated store backed by a temp file.
    """
    import store.assignment_store as sa
    sa.get_store.cache_clear()
    monkeypatch.setenv("LUSTRE_API_ASSIGNMENT_STORE_PATH", tmp_store_path)
    yield
    sa.get_store.cache_clear()


@pytest.fixture()
def patch_proc_mounts(monkeypatch):
    """Patch open('/proc/mounts') to return a fake Lustre entry."""
    import builtins
    real_open = builtins.open

    def fake_open(path, *args, **kwargs):
        if path == "/proc/mounts":
            import io
            return io.StringIO(FAKE_MOUNTS)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)


@pytest.fixture()
def client(patch_proc_mounts) -> Generator:
    """HTTP test client with /proc/mounts patched."""
    from main import create_app
    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
