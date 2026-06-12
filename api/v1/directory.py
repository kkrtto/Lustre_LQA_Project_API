"""
api/v1/directory.py — Routes for per-directory project ID management.

All ``path`` parameters may be either:
  - **Relative**: "LQA_prj1/prj50001" (relative to the filesystem mountpoint)
  - **Absolute**: "/lustre/aifs/client/LQA_prj1/prj50001"
"""

from fastapi import APIRouter, Query

from schemas.directory import (
    DirProjectResponse,
    DirProjectSetRequest,
    NextProjidResponse,
    UnassignedSubdirsResponse,
)
import services.directory as svc

router = APIRouter()


@router.get("/project", response_model=DirProjectResponse)
async def get_dir_project(
    fsname: str,
    path: str = Query(
        ...,
        description=(
            "Path to the directory — relative to the mountpoint "
            "(e.g. \"LQA_prj1/prj50001\") or absolute."
        ),
    ),
) -> DirProjectResponse:
    """Get the current project ID for a directory."""
    return svc.get_dir_project(fsname, path)


@router.put("/project", response_model=DirProjectResponse)
async def set_dir_project(
    fsname: str,
    req: DirProjectSetRequest,
) -> DirProjectResponse:
    """
    Set the project ID for a directory (``lfs project -srp``).

    The ``path`` field in the request body may be a path relative to the
    mountpoint (e.g. ``"LQA_prj1/prj50001"``) or an absolute path.
    """
    return svc.set_dir_project(fsname, req)


@router.delete("/project", status_code=204)
async def clear_dir_project(
    fsname: str,
    path: str = Query(
        ...,
        description=(
            "Path to the directory — relative to the mountpoint "
            "(e.g. \"LQA_prj1/prj50001\") or absolute."
        ),
    ),
) -> None:
    """Clear the project ID from a directory (``lfs project -C``)."""
    svc.clear_dir_project(fsname, path)


@router.get("/unassigned", response_model=UnassignedSubdirsResponse)
async def get_unassigned_subdirs(
    fsname: str,
    lqa: str = Query(
        ...,
        description=(
            "LQA name (also the basename of the L1 directory under the mountpoint). "
            "E.g. \"LQA_prj1\"."
        ),
    ),
) -> UnassignedSubdirsResponse:
    """
    Scan the immediate subdirectories of ``<mountpoint>/<lqa>`` and return
    those whose project ID is 0 (not yet assigned).

    LQA ranges are fetched from Lustre in real-time — no assignment store
    lookup is performed.

    ``next_projid`` in the response is the first project ID within the LQA
    ranges that is not currently used by any L2 subdir.
    """
    return svc.find_unassigned_subdirs(fsname, lqa)


@router.get("/next-projid", response_model=NextProjidResponse)
async def get_next_projid(
    fsname: str,
    lqa: str = Query(
        ...,
        description="LQA name (basename of the L1 directory under the mountpoint).",
    ),
) -> NextProjidResponse:
    """
    Predict the next available project ID for a new L2 subdirectory.

    Scans the immediate subdirectories of ``<mountpoint>/<lqa>``, collects
    all project IDs that fall within the LQA ranges, and returns the first
    unused project ID.

    Returns ``next_projid=null`` if all project IDs in all ranges are taken.
    """
    return svc.find_next_projid(fsname, lqa)
