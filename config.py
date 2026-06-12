import functools
import os
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, DotEnvSettingsSource, EnvSettingsSource, SettingsConfigDict


class LqaExecutionMode(str, Enum):
    LOCAL = "local"   # lctl runs on the same host as this API process
    SSH   = "ssh"     # lctl must be run on a remote MGS node via SSH


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LUSTRE_API_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # ── Tool paths ────────────────────────────────────────────────────────────
    lfs_path: str = "/usr/bin/lfs"
    lctl_path: str = "/usr/sbin/lctl"

    # ── LQA execution mode ────────────────────────────────────────────────────
    # Set to "ssh" when this API is deployed on a client node (not on MDS/MGS).
    lqa_mode: LqaExecutionMode = LqaExecutionMode.LOCAL

    # ── MGS HA configuration (used when lqa_mode = "ssh") ────────────────────
    # Ordered list of candidate MGS host addresses (hostname or IP).
    # The adapter will iterate this list to find the currently active MGS.
    # Example env var: LUSTRE_API_MGS_HOSTS=192.168.1.10,192.168.1.11
    mgs_hosts: List[str] = []

    # SSH credentials for reaching MGS nodes.
    # Only key-based authentication is supported; password auth is intentionally
    # disabled to prevent credentials from being exposed in config files or logs.
    ssh_user: str = "root"
    ssh_key: Optional[str] = None     # REQUIRED when lqa_mode=ssh; absolute path to SSH private key
    ssh_port: int = 22

    # How long (seconds) to cache the discovered active MGS host
    mgs_cache_ttl: int = 60

    # ── Timeouts ──────────────────────────────────────────────────────────────
    lfs_timeout: int = 30             # seconds for lfs commands
    lctl_timeout: int = 30            # seconds for lctl commands
    ssh_connect_timeout: int = 5      # seconds for SSH handshake

    # ── Filesystem mountpoints ────────────────────────────────────────────────
    # Explicit fsname → mountpoint mapping.  In ssh mode this is REQUIRED
    # because /proc/mounts on the local API node does not list the remote
    # Lustre mounts.  In local mode it is optional — the API falls back to
    # /proc/mounts discovery for any fsname not present here.
    # Example: LUSTRE_API_MOUNTPOINTS='{"aifs": "/lustre/aifs"}'
    mountpoints: Dict[str, str] = {}

    # ── Persistence ───────────────────────────────────────────────────────────
    assignment_store_path: str = "./data/assignments.json"

    @field_validator("mgs_hosts", mode="before")
    @classmethod
    def parse_mgs_hosts(cls, v):
        """Accept comma-separated string or list (fallback; primary handling is
        in the custom env source below)."""
        if isinstance(v, str):
            return [h.strip() for h in v.split(",") if h.strip()]
        return v

    @model_validator(mode="after")
    def require_ssh_key_in_ssh_mode(self) -> "Settings":
        """Enforce that ssh_key is provided when lqa_mode=ssh."""
        if self.lqa_mode == LqaExecutionMode.SSH and not self.ssh_key:
            raise ValueError(
                "LUSTRE_API_SSH_KEY must be set when LUSTRE_API_LQA_MODE=ssh. "
                "Password-based SSH authentication is not supported."
            )
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        """
        Override both the env-var source and the .env-file source so that:
          • mgs_hosts accepts comma-separated strings (in addition to JSON arrays)
          • mountpoints accepts unquoted JSON dicts from .env files

        pydantic-settings 2.x splits these into two separate source classes:
          • EnvSettingsSource    — reads process environment variables
          • DotEnvSettingsSource — reads .env files
        Both need the same prepare_field_value override.
        """
        def _prepare(base_cls, field_name, field, value, value_is_complex, super_fn):
            if isinstance(value, str):
                v = value.strip()
                if field_name == "mgs_hosts":
                    if v.startswith("["):
                        return super_fn(field_name, field, value, value_is_complex)
                    return [h.strip() for h in v.split(",") if h.strip()] if v else []
                if field_name == "mountpoints":
                    if v.startswith("{"):
                        import json
                        try:
                            return json.loads(v)
                        except Exception:
                            pass
            return super_fn(field_name, field, value, value_is_complex)

        class _CustomEnvSource(EnvSettingsSource):
            def prepare_field_value(self, field_name, field, value, value_is_complex):
                return _prepare(
                    EnvSettingsSource, field_name, field, value, value_is_complex,
                    super().prepare_field_value,
                )

        class _CustomDotEnvSource(DotEnvSettingsSource):
            def prepare_field_value(self, field_name, field, value, value_is_complex):
                return _prepare(
                    DotEnvSettingsSource, field_name, field, value, value_is_complex,
                    super().prepare_field_value,
                )

        return (
            init_settings,
            _CustomEnvSource(settings_cls),
            _CustomDotEnvSource(
                settings_cls,
                # Allow tests (or operators) to redirect the env file by setting
                # LUSTRE_API__ENV_FILE=/dev/null (double underscore — not a
                # settings field, read directly from os.environ before sources
                # are constructed).
                env_file=os.environ.get(
                    "LUSTRE_API__ENV_FILE",
                    settings_cls.model_config.get("env_file"),
                ),
            ),
            file_secret_settings,
        )


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
