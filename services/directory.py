"""
services/directory.py — Business logic for per-directory project ID operations.

Path resolution
---------------
All public functions accept either:
  - A **relative** path such as "LQA_prj1/prj50001" (relative to the
    filesystem mountpoint).  The service prepends the mountpoint.
  - An **absolute** path that must already start with the mountpoint.

Responses always contain the resolved absolute path.
"""

import os
from typing import List, Optional

from adapters import lctl as lctl_adapter
from adapters import lfs as lfs_adapter
from config import LqaExecutionMode, get_settings
from errors import DirectoryNotFound, InvalidParameter, LqaNotFound, PermissionDenied
from schemas.directory import (
    DirProjectResponse,
    DirProjectSetRequest,
    NextProjidResponse,
    UnassignedDirItem,
    UnassignedSubdirsResponse,
)
from services.filesystem import get_mountpoint, validate_path_under_mountpoint


def _is_local() -> bool:
    return get_settings().lqa_mode == LqaExecutionMode.LOCAL


def _resolve_path(rel_or_abs: str, mnt: str) -> str:
    """
    Convert a relative or absolute path to an absolute, normalised path that
    is validated to be under *mnt*.

    Relative paths are joined to *mnt*; absolute paths are used as-is.
    ``validate_path_under_mountpoint`` raises ``InvalidParameter`` if the
    resolved path escapes the mountpoint.
    """
    if rel_or_abs.startswith("/"):
        candidate = rel_or_abs
    else:
        candidate = os.path.join(mnt, rel_or_abs)
    return validate_path_under_mountpoint(candidate, mnt)


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

def get_dir_project(fsname: str, path: str) -> DirProjectResponse:
    mnt = get_mountpoint(fsname)
    canonical = _resolve_path(path, mnt)
    if _is_local() and not os.path.isdir(canonical):
        raise DirectoryNotFound(f"Directory not found: {path}", {"path": path})
    data = lfs_adapter.lfs_get_dir_project(canonical)
    return DirProjectResponse(
        path=canonical,
        projid=data["projid"],
        inherit_flag=data["inherit_flag"],
    )


def set_dir_project(fsname: str, req: DirProjectSetRequest) -> DirProjectResponse:
    mnt = get_mountpoint(fsname)
    canonical = _resolve_path(req.path, mnt)
    if _is_local() and not os.path.isdir(canonical):
        raise DirectoryNotFound(
            f"Directory not found: {req.path}", {"path": req.path}
        )
    lfs_adapter.lfs_set_dir_project(canonical, req.projid)
    return DirProjectResponse(
        path=canonical,
        projid=req.projid,
        inherit_flag=True,  # lfs project -srp always sets inherit flag
    )


def clear_dir_project(fsname: str, path: str) -> None:
    mnt = get_mountpoint(fsname)
    canonical = _resolve_path(path, mnt)
    if _is_local() and not os.path.isdir(canonical):
        raise DirectoryNotFound(f"Directory not found: {path}", {"path": path})
    lfs_adapter.lfs_clear_dir_project(canonical)


def find_unassigned_subdirs(
    fsname: str, lqa_name: str
) -> UnassignedSubdirsResponse:
    """
    Scan the immediate subdirectories of the L1 directory whose basename
    matches *lqa_name* and return those whose Lustre project ID is 0
    (i.e. not yet assigned).

    The L1 directory path is ``<mountpoint>/<lqa_name>``.

    LQA ranges are fetched in real-time from lctl (no assignment store
    lookup).  The ``next_projid`` field in the response contains the first
    projid within the LQA ranges that is not currently used by any L2
    subdirectory.
    """
    mnt = get_mountpoint(fsname)
    l1_path = _resolve_path(lqa_name, mnt)

    if _is_local() and not os.path.isdir(l1_path):
        raise DirectoryNotFound(
            f"Directory not found: {lqa_name}", {"path": l1_path}
        )

    # Fetch LQA ranges from Lustre in real-time
    try:
        raw_ranges = lctl_adapter.lctl_lqa_list_ranges(fsname, lqa_name)
    except LqaNotFound:
        raise LqaNotFound(
            f"No LQA named \'{lqa_name}\' found in filesystem \'{fsname}\'.",
            {"fsname": fsname, "lqa_name": lqa_name},
        )
    range_pairs = [(r["start"], r["end"]) for r in raw_ranges]

    # Scan immediate subdirs
    try:
        subdir_paths = lfs_adapter.lfs_list_subdirs(l1_path)
    except PermissionDenied:
        raise PermissionDenied(
            f"Permission denied reading directory \'{l1_path}\'",
            {"path": l1_path},
        )

    # Batch-query projids with a single lfs project -d call
    projid_map = lfs_adapter.lfs_get_dir_projects_batch(subdir_paths)

    unassigned: List[UnassignedDirItem] = [
        UnassignedDirItem(path=p, inherit_flag=False)
        for p, pid in projid_map.items()
        if pid == 0
    ]

    # Find first unused projid within LQA ranges (real-time; based on batch result)
    used = {pid for pid in projid_map.values() if pid > 0}
    next_projid: Optional[int] = _first_free(range_pairs, used)

    return UnassignedSubdirsResponse(
        l1_path=l1_path,
        governing_lqa=lqa_name,
        lqa_ranges=[{"start": s, "end": e} for s, e in range_pairs],
        total_subdirs=len(subdir_paths),
        unassigned_count=len(unassigned),
        unassigned_dirs=unassigned,
        next_projid=next_projid,
    )


def find_next_projid(fsname: str, lqa_name: str) -> NextProjidResponse:
    """
    Inspect the immediate subdirectories of ``<mountpoint>/<lqa_name>``,
    collect all project IDs that are already in use **and** fall within the
    LQA ranges, and return the first projid in the ranges that is not yet
    used.

    Returns ``next_projid=None`` when every projid in every range is taken.
    """
    mnt = get_mountpoint(fsname)
    l1_path = _resolve_path(lqa_name, mnt)

    if _is_local() and not os.path.isdir(l1_path):
        raise DirectoryNotFound(
            f"Directory not found: {lqa_name}", {"path": l1_path}
        )

    try:
        raw_ranges = lctl_adapter.lctl_lqa_list_ranges(fsname, lqa_name)
    except LqaNotFound:
        raise LqaNotFound(
            f"No LQA named \'{lqa_name}\' found in filesystem \'{fsname}\'.",
            {"fsname": fsname, "lqa_name": lqa_name},
        )
    range_pairs = [(r["start"], r["end"]) for r in raw_ranges]

    def _in_ranges(pid: int) -> bool:
        return any(s <= pid <= e for s, e in range_pairs)

    subdir_paths = lfs_adapter.lfs_list_subdirs(l1_path)
    projid_map = lfs_adapter.lfs_get_dir_projects_batch(subdir_paths)

    # Only count projids that actually fall within the LQA ranges
    used = sorted(
        {pid for pid in projid_map.values() if pid > 0 and _in_ranges(pid)}
    )
    next_projid = _first_free(range_pairs, set(used))

    return NextProjidResponse(
        lqa_name=lqa_name,
        fsname=fsname,
        lqa_ranges=[{"start": s, "end": e} for s, e in range_pairs],
        used_projids=used,
        next_projid=next_projid,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _first_free(
    range_pairs: List[tuple], used: set
) -> Optional[int]:
    """Return the first projid within *range_pairs* that is not in *used*."""
    for start, end in range_pairs:
        for projid in range(start, end + 1):
            if projid not in used:
                return projid
    return None
