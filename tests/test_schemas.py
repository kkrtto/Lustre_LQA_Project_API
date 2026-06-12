"""
tests/test_schemas.py — Unit tests for Pydantic schema validation.
"""

import pytest
from pydantic import ValidationError

from schemas.directory import DirProjectSetRequest
from schemas.lqa import LqaCreateRequest, LqaRangeRequest, LqaQuotaSetRequest
from schemas.project_quota import ProjectQuotaSetRequest


# ── ProjectQuotaSetRequest ────────────────────────────────────────────────────

class TestProjectQuotaSetRequest:
    def test_valid(self):
        r = ProjectQuotaSetRequest(
            block_softlimit=1_000_000_000,
            block_hardlimit=2_000_000_000,
        )
        assert r.block_hardlimit == 2_000_000_000

    def test_hard_lt_soft_raises(self):
        with pytest.raises(ValidationError, match="block_hardlimit"):
            ProjectQuotaSetRequest(block_softlimit=2_000_000_000, block_hardlimit=1_000_000_000)

    def test_inode_hard_lt_soft_raises(self):
        with pytest.raises(ValidationError, match="inode_hardlimit"):
            ProjectQuotaSetRequest(inode_softlimit=1000, inode_hardlimit=500)

    def test_only_hardlimit_is_valid(self):
        """Providing only hardlimit (no soft) is valid."""
        r = ProjectQuotaSetRequest(block_hardlimit=1_000_000_000)
        assert r.block_softlimit is None

    def test_zero_means_unlimited(self):
        """0 is a valid value meaning 'unlimited'."""
        r = ProjectQuotaSetRequest(block_hardlimit=0, block_softlimit=0)
        assert r.block_hardlimit == 0


# ── LqaCreateRequest ──────────────────────────────────────────────────────────

class TestLqaCreateRequest:
    def test_valid_name(self):
        assert LqaCreateRequest(name="proj_A").name == "proj_A"

    def test_all_allowed_chars(self):
        assert LqaCreateRequest(name="Abc_123").name == "Abc_123"

    def test_too_long(self):
        with pytest.raises(ValidationError):
            LqaCreateRequest(name="a" * 17)

    def test_empty(self):
        with pytest.raises(ValidationError):
            LqaCreateRequest(name="")

    def test_space_not_allowed(self):
        with pytest.raises(ValidationError):
            LqaCreateRequest(name="proj A")

    def test_hyphen_not_allowed(self):
        with pytest.raises(ValidationError):
            LqaCreateRequest(name="proj-A")


# ── LqaRangeRequest ───────────────────────────────────────────────────────────

class TestLqaRangeRequest:
    def test_valid(self):
        r = LqaRangeRequest(start=20001, end=30000)
        assert r.start == 20001

    def test_start_equals_end_valid(self):
        r = LqaRangeRequest(start=20001, end=20001)
        assert r.start == r.end

    def test_end_lt_start(self):
        with pytest.raises(ValidationError, match="end must be >= start"):
            LqaRangeRequest(start=30000, end=20001)

    def test_negative_start(self):
        with pytest.raises(ValidationError, match="start must be >= 0"):
            LqaRangeRequest(start=-1, end=100)

    def test_end_exceeds_max(self):
        with pytest.raises(ValidationError):
            LqaRangeRequest(start=0, end=0x1_0000_0000)


# ── DirProjectSetRequest ──────────────────────────────────────────────────────

class TestDirProjectSetRequest:
    def test_valid(self):
        r = DirProjectSetRequest(path="/lustre/aifs/proj_A", projid=20001)
        assert r.path == "/lustre/aifs/proj_A"

    def test_relative_path_accepted(self):
        """Relative paths are now allowed; service layer resolves them to absolute."""
        r = DirProjectSetRequest(path="LQA_prj1/prj50001", projid=20001)
        assert r.path == "LQA_prj1/prj50001"

    def test_dotdot_rejected(self):
        """Paths containing '..' components are rejected by the schema validator."""
        with pytest.raises(ValidationError, match=r"\.\."):
            DirProjectSetRequest(path="LQA_prj1/../../../etc/passwd", projid=1)

    def test_absolute_dotdot_rejected(self):
        with pytest.raises(ValidationError, match=r"\.\."):
            DirProjectSetRequest(path="/lustre/../etc/passwd", projid=1)

    def test_path_accepted_with_slashes(self):
        r = DirProjectSetRequest(path="proj_A/user1", projid=20001)
        assert r.path == "proj_A/user1"

    def test_projid_zero_rejected(self):
        with pytest.raises(ValidationError, match="projid must be >= 1"):
            DirProjectSetRequest(path="/lustre/aifs/proj_A", projid=0)

    def test_projid_max_valid(self):
        r = DirProjectSetRequest(path="/lustre/aifs/proj_A", projid=0xFFFFFFFF)
        assert r.projid == 0xFFFFFFFF

    def test_projid_over_max(self):
        with pytest.raises(ValidationError):
            DirProjectSetRequest(path="/lustre/aifs/proj_A", projid=0x1_0000_0000)
