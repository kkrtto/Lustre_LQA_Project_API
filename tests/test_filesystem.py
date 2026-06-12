"""
tests/test_filesystem.py — Unit tests for /proc/mounts parsing and validation.
"""

import builtins
import io

import pytest

from errors import FilesystemNotFound, PathNotUnderMountpoint


MOUNTS_CONTENT = (
    "192.168.1.1@o2ib:/aifs /lustre/aifs lustre rw 0 0\n"
    "10.0.0.1:/scratch /lustre/scratch lustre rw 0 0\n"
    "sysfs /sys sysfs rw 0 0\n"
    "proc /proc proc rw 0 0\n"
)

MOUNTS_LOCAL_FORMAT = (
    # loopback / local test setup (fsname-MDT format)
    "aifs-MDT0000 /lustre/aifs lustre rw 0 0\n"
)


def _patch_mounts(monkeypatch, content: str):
    real_open = builtins.open

    def fake_open(path, *args, **kwargs):
        if path == "/proc/mounts":
            return io.StringIO(content)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)


def test_list_filesystems_nid_format(monkeypatch):
    _patch_mounts(monkeypatch, MOUNTS_CONTENT)
    from services.filesystem import list_filesystems
    result = list_filesystems()
    assert result == {"aifs": "/lustre/aifs", "scratch": "/lustre/scratch"}


def test_list_filesystems_local_format(monkeypatch):
    _patch_mounts(monkeypatch, MOUNTS_LOCAL_FORMAT)
    from services.filesystem import list_filesystems
    result = list_filesystems()
    assert result == {"aifs": "/lustre/aifs"}


def test_list_filesystems_empty(monkeypatch):
    _patch_mounts(monkeypatch, "sysfs /sys sysfs rw 0 0\n")
    from services.filesystem import list_filesystems
    assert list_filesystems() == {}


def test_get_mountpoint_found(monkeypatch):
    _patch_mounts(monkeypatch, MOUNTS_CONTENT)
    from services.filesystem import get_mountpoint
    assert get_mountpoint("aifs") == "/lustre/aifs"


def test_get_mountpoint_not_found(monkeypatch):
    _patch_mounts(monkeypatch, MOUNTS_CONTENT)
    from services.filesystem import get_mountpoint
    with pytest.raises(FilesystemNotFound) as exc_info:
        get_mountpoint("nonexistent")
    assert "nonexistent" in exc_info.value.message


def test_validate_path_under_mountpoint_valid(tmp_path):
    """A path inside the mountpoint resolves cleanly."""
    from services.filesystem import validate_path_under_mountpoint
    mnt = str(tmp_path)
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    result = validate_path_under_mountpoint(str(subdir), mnt)
    assert result == str(subdir)


def test_validate_path_not_under_mountpoint(tmp_path):
    """A path outside the mountpoint raises PathNotUnderMountpoint."""
    from services.filesystem import validate_path_under_mountpoint
    mnt = str(tmp_path / "mnt")
    other = str(tmp_path / "other")
    with pytest.raises(PathNotUnderMountpoint):
        validate_path_under_mountpoint(other, mnt)


def test_validate_path_prefix_attack(tmp_path):
    """Ensure '/mnt/lustre2' does not match '/mnt/lustre' as mountpoint."""
    from services.filesystem import validate_path_under_mountpoint
    (tmp_path / "lustre").mkdir()
    (tmp_path / "lustre2").mkdir()
    mnt = str(tmp_path / "lustre")
    sibling = str(tmp_path / "lustre2")
    with pytest.raises(PathNotUnderMountpoint):
        validate_path_under_mountpoint(sibling, mnt)


def test_get_mountpoint_from_config(monkeypatch):
    """An explicit mountpoints config entry is used without reading /proc/mounts."""
    monkeypatch.setenv("LUSTRE_API_MOUNTPOINTS", '{"aifs": "/lustre/aifs"}')
    from services.filesystem import get_mountpoint
    # No /proc/mounts patch — should succeed via config alone
    assert get_mountpoint("aifs") == "/lustre/aifs"


def test_get_mountpoint_config_takes_priority(monkeypatch):
    """Config mountpoint beats /proc/mounts even in local mode."""
    monkeypatch.setenv("LUSTRE_API_MOUNTPOINTS", '{"aifs": "/custom/mount"}')
    _patch_mounts(monkeypatch, MOUNTS_CONTENT)  # /proc/mounts says /lustre/aifs
    from services.filesystem import get_mountpoint
    assert get_mountpoint("aifs") == "/custom/mount"


def test_get_mountpoint_ssh_mode_requires_config(monkeypatch):
    """In ssh mode, get_mountpoint raises FilesystemNotFound if not in config."""
    monkeypatch.setenv("LUSTRE_API_LQA_MODE", "ssh")
    monkeypatch.setenv("LUSTRE_API_SSH_KEY", "/root/.ssh/id_ed25519")
    from services.filesystem import get_mountpoint
    with pytest.raises(FilesystemNotFound) as exc_info:
        get_mountpoint("aifs")
    assert "LUSTRE_API_MOUNTPOINTS" in exc_info.value.message


def test_get_mountpoint_ssh_mode_with_config(monkeypatch):
    """In ssh mode, get_mountpoint succeeds when mountpoints is configured."""
    monkeypatch.setenv("LUSTRE_API_LQA_MODE", "ssh")
    monkeypatch.setenv("LUSTRE_API_SSH_KEY", "/root/.ssh/id_ed25519")
    monkeypatch.setenv("LUSTRE_API_MOUNTPOINTS", '{"aifs": "/lustre/aifs"}')
    from services.filesystem import get_mountpoint
    assert get_mountpoint("aifs") == "/lustre/aifs"
