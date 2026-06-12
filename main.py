import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.v1.router import router as v1_router
from config import get_settings
from errors import LustreApiError, lustre_api_error_handler
from store.assignment_store import get_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    store_dir = os.path.dirname(os.path.abspath(settings.assignment_store_path))
    os.makedirs(store_dir, exist_ok=True)
    get_store()  # initialise / create file if absent
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Lustre Project Quota API",
        description=(
            "REST API for managing Lustre project quotas and "
            "LQA (Lustre Quota Aggregation). Implements a two-tier directory "
            "structure where Level-1 directories are governed by LQA aggregated "
            "quotas and Level-2 subdirectories use individual project quotas."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(LustreApiError, lustre_api_error_handler)  # type: ignore[arg-type]

    app.include_router(v1_router, prefix="/api/v1")

    @app.get("/health", tags=["Health"])
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/health/mgs", tags=["Health"])
    async def health_mgs() -> dict:
        """
        Diagnostic endpoint: test MGS connectivity and verify lctl is reachable.
        Returns raw lctl output so you can confirm the correct commands exist.
        """
        import subprocess
        from config import LqaExecutionMode, get_settings
        from adapters.lctl import get_active_mgs_host, exec_remote_cmd, _shell_quote

        settings = get_settings()
        result: dict = {
            "mode": settings.lqa_mode.value,
            "mgs_hosts": settings.mgs_hosts,
            "mountpoints": settings.mountpoints,
        }

        try:
            active_host = get_active_mgs_host()
            result["active_mgs"] = active_host or "local"

            # Run 'lctl dl' to verify lctl works and MGS device is present
            lctl = settings.lctl_path
            if settings.lqa_mode == LqaExecutionMode.SSH and active_host:
                cmd = f"{_shell_quote(lctl)} dl 2>&1 | head -20"
                stdout, stderr, rc = exec_remote_cmd(active_host, cmd, settings.lctl_timeout)
            else:
                r = subprocess.run(
                    [lctl, "dl"], capture_output=True, text=True,
                    timeout=settings.lctl_timeout
                )
                stdout, stderr, rc = r.stdout, r.stderr, r.returncode

            result["lctl_dl_rc"] = rc
            result["lctl_dl_stdout"] = stdout.strip().splitlines()[:20]
            if stderr.strip():
                result["lctl_dl_stderr"] = stderr.strip()

            # Run 'lctl lqa_list --fsname <fsname>' for first configured fsname
            fsnames = list(settings.mountpoints.keys())
            if fsnames:
                fsname = fsnames[0]
                lqa_cmd_args = [lctl, "lqa_list", "--fsname", fsname]
                if settings.lqa_mode == LqaExecutionMode.SSH and active_host:
                    cmd2 = " ".join(_shell_quote(a) for a in lqa_cmd_args) + " 2>&1"
                    o2, e2, rc2 = exec_remote_cmd(active_host, cmd2, settings.lctl_timeout)
                else:
                    r2 = subprocess.run(
                        lqa_cmd_args, capture_output=True, text=True,
                        timeout=settings.lctl_timeout
                    )
                    o2, e2, rc2 = r2.stdout, r2.stderr, r2.returncode
                result["lctl_lqa_list"] = {
                    "fsname": fsname,
                    "rc": rc2,
                    "stdout": o2.strip(),
                    "stderr": e2.strip(),
                }

        except Exception as exc:
            result["error"] = str(exc)

        return result

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
