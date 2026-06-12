from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class LqaRangeRecord(BaseModel):
    start: int
    end: int


class AssignmentRecord(BaseModel):
    path: str
    fsname: str
    projid: int  # 0 for tier-1 (L1) directories which carry no project ID
    parent_path: str
    parent_projid: int
    # Name of the LQA that governs this directory (or its L1 ancestor).
    # None for tier=0 (standalone project).
    governing_lqa: Optional[str] = None
    # Snapshot of the governing LQA's ranges at assignment time.
    # Populated for tier=1 directories; kept in sync when ranges change.
    # None for tier=2 and tier=0.
    lqa_ranges: Optional[List[LqaRangeRecord]] = None
    # For tier=2: path of the L1 ancestor directory.
    l1_parent_path: Optional[str] = None
    # 0 = standalone project (no LQA association)
    # 1 = L1 directory (basename == LQA name, projid ∈ LQA ranges)
    # 2 = L2 directory (parent is L1, projid ∈ parent LQA ranges)
    tier: int
    created_at: datetime
    updated_at: datetime


class AssignmentListResponse(BaseModel):
    total: int
    items: List[AssignmentRecord]


class L2ChildItem(BaseModel):
    path: str
    projid: int
    created_at: datetime
    updated_at: datetime


class L1SummaryItem(BaseModel):
    path: str
    fsname: str
    projid: int
    governing_lqa: str
    lqa_ranges: List[LqaRangeRecord]
    l2_count: int
    created_at: datetime
    updated_at: datetime


class L1SummaryResponse(BaseModel):
    total: int
    items: List[L1SummaryItem]


class L1DetailResponse(BaseModel):
    path: str
    fsname: str
    projid: int
    governing_lqa: str
    lqa_ranges: List[LqaRangeRecord]
    children: List[L2ChildItem]
    created_at: datetime
    updated_at: datetime
