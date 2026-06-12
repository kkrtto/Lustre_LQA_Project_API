from fastapi import APIRouter

from api.v1 import directory, lqa, project_quota

router = APIRouter()

router.include_router(
    project_quota.router,
    prefix="/filesystems/{fsname}/quotas/projects",
    tags=["Project Quota"],
)
router.include_router(
    lqa.router,
    prefix="/filesystems/{fsname}/lqas",
    tags=["LQA"],
)
router.include_router(
    directory.router,
    prefix="/filesystems/{fsname}/directories",
    tags=["Directory Project"],
)
