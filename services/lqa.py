"""
services/lqa.py — Business logic for LQA (Lustre Quota Aggregation) management.

Each LQA is a named container on the QMT that aggregates quota usage from a
set of project IDs. This service bridges the lctl adapter (for LQA lifecycle
and range management) and the lfs adapter (for quota values and accurate
usage calculation).

Accurate LQA usage: lfs quota -P --lqa returns grant-based values which can
exceed actual disk usage. When accurate_usage=True is requested, we call
lfs_iterate_project_quotas() to get all per-project actual usage, then filter
to projids known to belong to this LQA (via the assignment store), and sum.
"""

from typing import Dict, List, Optional

from adapters import lctl as lctl_adapter
from adapters import lfs as lfs_adapter
from errors import LqaNotFound, RangeConflict
from schemas.lqa import (
    LqaCreateRequest,
    LqaDetail,
    LqaGraceRequest,
    LqaGraceResponse,
    LqaQuotaResponse,
    LqaQuotaSetRequest,
    LqaRangeRequest,
    LqaRangeResponse,
    LqaSummary,
)
from services.filesystem import get_mountpoint
from store.assignment_store import get_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


def _check_range_conflict(
    fsname: str, lqa_name: str, new_start: int, new_end: int
) -> None:
    """
    Verify that [new_start, new_end] does not overlap with any range already
    assigned to any other LQA in the same filesystem.
    """
    all_lqas = lctl_adapter.lctl_lqa_list_all(fsname)
    for name in all_lqas:
        if name == lqa_name:
            continue
        try:
            existing_ranges = lctl_adapter.lctl_lqa_list_ranges(fsname, name)
        except Exception:
            continue
        for r in existing_ranges:
            if _ranges_overlap(new_start, new_end, r["start"], r["end"]):
                raise RangeConflict(
                    f"Range {new_start}-{new_end} overlaps with LQA '{name}' "
                    f"range {r['start']}-{r['end']}",
                    {"conflicting_lqa": name, "conflicting_range": r},
                )


# ---------------------------------------------------------------------------
# LQA lifecycle
# ---------------------------------------------------------------------------

def list_lqas(fsname: str) -> List[LqaSummary]:
    """List all LQAs with their ranges."""
    names = lctl_adapter.lctl_lqa_list_all(fsname)
    result = []
    for name in names:
        try:
            raw_ranges = lctl_adapter.lctl_lqa_list_ranges(fsname, name)
        except Exception:
            raw_ranges = []
        result.append(LqaSummary(
            name=name,
            ranges=[LqaRangeResponse(start=r["start"], end=r["end"]) for r in raw_ranges],
        ))
    return result


def create_lqa(fsname: str, req: LqaCreateRequest) -> LqaSummary:
    lctl_adapter.lctl_lqa_new(fsname, req.name)
    return LqaSummary(name=req.name, ranges=[])


def get_lqa(
    fsname: str,
    name: str,
    accurate_usage: bool = False,
    check_consistency: bool = False,
) -> LqaDetail:
    # Verify it exists by listing ranges (raises LqaNotFound if absent)
    try:
        raw_ranges = lctl_adapter.lctl_lqa_list_ranges(fsname, name)
    except LqaNotFound:
        raise LqaNotFound(
            f"LQA '{name}' not found in filesystem '{fsname}'",
            {"fsname": fsname, "name": name},
        )
    # Fetch quota; return None if not set or unavailable
    quota = None
    try:
        quota = get_lqa_quota(
            fsname, name,
            accurate_usage=accurate_usage,
            check_consistency=check_consistency,
        )
    except Exception:
        quota = None
    return LqaDetail(
        name=name,
        fsname=fsname,
        ranges=[LqaRangeResponse(start=r["start"], end=r["end"]) for r in raw_ranges],
        quota=quota,
    )


def destroy_lqa(fsname: str, name: str) -> None:
    lctl_adapter.lctl_lqa_destroy(fsname, name)
    # Demote all records governed by this LQA in the assignment store
    get_store().on_lqa_destroyed(fsname, name)


# ---------------------------------------------------------------------------
# Range management
# ---------------------------------------------------------------------------

def list_lqa_ranges(fsname: str, name: str) -> List[LqaRangeResponse]:
    raw = lctl_adapter.lctl_lqa_list_ranges(fsname, name)
    return [LqaRangeResponse(start=r["start"], end=r["end"]) for r in raw]


def add_lqa_range(fsname: str, name: str, req: LqaRangeRequest) -> List[LqaRangeResponse]:
    _check_range_conflict(fsname, name, req.start, req.end)
    lctl_adapter.lctl_lqa_add_range(fsname, name, req.start, req.end)
    raw = lctl_adapter.lctl_lqa_list_ranges(fsname, name)
    # Sync updated ranges into assignment store for all governed tier-1 records
    get_store().sync_lqa_ranges(
        fsname, name, [{"start": r["start"], "end": r["end"]} for r in raw]
    )
    return [LqaRangeResponse(start=r["start"], end=r["end"]) for r in raw]


def remove_lqa_range(
    fsname: str, name: str, start: int, end: int
) -> List[LqaRangeResponse]:
    lctl_adapter.lctl_lqa_remove_range(fsname, name, start, end)
    raw = lctl_adapter.lctl_lqa_list_ranges(fsname, name)
    get_store().sync_lqa_ranges(
        fsname, name, [{"start": r["start"], "end": r["end"]} for r in raw]
    )
    return [LqaRangeResponse(start=r["start"], end=r["end"]) for r in raw]


