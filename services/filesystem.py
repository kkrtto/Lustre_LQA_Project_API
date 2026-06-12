"""
services/filesystem.py — Lustre filesystem discovery via /proc/mounts.

Parses mounted Lustre filesystems to build a fsname→mountpoint map.
Handles two common device string formats:
  • MGS/client: <addr>:/<fsname>    e.g. 192.168.1.1@o2ib:/lustre
  • Local test: <fsname>-MDT...     e.g. lustre-MDT0000 (loopback setups)

Mountpoint resolution priority:
  1. Explicit LUSTRE_API_MOUNTPOINTS config (always checked first).
  2. /proc/mounts auto-discovery (LOCAL mode only).
In SSH mode, /proc/mounts on the API node does not list the remote Lustre
filesystem, so an explicit config entry is required.
"""

import os
from typing import Dict

from config import LqaExecutionMode, get_settings
from errors import FilesystemNotFound, PathNotUnderMountpoint

_PROC_MOUNTS = "/proc/mounts"


def _parse_mounts() -> Dict[str, str]:
    """
    Return {fsname: mountpoint} for all currently mounted Lustre filesystems.
    """
    result: Dict[str, str] = {}
    try:
        with open(_PROC_MOUNTS, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                device, mountpoint, fstype = parts[0], parts[1], parts[2]
                if fstype != "lustre":
                    continue
                # Format 1: addr:/fsname or addr@nid:/fsname
                if ":/" in device:
                    fsname = device.rsplit(":/", 1)[-1].split("/")[0]
                # Format 2: fsname-MDT0000 or fsname-OST0000 (loop/local setups)
                elif "-MDT" in device or "-OST" in device:
                    fsname = device.split("-")[0]
                else:
                    continue
                if fsname:
                    result[fsname] = mountpoint
    except FileNotFoundError:
        pass
    return result


def get_mountpoint(fsname: str) -> str:
    """
    Return the mountpoint for the given Lustre filesystem name.

    Resolution order:
      1. LUSTRE_API_MOUNTPOINTS config dict  (always checked first)
      2. /proc/mounts auto-discovery         (LOCAL mode only)

    Raises FilesystemNotFound if the filesystem cannot be resolved.
    """
    settings = get_settings()

    # 1. Explicit config wins in both modes.
    if fsname in settings.mountpoints:
        return settings.mountpoints[fsname]

    # 2. In SSH mode we cannot discover mounts from the local /proc/mounts.
    if settings.lqa_mode == LqaExecutionMode.SSH:
        raise FilesystemNotFound(
            f"Lustre filesystem '{fsname}' not found in LUSTRE_API_MOUNTPOINTS. "
            "Set LUSTRE_API_MOUNTPOINTS='{\"<fsname>\": \"<mountpoint>\"}' "
            "when running in ssh mode.",
            {"fsname": fsname},
        )

    # 3. LOCAL mode fallback: discover from /proc/mounts.
    mounts = _parse_mounts()
    mnt = mounts.get(fsname)
    if mnt is None:
        raise FilesystemNotFound(
            f"Lustre filesystem '{fsname}' is not mounted on this node",
            {"fsname": fsname, "mounted": list(mounts.keys())},
        )
    return mnt


def list_filesystems() -> Dict[str, str]:
    """Return all mounted Lustre filesystems as {fsname: mountpoint}."""
    return _parse_mounts()


def validate_path_under_mountpoint(path: str, mountpoint: str) -> str:
    """
    Verify that *path* resides under *mountpoint* and return the canonical path.

    In LOCAL mode symlinks are resolved via os.path.realpath so that a
    symlink pointing outside the mountpoint is rejected.

    In SSH mode the path exists on a remote node; symlink resolution is not
    possible locally.  os.path.normpath (which resolves '..') is used instead.
    This is sufficient to prevent path-traversal via '..' components; the
    remote lfs command itself validates existence.

    Raises PathNotUnderMountpoint if the path escapes the mountpoint.
    """
    settings = get_settings()
    if settings.lqa_mode == LqaExecutionMode.SSH:
        canonical = os.path.normpath(os.path.abspath(path))
        mp = os.path.normpath(mountpoint)
    else:
        canonical = os.path.realpath(path)
        mp = os.path.realpath(mountpoint)
    # Ensure mp ends with separator so "/mnt/lustre2" doesn't match "/mnt/lustre"
    if not mp.endswith(os.sep):
        mp = mp + os.sep
    if not (canonical + os.sep).startswith(mp):
        raise PathNotUnderMountpoint(
            f"Path '{path}' is not under mountpoint '{mountpoint}'",
            {"path": path, "mountpoint": mountpoint, "canonical": canonical},
        )
    return canonical
