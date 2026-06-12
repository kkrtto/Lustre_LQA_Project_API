"""
services/project_quota.py — Business logic for per-project quota management.

All calls are thin wrappers around adapters/lfs.py. The service layer is
responsible for fsname→mountpoint resolution and building response objects.
"""

from adapters import lfs as lfs_adapter
from schemas.project_quota import (
    GraceTimeRequest,
    GraceTimeResponse,
    ProjectQuotaResponse,
    ProjectQuotaSetRequest,
)
from services.filesystem import get_mountpoint


def _build_quota_response(projid: int, data: dict) -> ProjectQuotaResponse:
    return ProjectQuotaResponse(
        projid=projid,
        block_softlimit=data["block_softlimit"],
        block_hardlimit=data["block_hardlimit"],
        block_usage=data["block_usage"],
        inode_softlimit=data["inode_softlimit"],
        inode_hardlimit=data["inode_hardlimit"],
        inode_usage=data["inode_usage"],
        block_grace=data["block_grace"],
        inode_grace=data["inode_grace"],
        is_default=data.get("is_default", False),
    )


def get_project_quota(fsname: str, projid: int) -> ProjectQuotaResponse:
    mnt = get_mountpoint(fsname)
    data = lfs_adapter.lfs_get_project_quota(mnt, projid)
    return _build_quota_response(projid, data)


def set_project_quota(fsname: str, projid: int, req: ProjectQuotaSetRequest) -> ProjectQuotaResponse:
    mnt = get_mountpoint(fsname)
    lfs_adapter.lfs_set_project_quota(mnt, projid, req.model_dump(exclude_none=True))
    data = lfs_adapter.lfs_get_project_quota(mnt, projid)
    return _build_quota_response(projid, data)


def delete_project_quota(fsname: str, projid: int) -> None:
    mnt = get_mountpoint(fsname)
    lfs_adapter.lfs_delete_project_quota(mnt, projid)


def reset_project_quota(fsname: str, projid: int) -> ProjectQuotaResponse:
    mnt = get_mountpoint(fsname)
    lfs_adapter.lfs_reset_project_quota(mnt, projid)
    data = lfs_adapter.lfs_get_project_quota(mnt, projid)
    return _build_quota_response(projid, data)


def get_default_project_quota(fsname: str) -> ProjectQuotaResponse:
    mnt = get_mountpoint(fsname)
    data = lfs_adapter.lfs_get_default_project_quota(mnt)
    resp = _build_quota_response(0, data)
    resp.is_default = True
    return resp


def set_default_project_quota(fsname: str, req: ProjectQuotaSetRequest) -> ProjectQuotaResponse:
    mnt = get_mountpoint(fsname)
    lfs_adapter.lfs_set_default_project_quota(mnt, req.model_dump(exclude_none=True))
    data = lfs_adapter.lfs_get_default_project_quota(mnt)
    resp = _build_quota_response(0, data)
    resp.is_default = True
    return resp


def get_grace_time(fsname: str) -> GraceTimeResponse:
    mnt = get_mountpoint(fsname)
    data = lfs_adapter.lfs_get_grace_time(mnt)
    return GraceTimeResponse(
        block_grace=data["block_grace"],
        inode_grace=data["inode_grace"],
    )


def set_grace_time(fsname: str, req: GraceTimeRequest) -> GraceTimeResponse:
    mnt = get_mountpoint(fsname)
    lfs_adapter.lfs_set_grace_time(mnt, req.model_dump(exclude_none=True))
    data = lfs_adapter.lfs_get_grace_time(mnt)
    return GraceTimeResponse(
        block_grace=data["block_grace"],
        inode_grace=data["inode_grace"],
    )
