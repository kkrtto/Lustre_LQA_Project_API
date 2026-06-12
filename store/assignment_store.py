import functools
import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from schemas.assignment import AssignmentRecord, LqaRangeRecord
from config import get_settings


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AssignmentStore:
    """
    Thread-safe JSON-backed store for directory project assignment records.

    File format:
    {
      "version": "1.0",
      "last_updated": "<ISO-8601>",
      "assignments": {
        "/path/to/dir": { ...AssignmentRecord fields... }
      }
    }

    Writes are atomic (write to .tmp then os.replace) to prevent corruption.
    """

    def __init__(self, file_path: str) -> None:
        self._path = file_path
        self._lock = threading.RLock()
        self._ensure_file()

    def _ensure_file(self) -> None:
        if not os.path.exists(self._path):
            os.makedirs(
                os.path.dirname(os.path.abspath(self._path)), exist_ok=True
            )
            self._write({"version": "1.0", "assignments": {}})

    # ── Low-level I/O ─────────────────────────────────────────────────────────

    def _read(self) -> Dict[str, Any]:
        with self._lock:
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                return {"version": "1.0", "assignments": {}}

    def _write(self, data: Dict[str, Any]) -> None:
        data["last_updated"] = _utcnow().isoformat()
        tmp = self._path + ".tmp"
        with self._lock:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp, self._path)  # atomic on POSIX

    # ── Deserialisation ────────────────────────────────────────────────────────

    @staticmethod
    def _to_record(d: Dict[str, Any]) -> AssignmentRecord:
        ranges = None
        if d.get("lqa_ranges"):
            ranges = [
                LqaRangeRecord(start=r["start"], end=r["end"])
                for r in d["lqa_ranges"]
            ]
        return AssignmentRecord(
            path=d["path"],
            fsname=d["fsname"],
            projid=d["projid"],
            parent_path=d["parent_path"],
            parent_projid=d["parent_projid"],
            governing_lqa=d.get("governing_lqa"),
            lqa_ranges=ranges,
            l1_parent_path=d.get("l1_parent_path"),
            tier=d["tier"],
            created_at=datetime.fromisoformat(d["created_at"]),
            updated_at=datetime.fromisoformat(d["updated_at"]),
        )

    @staticmethod
    def _from_record(record: AssignmentRecord) -> Dict[str, Any]:
        d = record.model_dump()
        if record.lqa_ranges is not None:
            d["lqa_ranges"] = [
                {"start": r.start, "end": r.end} for r in record.lqa_ranges
            ]
        d["created_at"] = record.created_at.isoformat()
        d["updated_at"] = record.updated_at.isoformat()
        return d

    # ── Public CRUD ───────────────────────────────────────────────────────────

    def get_all(self) -> Dict[str, AssignmentRecord]:
        data = self._read()
        result: Dict[str, AssignmentRecord] = {}
        for path, raw in data.get("assignments", {}).items():
            try:
                result[path] = self._to_record(raw)
            except Exception:
                continue  # skip malformed entries
        return result

    def get(self, path: str) -> Optional[AssignmentRecord]:
        data = self._read()
        raw = data.get("assignments", {}).get(path)
        if raw is None:
            return None
        try:
            return self._to_record(raw)
        except Exception:
            return None

    def upsert(self, record: AssignmentRecord) -> None:
        with self._lock:
            data = self._read()
            assignments = data.setdefault("assignments", {})
            existing = assignments.get(record.path)
            rec_dict = self._from_record(record)
            # Preserve original created_at
            if existing:
                rec_dict["created_at"] = existing["created_at"]
            rec_dict["updated_at"] = _utcnow().isoformat()
            assignments[record.path] = rec_dict
            self._write(data)

    def delete(self, path: str) -> bool:
        with self._lock:
            data = self._read()
            assignments = data.get("assignments", {})
            if path not in assignments:
                return False
            del assignments[path]
            self._write(data)
            return True

    # ── Filtered queries ──────────────────────────────────────────────────────

    def filter(
        self,
        fsname: Optional[str] = None,
        tier: Optional[int] = None,
        governing_lqa: Optional[str] = None,
        path_prefix: Optional[str] = None,
        parent_path: Optional[str] = None,
        l1_parent_path: Optional[str] = None,
    ) -> List[AssignmentRecord]:
        result = []
        for record in self.get_all().values():
            if fsname is not None and record.fsname != fsname:
                continue
            if tier is not None and record.tier != tier:
                continue
            if governing_lqa is not None and record.governing_lqa != governing_lqa:
                continue
            if path_prefix is not None and not record.path.startswith(path_prefix):
                continue
            if parent_path is not None and record.parent_path != parent_path:
                continue
            if l1_parent_path is not None and record.l1_parent_path != l1_parent_path:
                continue
            result.append(record)
        return result

    def get_l2_children(self, l1_path: str) -> List[AssignmentRecord]:
        return self.filter(l1_parent_path=l1_path)

    # ── LQA-change hooks ──────────────────────────────────────────────────────

    def sync_lqa_ranges(
        self,
        fsname: str,
        lqa_name: str,
        new_ranges: List[Dict[str, int]],
    ) -> int:
        """
        After an LQA range is added or removed, update the stored ranges
        snapshot for all tier=1 records governed by that LQA.
        Returns the number of records updated.
        """
        with self._lock:
            data = self._read()
            assignments = data.get("assignments", {})
            updated = 0
            for rec in assignments.values():
                if (
                    rec.get("fsname") == fsname
                    and rec.get("governing_lqa") == lqa_name
                    and rec.get("tier") == 1
                ):
                    rec["lqa_ranges"] = new_ranges
                    rec["updated_at"] = _utcnow().isoformat()
                    updated += 1
            if updated:
                self._write(data)
            return updated

    def on_lqa_destroyed(self, fsname: str, lqa_name: str) -> int:
        """
        When an LQA is destroyed, demote all its governed records to tier=0
        and clear the governing_lqa / lqa_ranges fields.
        Returns the number of records updated.
        """
        with self._lock:
            data = self._read()
            assignments = data.get("assignments", {})
            updated = 0
            for rec in assignments.values():
                if (
                    rec.get("fsname") == fsname
                    and rec.get("governing_lqa") == lqa_name
                ):
                    rec["governing_lqa"] = None
                    rec["lqa_ranges"] = None
                    rec["tier"] = 0
                    rec["updated_at"] = _utcnow().isoformat()
                    updated += 1
            if updated:
                self._write(data)
            return updated


@functools.lru_cache(maxsize=1)
def get_store() -> AssignmentStore:
    settings = get_settings()
    return AssignmentStore(settings.assignment_store_path)