# ---------------------------------------------------------------------------
# Quota values
# ---------------------------------------------------------------------------

def get_lqa_quota(
    fsname: str,
    name: str,
    accurate_usage: bool = False,
    check_consistency: bool = False,
) -> LqaQuotaResponse:
    mnt = get_mountpoint(fsname)
    data = lfs_adapter.lfs_get_lqa_quota(mnt, name)

    actual_block: Optional[str] = None
    usage_warning: Optional[str] = None
    misconfigured_dirs: Optional[List[str]] = None

    # Fetch LQA ranges once if either flag is set (they share the same input)
    if accurate_usage or check_consistency:
        lqa_ranges = lctl_adapter.lctl_lqa_list_ranges(fsname, name)
        if accurate_usage:
            actual_block = _compute_accurate_usage(mnt, lqa_ranges)
        if check_consistency:
            usage_warning, misconfigured_dirs = _check_consistency(mnt, lqa_ranges)

    return LqaQuotaResponse(
        lqa_name=name,
        fsname=fsname,
        block_softlimit=data["block_softlimit"],
        block_hardlimit=data["block_hardlimit"],
        block_granted=data["block_usage"],
        actual_block_usage=actual_block,
        usage_warning=usage_warning,
        misconfigured_dirs=misconfigured_dirs,
    )


def _compute_accurate_usage(mnt: str, lqa_ranges: List[Dict]) -> str:
    """
    Return block_usage_human.

    Sums actual block usage for every projid that falls within the LQA's
    ranges using a single lfs_iterate_project_quotas call — O(1) regardless
    of how many L2 subdirs exist.
    """
    range_pairs = [(r["start"], r["end"]) for r in lqa_ranges]

    def in_lqa_range(projid: int) -> bool:
        return any(s <= projid <= e for s, e in range_pairs)

    all_quotas: Dict[int, Dict] = lfs_adapter.lfs_iterate_project_quotas(mnt)
    total_block_bytes = 0
    for projid, q in all_quotas.items():
        if in_lqa_range(projid):
            total_block_bytes += q.get("block_usage", 0)

    return lfs_adapter.bytes_to_human(total_block_bytes)


def _check_consistency(mnt: str, lqa_ranges: List[Dict]) -> tuple:
    """
    Scan L1 dirs → L2 subdirs to detect misconfigured project IDs.
    Returns (warning_str_or_None, misconfigured_dirs_list).
    misconfigured_dirs contains paths of L2 subdirs that have projid == 0
    or a projid outside the LQA ranges.

    Performance:
      - lfs_list_subdirs(mnt)          : 1 call  (find, fast)
      - lfs_list_subdirs(l1)           : 1 call per L1 dir (find, fast)
      - lfs_get_dir_projects_batch(l2s): 1 call per L1 dir (lfs project -d path1 path2 ...)
        O(L1_count) batch calls instead of O(total_L2) individual calls.
    """
    range_pairs = [(r["start"], r["end"]) for r in lqa_ranges]

    def in_lqa_range(projid: int) -> bool:
        return any(s <= projid <= e for s, e in range_pairs)

    bad_dirs: List[str] = []
    try:
        l1_candidates = lfs_adapter.lfs_list_subdirs(mnt)
        for l1_path in l1_candidates:
            try:
                l2_subdirs = lfs_adapter.lfs_list_subdirs(l1_path)
            except Exception:
                continue
            if not l2_subdirs:
                continue

            # One batch call to get all L2 projids — O(1) per L1 dir
            l2_projids = lfs_adapter.lfs_get_dir_projects_batch(l2_subdirs)

            # Skip L1 dirs that don't belong to this LQA
            if not any(in_lqa_range(pid) for pid in l2_projids.values()):
                continue

            # This L1 belongs to our LQA — collect all bad L2 subdirs
            for subdir, projid in l2_projids.items():
                if projid == 0 or not in_lqa_range(projid):
                    bad_dirs.append(subdir)
    except Exception:
        pass  # best-effort

    if bad_dirs:
        return "usage結果可能不準確，因爲：子目錄未設置lqa範圍内的project id", bad_dirs
    return None, None


def set_lqa_quota(fsname: str, name: str, req: LqaQuotaSetRequest) -> LqaQuotaResponse:
    mnt = get_mountpoint(fsname)
    lfs_adapter.lfs_set_lqa_quota(mnt, name, req.model_dump(exclude_none=True))
    return get_lqa_quota(fsname, name, accurate_usage=False)


def get_lqa_grace(fsname: str, name: str) -> LqaGraceResponse:
    mnt = get_mountpoint(fsname)
    data = lfs_adapter.lfs_get_lqa_grace(mnt, name)
    return LqaGraceResponse(
        block_grace=data["block_grace"],
        inode_grace=data["inode_grace"],
    )


def set_lqa_grace(fsname: str, name: str, req: LqaGraceRequest) -> LqaGraceResponse:
    mnt = get_mountpoint(fsname)
    lfs_adapter.lfs_set_lqa_grace(mnt, name, req.model_dump(exclude_none=True))
    data = lfs_adapter.lfs_get_lqa_grace(mnt, name)
    return LqaGraceResponse(
        block_grace=data["block_grace"],
        inode_grace=data["inode_grace"],
    )
