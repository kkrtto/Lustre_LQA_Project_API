import re
from typing import List, Optional, Union

from pydantic import BaseModel, field_validator, model_validator

# ---------------------------------------------------------------------------
# Shared quota-limit validator
# ---------------------------------------------------------------------------

_UNIT_RE = re.compile(r'^\d+(?:\.\d+)?[KkMmGgTtPp]$')


def _parse_block_limit(v):
    """
    Validate a block quota limit field.
    Accepts:
      int   — bytes (0 = unlimited)
      str   — unit string like '100G', '1T', '500M'  (passed directly to lfs)
      '0'   — normalised to int 0 (unlimited)
    """
    if v is None:
        return None
    if isinstance(v, int):
        if v < 0:
            raise ValueError("quota limit must be >= 0")
        return v
    if isinstance(v, str):
        s = v.strip()
        if s == "0" or s.isdigit():
            n = int(s)
            if n < 0:
                raise ValueError("quota limit must be >= 0")
            return n
        if _UNIT_RE.fullmatch(s):
            return s.upper()   # normalise unit to uppercase
        raise ValueError(
            f"Invalid quota limit '{v}'. Use bytes int (0=unlimited) "
            "or unit string like '100G', '1T', '500M'"
        )
    raise ValueError(f"Expected int or unit string, got {type(v).__name__}")


class LqaCreateRequest(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9_]{1,16}", v):
            raise ValueError(
                "LQA name must be 1-16 characters, [a-zA-Z0-9_] only"
            )
        return v


class LqaRangeRequest(BaseModel):
    start: int
    end: int

    @model_validator(mode="after")
    def check_range(self) -> "LqaRangeRequest":
        if self.start < 0:
            raise ValueError("start must be >= 0")
        if self.end < self.start:
            raise ValueError("end must be >= start")
        if self.end > 0xFFFFFFFF:
            raise ValueError("end must be <= 2^32-1")
        return self


class LqaRangeResponse(BaseModel):
    start: int
    end: int


class LqaQuotaSetRequest(BaseModel):
    """Block-only quota for LQA (inode quota is not supported at LQA level)."""
    block_softlimit: Optional[Union[int, str]] = None   # bytes or unit string
    block_hardlimit: Optional[Union[int, str]] = None   # bytes or unit string

    @field_validator("block_softlimit", "block_hardlimit", mode="before")
    @classmethod
    def validate_block_limit(cls, v):
        return _parse_block_limit(v)

    @model_validator(mode="after")
    def check_limits(self) -> "LqaQuotaSetRequest":
        bh, bs = self.block_hardlimit, self.block_softlimit
        # Ordering check only possible when both are plain integers
        if (isinstance(bh, int) and isinstance(bs, int)
                and bh is not None and bs is not None
                and bs > 0 and bh < bs):
            raise ValueError("block_hardlimit must be >= block_softlimit")
        return self


class LqaQuotaResponse(BaseModel):
    lqa_name: str
    fsname: str
    block_softlimit: str    # human-readable, e.g. "0k", "100G"
    block_hardlimit: str    # human-readable
    block_granted: str      # QMT grant-based usage (lfs quota -P --lqa)
    # Only populated when ?accurate_usage=true:
    actual_block_usage: Optional[str] = None   # sum of all L2 project usages
    # Only populated when ?check_consistency=true:
    usage_warning: Optional[str] = None        # set when misconfigured dirs are found
    misconfigured_dirs: Optional[List[str]] = None  # L2 subdirs with missing/wrong projid


class LqaGraceRequest(BaseModel):
    block_grace: Optional[str] = None
    inode_grace: Optional[str] = None


class LqaGraceResponse(BaseModel):
    block_grace: str
    inode_grace: str


class LqaSummary(BaseModel):
    name: str
    ranges: List[LqaRangeResponse] = []


class LqaDetail(BaseModel):
    name: str
    fsname: str
    ranges: List[LqaRangeResponse]
    quota: Optional[LqaQuotaResponse] = None
