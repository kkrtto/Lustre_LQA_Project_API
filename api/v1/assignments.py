"""
api/v1/assignments.py — Routes for querying the assignment store.

Route ordering is significant — more specific paths must come before generic
ones to avoid FastAPI resolution conflicts:
  1. GET /                  — filtered list
  2. GET /summary/l1        — L1 summary view
  3. GET /l1                — L1 detail (with children)
  4. GET /by-path           — single record lookup

Paths are passed as query parameters (not URL path segments) to avoid
percent-encoding issues with forward slashes.
"""

from typing import List, Optional

from fastapi import APIRouter, Query

from schemas.assignment import (
    AssignmentListResponse,
    AssignmentRecord,
    L1DetailResponse,
    L1SummaryItem,
    L1SummaryResponse,
    L2ChildItem,
    LqaRangeRecord,
)
from store.assignment_store import get_store
from errors import LqaNotFound

router = APIRouter()


# ---------------------------------------------------------------------------
# 1. Filtered list of all assignment records
# ---------------------------------------------------------------------------

@router.get("", response_model=AssignmentListResponse)
async def list_assignments(
    fsname: Optional[str] = Query(default=None),
    tier: Optional[int] = Query(default=None),
    lqa: Optional[str] = Query(default=None, description="Filter by governing LQA name"),
    path_prefix: Optional[str] = Query(default=None),
    parent_path: Optional[str] = Query(default=None),
    l1_parent_path: Optional[str] = Query(default=None),
) -> AssignmentListResponse:
    """List assignment records with optional filters."""
    store = get_store()
    items = store.filter(
        fsname=fsname,
        tier=tier,
        governing_lqa=lqa,
        path_prefix=path_prefix,
        parent_path=parent_path,
        l1_parent_path=l1_parent_path,
    )
    return AssignmentListResponse(total=len(items), items=items)


# ---------------------------------------------------------------------------
# 2. L1 summary (tier-1 records with l2_count)
# ---------------------------------------------------------------------------

@router.get("/summary/l1", response_model=L1SummaryResponse)
async def l1_summary(
    fsname: Optional[str] = Query(default=None),
    lqa: Optional[str] = Query(default=None, description="Filter by LQA name"),
) -> L1SummaryResponse:
    """
    Return a summary of all tier-1 (L1) directories: path, fsname, projid,
    governing LQA, LQA ranges snapshot, and count of tier-2 children.
    """
    store = get_store()
    l1_records = store.filter(fsname=fsname, tier=1, governing_lqa=lqa)
    items: List[L1SummaryItem] = []
    for rec in l1_records:
        l2_children = store.get_l2_children(rec.path)
        items.append(L1SummaryItem(
            path=rec.path,
            fsname=rec.fsname,
            projid=rec.projid,
            governing_lqa=rec.governing_lqa or "",
            lqa_ranges=rec.lqa_ranges or [],
            l2_count=len(l2_children),
            created_at=rec.created_at,
            updated_at=rec.updated_at,
        ))
    return L1SummaryResponse(total=len(items), items=items)


# ---------------------------------------------------------------------------
# 3. L1 detail — single L1 record with its L2 children list
# ---------------------------------------------------------------------------

@router.get("/l1", response_model=L1DetailResponse)
async def l1_detail(
    path: str = Query(..., description="Absolute path of the L1 directory"),
) -> L1DetailResponse:
    """
    Return details for a specific L1 directory including all its tier-2 children.
    """
    store = get_store()
    rec = store.get(path)
    if rec is None or rec.tier != 1:
        raise LqaNotFound(
            f"No tier-1 assignment found for path '{path}'",
            {"path": path},
        )
    children = store.get_l2_children(path)
    return L1DetailResponse(
        path=rec.path,
        fsname=rec.fsname,
        projid=rec.projid,
        governing_lqa=rec.governing_lqa or "",
        lqa_ranges=rec.lqa_ranges or [],
        children=[
            L2ChildItem(
                path=c.path,
                projid=c.projid,
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
            for c in children
        ],
        created_at=rec.created_at,
        updated_at=rec.updated_at,
    )


# ---------------------------------------------------------------------------
# 4. Single record lookup by path
# ---------------------------------------------------------------------------

@router.get("/by-path", response_model=AssignmentRecord)
async def get_by_path(
    path: str = Query(..., description="Absolute path of the directory"),
) -> AssignmentRecord:
    """Look up the assignment record for a specific directory path."""
    store = get_store()
    rec = store.get(path)
    if rec is None:
        raise LqaNotFound(
            f"No assignment record found for path '{path}'",
            {"path": path},
        )
    return rec
