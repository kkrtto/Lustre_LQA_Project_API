"""
schemas/directory.py — Pydantic models for directory project operations.

Paths in requests may be:
  - Relative: "LQA_prj1/prj50001"  (relative to the filesystem mountpoint)
  - Absolute: "/lustre/aifs/client/LQA_prj1/prj50001"

The service layer resolves relative paths to absolute before calling lfs.
Responses always return absolute paths.
"""

import re
from typing import List, Optional

from pydantic import BaseModel, field_validator


_PATH_TRAVERSAL_RE = re.compile(r"(^|/)\.\.(/|$)")


class DirProjectSetRequest(BaseModel):
    path: str   # relative (e.g. "LQA_prj1/prj50001") or absolute
    projid: int

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("path must not be empty")
        if _PATH_TRAVERSAL_RE.search(v):
            raise ValueError("path must not contain \'..' components")
        return v

    @field_validator("projid")
    @classmethod
    def validate_projid(cls, v: int) -> int:
        if v < 1:
            raise ValueError("projid must be >= 1 (0 is reserved for root)")
        if v > 0xFFFFFFFF:
            raise ValueError("projid must be <= 2^32-1")
        return v


class DirProjectResponse(BaseModel):
    path: str        # absolute path (resolved by service)
    projid: int
    inherit_flag: bool  # True when the PROJID_INHERIT flag (P) is set


class UnassignedDirItem(BaseModel):
    """A subdirectory that has no project ID set (projid == 0)."""
    path: str
    inherit_flag: bool


class UnassignedSubdirsResponse(BaseModel):
    """
    Result of scanning an L1 (LQA container) directory for subdirectories
    that have not had a project ID assigned yet (projid == 0).
    """
    l1_path: str
    governing_lqa: str
    lqa_ranges: List[dict]
    total_subdirs: int
    unassigned_count: int
    unassigned_dirs: List[UnassignedDirItem]
    next_projid: Optional[int] = None


class NextProjidResponse(BaseModel):
    """
    Predicted next available project ID within the LQA ranges of a given L1 directory.

    ``used_projids`` lists every projid currently assigned to an immediate subdir of
    the L1 directory that falls within the LQA\'s ranges.  ``next_projid`` is the
    first projid in the ranges not in ``used_projids``, or None if all are taken.
    """
    lqa_name: str
    fsname: str
    lqa_ranges: List[dict]
    used_projids: List[int]
    next_projid: Optional[int] = None
