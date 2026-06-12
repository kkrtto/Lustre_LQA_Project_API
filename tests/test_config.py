"""
tests/test_config.py — Unit tests for configuration loading and validation.
"""

import os

import pytest


def test_defaults():
    """Default values are applied when no env vars are set."""
    from config import Settings, LqaExecutionMode
    s = Settings()
    assert s.lfs_path == "/usr/bin/lfs"
    assert s.lctl_path == "/usr/sbin/lctl"
    assert s.lqa_mode == LqaExecutionMode.LOCAL
    assert s.mgs_hosts == []
    assert s.mgs_cache_ttl == 60
    assert s.ssh_port == 22


def test_mgs_hosts_from_comma_string(monkeypatch):
    """LUSTRE_API_MGS_HOSTS comma-string is parsed into a list."""
    monkeypatch.setenv("LUSTRE_API_MGS_HOSTS", "10.0.0.1,10.0.0.2, 10.0.0.3")
    from config import Settings
    s = Settings()
    assert s.mgs_hosts == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]


def test_mgs_hosts_from_json_array(monkeypatch):
    """LUSTRE_API_MGS_HOSTS JSON-array format is also accepted."""
    monkeypatch.setenv("LUSTRE_API_MGS_HOSTS", '["10.0.0.1","10.0.0.2"]')
    from config import Settings
    s = Settings()
    assert s.mgs_hosts == ["10.0.0.1", "10.0.0.2"]


def test_lqa_mode_ssh(monkeypatch):
    """LUSTRE_API_LQA_MODE=ssh with a key file sets enum to SSH."""
    monkeypatch.setenv("LUSTRE_API_LQA_MODE", "ssh")
    monkeypatch.setenv("LUSTRE_API_SSH_KEY", "/root/.ssh/id_ed25519")
    from config import Settings, LqaExecutionMode
    s = Settings()
    assert s.lqa_mode == LqaExecutionMode.SSH
    assert s.ssh_key == "/root/.ssh/id_ed25519"


def test_ssh_mode_without_key_raises(monkeypatch):
    """lqa_mode=ssh without ssh_key must raise a validation error."""
    monkeypatch.setenv("LUSTRE_API_LQA_MODE", "ssh")
    from config import Settings
    with pytest.raises(Exception, match="LUSTRE_API_SSH_KEY"):
        Settings()


def test_invalid_lqa_mode(monkeypatch):
    """An unrecognised LQA_MODE raises a validation error."""
    monkeypatch.setenv("LUSTRE_API_LQA_MODE", "invalid")
    from config import Settings
    with pytest.raises(Exception):
        Settings()


def test_mountpoints_from_env(monkeypatch):
    """LUSTRE_API_MOUNTPOINTS JSON dict is parsed into a dict."""
    monkeypatch.setenv("LUSTRE_API_MOUNTPOINTS", '{"aifs": "/lustre/aifs", "scratch": "/lustre/scratch"}')
    from config import Settings
    s = Settings()
    assert s.mountpoints == {"aifs": "/lustre/aifs", "scratch": "/lustre/scratch"}


def test_mountpoints_default_empty():
    """mountpoints defaults to an empty dict."""
    from config import Settings
    s = Settings()
    assert s.mountpoints == {}
