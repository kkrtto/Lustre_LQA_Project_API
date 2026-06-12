import re
from typing import Optional, Union

from pydantic import BaseModel, field_validator, model_validator

# ---------------------------------------------------------------------------
# Import shared quota-limit validator from LQA schema
# ---------------------------------------------------------------------------
from schemas.lqa import _parse_block_limit


class ProjectQuotaSetRequest(BaseModel):
    """
    Request body for setting project quota limits.
    block limits: int (bytes, 0=unlimited) or unit string like '100G', '1T'.
    inode limits: int (0=unlimited).
    """
    block_softlimit: Optional[Union[int, str]] = None
    block_hardlimit: Optional[Union[int, str]] = None
    inode_softlimit: Optional[int] = None
    inode_hardlimit: Optional[int] = None

    @field_validator("block_softlimit", "block_hardlimit", mode="before")
    @classmethod
    def validate_block_limit(cls, v):
        return _parse_block_limit(v)

    @model_validator(mode="after")
    def check_limits(self) -> "ProjectQuotaSetRequest":
        bh, bs = self.block_hardlimit, self.block_softlimit
        ih, is_ = self.inode_hardlimit, self.inode_softlimit
        if (isinstance(bh, int) and isinstance(bs, int)
                and bh is not None and bs is not None
                and bs > 0 and bh < bs):
            raise ValueError("block_hardlimit must be >= block_softlimit")
        if ih is not None and is_ is not None and is_ > 0 and ih < is_:
            raise ValueError("inode_hardlimit must be >= inode_softlimit")
        return self


class ProjectQuotaResponse(BaseModel):
    projid: int
    block_softlimit: str          # human-readable, e.g. "0k", "100G"
    block_hardlimit: str          # human-readable
    block_usage: str              # human-readable (from lfs quota -h)
    inode_softlimit: int
    inode_hardlimit: int
    inode_usage: int
    block_grace: str              # "-" | grace time string | "notify"
    inode_grace: str
    is_default: bool = False


class GraceTimeRequest(BaseModel):
    """
    Grace time format examples: "7days", "1w4d", "3600", "notify".
    "notify" means warn-only (no hard block until hardlimit).
    """
    block_grace: Optional[str] = None
    inode_grace: Optional[str] = None


class GraceTimeResponse(BaseModel):
    block_grace: str
    inode_grace: str
