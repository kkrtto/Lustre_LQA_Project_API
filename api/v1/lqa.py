"""
api/v1/lqa.py — Routes for LQA management.
"""

import re
from typing import List, Optional

from fastapi import APIRouter, Query

from errors import InvalidParameter
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
import services.lqa as svc

router = APIRouter()


# ---------------------------------------------------------------------------
# LQA collection
# ---------------------------------------------------------------------------

@router.get("", response_model=List[LqaSummary])
async def list_lqas(fsname: str) -> List[LqaSummary]:
    """List all LQAs for a filesystem."""
    return svc.list_lqas(fsname)


@router.post("", response_model=LqaSummary, status_code=201)
async def create_lqa(fsname: str, req: LqaCreateRequest) -> LqaSummary:
    """Create a new LQA. The LQA name must match the basename of the L1 directory."""
    return svc.create_lqa(fsname, req)


# ---------------------------------------------------------------------------
# Single LQA
# ---------------------------------------------------------------------------

@router.get("/{name}", response_model=LqaDetail)
async def get_lqa(
    fsname: str,
    name: str,
    accurate_usage: bool = Query(default=False),
    check_consistency: bool = Query(default=False),
) -> LqaDetail:
    """Get details (name, ranges, quota) for a specific LQA.
    ?accurate_usage=true  — include actual_block_usage (fast, single iterate call).
    ?check_consistency=true — scan L2 subdirs for misconfigured project IDs (potentially slow).
    """
    return svc.get_lqa(fsname, name, accurate_usage=accurate_usage, check_consistency=check_consistency)


@router.delete("/{name}", status_code=204)
async def destroy_lqa(fsname: str, name: str) -> None:
    """Destroy an LQA. All governed assignment records are demoted to tier=0."""
    svc.destroy_lqa(fsname, name)


# ---------------------------------------------------------------------------
# Ranges
# ---------------------------------------------------------------------------

@router.get("/{name}/ranges", response_model=List[LqaRangeResponse])
async def list_ranges(fsname: str, name: str) -> List[LqaRangeResponse]:
    """List all project ID ranges assigned to an LQA."""
    return svc.list_lqa_ranges(fsname, name)


@router.post("/{name}/ranges", response_model=List[LqaRangeResponse], status_code=201)
async def add_range(
    fsname: str, name: str, req: LqaRangeRequest
) -> List[LqaRangeResponse]:
    """Add a new project ID range to an LQA (overlap with other LQAs is rejected)."""
    return svc.add_lqa_range(fsname, name, req)


@router.delete("/{name}/ranges/{range_str}", response_model=List[LqaRangeResponse])
async def remove_range(
    fsname: str, name: str, range_str: str
) -> List[LqaRangeResponse]:
    """
    Remove a project ID range from an LQA.
    range_str format: start-end  e.g. 20001-30000
    """
    m = re.fullmatch(r"(\d+)-(\d+)", range_str)
    if not m:
        raise InvalidParameter(
            "Range must be in format 'start-end' (e.g. 20001-30000)",
            {"range_str": range_str},
        )
    return svc.remove_lqa_range(fsname, name, int(m.group(1)), int(m.group(2)))


# ---------------------------------------------------------------------------
# Quota
# ---------------------------------------------------------------------------

@router.get("/{name}/quota", response_model=LqaQuotaResponse)
async def get_lqa_quota(
    fsname: str,
    name: str,
    accurate_usage: bool = Query(default=False),
    check_consistency: bool = Query(default=False),
) -> LqaQuotaResponse:
    """
    Get quota for an LQA.
    ?accurate_usage=true     — include actual_block_usage (fast, O(1) iterate call).
    ?check_consistency=true  — scan L2 subdirs for misconfigured project IDs (slow for large dirs).
    Both flags are independent and can be combined.
    """
    return svc.get_lqa_quota(fsname, name, accurate_usage, check_consistency)


@router.put("/{name}/quota", response_model=LqaQuotaResponse)
async def set_lqa_quota(
    fsname: str, name: str, req: LqaQuotaSetRequest
) -> LqaQuotaResponse:
    """Set quota limits for an LQA."""
    return svc.set_lqa_quota(fsname, name, req)


# ---------------------------------------------------------------------------
# Grace
# ---------------------------------------------------------------------------

@router.get("/{name}/grace", response_model=LqaGraceResponse)
async def get_lqa_grace(fsname: str, name: str) -> LqaGraceResponse:
    """Get grace times for an LQA."""
    return svc.get_lqa_grace(fsname, name)


@router.put("/{name}/grace", response_model=LqaGraceResponse)
async def set_lqa_grace(
    fsname: str, name: str, req: LqaGraceRequest
) -> LqaGraceResponse:
    """Set grace times for an LQA."""
    return svc.set_lqa_grace(fsname, name, req)
