"""
tests/test_store.py — Unit tests for AssignmentStore.
"""

import json
import os
from datetime import datetime, timezone

import pytest

from schemas.assignment import AssignmentRecord, LqaRangeRecord
from store.assignment_store import AssignmentStore

NOW = datetime(2026, 6, 11, 8, 0, 0, tzinfo=timezone.utc)


def _make_record(
    path: str,
    projid: int,
    tier: int = 0,
    governing_lqa=None,
    lqa_ranges=None,
    l1_parent_path=None,
    parent_path: str = "/lustre/aifs",
    parent_projid: int = 0,
    fsname: str = "aifs",
) -> AssignmentRecord:
    return AssignmentRecord(
        path=path,
        fsname=fsname,
        projid=projid,
        parent_path=parent_path,
        parent_projid=parent_projid,
        governing_lqa=governing_lqa,
        lqa_ranges=lqa_ranges,
        l1_parent_path=l1_parent_path,
        tier=tier,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture()
def store(tmp_path) -> AssignmentStore:
    return AssignmentStore(str(tmp_path / "assignments.json"))


# ── Basic CRUD ────────────────────────────────────────────────────────────────

def test_upsert_and_get(store):
    rec = _make_record("/lustre/aifs/proj_A", 20001, tier=1, governing_lqa="proj_A")
    store.upsert(rec)
    fetched = store.get("/lustre/aifs/proj_A")
    assert fetched is not None
    assert fetched.projid == 20001
    assert fetched.tier == 1


def test_get_missing_returns_none(store):
    assert store.get("/does/not/exist") is None


def test_delete_existing(store):
    store.upsert(_make_record("/lustre/aifs/proj_A", 20001))
    assert store.delete("/lustre/aifs/proj_A") is True
    assert store.get("/lustre/aifs/proj_A") is None


def test_delete_nonexistent_returns_false(store):
    assert store.delete("/does/not/exist") is False


def test_upsert_preserves_created_at(store):
    rec = _make_record("/lustre/aifs/proj_A", 20001)
    store.upsert(rec)
    # Upsert again with updated projid — created_at must stay the same
    rec2 = _make_record("/lustre/aifs/proj_A", 20002)
    store.upsert(rec2)
    fetched = store.get("/lustre/aifs/proj_A")
    assert fetched.created_at == NOW


def test_get_all_returns_all_records(store):
    store.upsert(_make_record("/lustre/aifs/proj_A", 20001))
    store.upsert(_make_record("/lustre/aifs/proj_B", 30001))
    all_records = store.get_all()
    assert len(all_records) == 2


def test_atomic_write_creates_valid_json(store, tmp_path):
    store.upsert(_make_record("/lustre/aifs/proj_A", 20001))
    data = json.loads((tmp_path / "assignments.json").read_text())
    assert "assignments" in data
    assert "/lustre/aifs/proj_A" in data["assignments"]


# ── Filter ────────────────────────────────────────────────────────────────────

def test_filter_by_fsname(store):
    store.upsert(_make_record("/lustre/aifs/proj_A", 20001, fsname="aifs"))
    store.upsert(_make_record("/lustre/scratch/proj_B", 30001, fsname="scratch"))
    result = store.filter(fsname="aifs")
    assert len(result) == 1
    assert result[0].fsname == "aifs"


def test_filter_by_tier(store):
    store.upsert(_make_record("/lustre/aifs/proj_A", 20001, tier=1))
    store.upsert(_make_record("/lustre/aifs/proj_A/user1", 20002, tier=2))
    store.upsert(_make_record("/lustre/aifs/other", 99001, tier=0))
    assert len(store.filter(tier=1)) == 1
    assert len(store.filter(tier=2)) == 1
    assert len(store.filter(tier=0)) == 1


def test_filter_by_governing_lqa(store):
    store.upsert(_make_record("/lustre/aifs/proj_A", 20001, tier=1, governing_lqa="proj_A"))
    store.upsert(_make_record("/lustre/aifs/proj_B", 30001, tier=1, governing_lqa="proj_B"))
    result = store.filter(governing_lqa="proj_A")
    assert len(result) == 1 and result[0].governing_lqa == "proj_A"


def test_filter_by_path_prefix(store):
    store.upsert(_make_record("/lustre/aifs/proj_A", 20001))
    store.upsert(_make_record("/lustre/aifs/proj_A/user1", 20002))
    store.upsert(_make_record("/lustre/aifs/proj_B", 30001))
    result = store.filter(path_prefix="/lustre/aifs/proj_A")
    paths = {r.path for r in result}
    assert "/lustre/aifs/proj_A" in paths
    assert "/lustre/aifs/proj_A/user1" in paths
    assert "/lustre/aifs/proj_B" not in paths


# ── get_l2_children ───────────────────────────────────────────────────────────

def test_get_l2_children(store):
    store.upsert(_make_record(
        "/lustre/aifs/proj_A/user1", 20002, tier=2,
        l1_parent_path="/lustre/aifs/proj_A",
    ))
    store.upsert(_make_record(
        "/lustre/aifs/proj_A/user2", 20003, tier=2,
        l1_parent_path="/lustre/aifs/proj_A",
    ))
    store.upsert(_make_record("/lustre/aifs/proj_B", 30001, tier=1))
    children = store.get_l2_children("/lustre/aifs/proj_A")
    assert len(children) == 2


# ── sync_lqa_ranges ───────────────────────────────────────────────────────────

def test_sync_lqa_ranges(store):
    ranges = [LqaRangeRecord(start=20001, end=30000)]
    store.upsert(_make_record(
        "/lustre/aifs/proj_A", 20001, tier=1,
        governing_lqa="proj_A", lqa_ranges=ranges,
    ))
    new_ranges = [
        {"start": 20001, "end": 30000},
        {"start": 40001, "end": 50000},
    ]
    updated = store.sync_lqa_ranges("aifs", "proj_A", new_ranges)
    assert updated == 1
    fetched = store.get("/lustre/aifs/proj_A")
    assert len(fetched.lqa_ranges) == 2
    assert fetched.lqa_ranges[1].end == 50000


def test_sync_lqa_ranges_only_affects_matching_fsname(store):
    ranges = [LqaRangeRecord(start=20001, end=30000)]
    store.upsert(_make_record(
        "/lustre/aifs/proj_A", 20001, tier=1, fsname="aifs",
        governing_lqa="proj_A", lqa_ranges=ranges,
    ))
    store.upsert(_make_record(
        "/lustre/scratch/proj_A", 20001, tier=1, fsname="scratch",
        governing_lqa="proj_A", lqa_ranges=ranges,
    ))
    updated = store.sync_lqa_ranges("aifs", "proj_A", [{"start": 1, "end": 2}])
    assert updated == 1


# ── on_lqa_destroyed ──────────────────────────────────────────────────────────

def test_on_lqa_destroyed(store):
    ranges = [LqaRangeRecord(start=20001, end=30000)]
    store.upsert(_make_record(
        "/lustre/aifs/proj_A", 20001, tier=1,
        governing_lqa="proj_A", lqa_ranges=ranges,
    ))
    store.upsert(_make_record(
        "/lustre/aifs/proj_A/user1", 20002, tier=2,
        governing_lqa="proj_A",
    ))
    updated = store.on_lqa_destroyed("aifs", "proj_A")
    assert updated == 2
    for path in ["/lustre/aifs/proj_A", "/lustre/aifs/proj_A/user1"]:
        rec = store.get(path)
        assert rec.governing_lqa is None
        assert rec.tier == 0
        assert rec.lqa_ranges is None
