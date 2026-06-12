from typing import Any, Dict, Optional

from fastapi import Request
from fastapi.responses import JSONResponse


class LustreApiError(Exception):
    http_status: int = 500
    code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, detail: Optional[Dict[str, Any]] = None):
        self.message = message
        self.detail = detail or {}
        super().__init__(message)


class FilesystemNotFound(LustreApiError):
    http_status = 404
    code = "FILESYSTEM_NOT_FOUND"


class QuotaNotFound(LustreApiError):
    http_status = 404
    code = "QUOTA_NOT_FOUND"


class LqaNotFound(LustreApiError):
    http_status = 404
    code = "LQA_NOT_FOUND"


class LqaAlreadyExists(LustreApiError):
    http_status = 409
    code = "LQA_ALREADY_EXISTS"


class RangeConflict(LustreApiError):
    http_status = 409
    code = "RANGE_CONFLICT"


class PermissionDenied(LustreApiError):
    http_status = 403
    code = "PERMISSION_DENIED"


class LustreTimeoutError(LustreApiError):
    http_status = 503
    code = "LUSTRE_TIMEOUT"


class LustreCommandError(LustreApiError):
    http_status = 503
    code = "LUSTRE_ERROR"


class MgsNotFound(LustreApiError):
    http_status = 503
    code = "MGS_NOT_FOUND"


class ProjectIdOutOfLqaRange(LustreApiError):
    http_status = 422
    code = "PROJID_OUT_OF_LQA_RANGE"


class DirectoryNotFound(LustreApiError):
    http_status = 404
    code = "DIRECTORY_NOT_FOUND"


class PathNotUnderMountpoint(LustreApiError):
    http_status = 400
    code = "PATH_NOT_UNDER_MOUNTPOINT"


class InvalidParameter(LustreApiError):
    http_status = 400
    code = "INVALID_PARAMETER"


async def lustre_api_error_handler(request: Request, exc: LustreApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "code": exc.code,
            "message": exc.message,
            "detail": exc.detail,
        },
    )
