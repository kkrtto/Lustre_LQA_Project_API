"""
api/v1/project_quota.py — Routes for per-project quota management.

IMPORTANT: Static path segments (/default, /grace) MUST be defined before
the parameterised route (/{projid}) to avoid FastAPI routing ambiguity.
"""

from fastapi import APIRouter

from schemas.project_quota import (
    GraceTimeRequest,
    GraceTimeResponse,
    ProjectQuotaResponse,
    ProjectQuotaSetRequest,
)
import services.project_quota as svc

router = APIRouter()


# ---------------------------------------------------------------------------
# Default project quota  (MUST come before /{projid})
# ---------------------------------------------------------------------------

@router.get("/default", response_model=ProjectQuotaResponse)
async def get_default_quota(fsname: str) -> ProjectQuotaResponse:
    """Get the default project quota for a filesystem."""
    return svc.get_default_project_quota(fsname)


@router.put("/default", response_model=ProjectQuotaResponse)
async def set_default_quota(
    fsname: str,
    req: ProjectQuotaSetRequest,
) -> ProjectQuotaResponse:
    """Set the default project quota for a filesystem (or pool)."""
    return svc.set_default_project_quota(fsname, req)


# ---------------------------------------------------------------------------
# Grace times  (MUST come before /{projid})
# ---------------------------------------------------------------------------

@router.get("/grace", response_model=GraceTimeResponse)
async def get_grace(fsname: str) -> GraceTimeResponse:
    """Get project quota grace times."""
    return svc.get_grace_time(fsname)


@router.put("/grace", response_model=GraceTimeResponse)
async def set_grace(
    fsname: str,
    req: GraceTimeRequest,
) -> GraceTimeResponse:
    """Set project quota grace times."""
    return svc.set_grace_time(fsname, req)


# ---------------------------------------------------------------------------
# Per-project operations
# ---------------------------------------------------------------------------

@router.get("/{projid}", response_model=ProjectQuotaResponse)
async def get_quota(fsname: str, projid: int) -> ProjectQuotaResponse:
    """Get quota for a specific project ID."""
    return svc.get_project_quota(fsname, projid)


@router.put("/{projid}", response_model=ProjectQuotaResponse)
async def set_quota(
    fsname: str,
    projid: int,
    req: ProjectQuotaSetRequest,
) -> ProjectQuotaResponse:
    """Set quota limits for a specific project ID."""
    return svc.set_project_quota(fsname, projid, req)


@router.delete("/{projid}", status_code=204)
async def delete_quota(
    fsname: str,
    projid: int,
) -> None:
    """Delete (remove) quota record for a specific project ID."""
    svc.delete_project_quota(fsname, projid)


@router.post("/{projid}/reset", response_model=ProjectQuotaResponse)
async def reset_quota(
    fsname: str,
    projid: int,
) -> ProjectQuotaResponse:
    """Reset quota usage counters for a specific project ID."""
    return svc.reset_project_quota(fsname, projid)
