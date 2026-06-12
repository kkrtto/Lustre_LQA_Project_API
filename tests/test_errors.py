"""
tests/test_errors.py — Unit tests for the exception hierarchy and error handler.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from errors import (
    DirectoryNotFound,
    FilesystemNotFound,
    InvalidParameter,
    LqaAlreadyExists,
    LqaNotFound,
    LustreApiError,
    LustreCommandError,
    LustreTimeoutError,
    MgsNotFound,
    PathNotUnderMountpoint,
    PermissionDenied,
    ProjectIdOutOfLqaRange,
    RangeConflict,
    lustre_api_error_handler,
)


def _make_test_app(exc: LustreApiError) -> TestClient:
    app = FastAPI()
    app.add_exception_handler(LustreApiError, lustre_api_error_handler)

    @app.get("/boom")
    def boom():
        raise exc

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("exc_cls,expected_status,expected_code", [
    (FilesystemNotFound, 404, "FILESYSTEM_NOT_FOUND"),
    (LqaNotFound, 404, "LQA_NOT_FOUND"),
    (LqaAlreadyExists, 409, "LQA_ALREADY_EXISTS"),
    (RangeConflict, 409, "RANGE_CONFLICT"),
    (PermissionDenied, 403, "PERMISSION_DENIED"),
    (LustreTimeoutError, 503, "LUSTRE_TIMEOUT"),
    (LustreCommandError, 503, "LUSTRE_ERROR"),
    (MgsNotFound, 503, "MGS_NOT_FOUND"),
    (ProjectIdOutOfLqaRange, 422, "PROJID_OUT_OF_LQA_RANGE"),
    (DirectoryNotFound, 404, "DIRECTORY_NOT_FOUND"),
    (PathNotUnderMountpoint, 400, "PATH_NOT_UNDER_MOUNTPOINT"),
    (InvalidParameter, 400, "INVALID_PARAMETER"),
])
def test_error_handler_status_and_code(exc_cls, expected_status, expected_code):
    client = _make_test_app(exc_cls("test message"))
    resp = client.get("/boom")
    assert resp.status_code == expected_status
    body = resp.json()
    assert body["code"] == expected_code
    assert body["message"] == "test message"
    assert "detail" in body


def test_error_detail_propagated():
    client = _make_test_app(LqaNotFound("not found", {"name": "x", "fsname": "y"}))
    body = client.get("/boom").json()
    assert body["detail"]["name"] == "x"
    assert body["detail"]["fsname"] == "y"
