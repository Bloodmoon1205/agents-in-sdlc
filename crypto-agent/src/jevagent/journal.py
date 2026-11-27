"""Append-only, hash-chained JSONL journal. Every decision, order, fill and
verdict is written here; the nightly review reads only this."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


class Journal:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._prev = "0" * 64

    def _file(self, ts_ms: int) -> Path:
        return self.root / (datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d") + ".jsonl")

    def write(self, kind: str, ts_ms: int, **data) -> None:
        rec = {"kind": kind, "ts_ms": ts_ms, **data, "prev": self._prev}
        line = json.dumps(rec, separators=(",", ":"), sort_keys=True, default=str)
        self._prev = hashlib.sha256(line.encode()).hexdigest()
        with self._file(ts_ms).open("a") as f:
            f.write(line + "\n")

    @staticmethod
    def read(path: Path, anchor: str | None = None) -> list[dict]:
        """Verify the chain inside one daily file. The chain continues across
        files, so the first record's `prev` is the anchor unless one is given."""
        out, prev = [], anchor
        for line in Path(path).read_text().splitlines():
            rec = json.loads(line)
            if prev is None:
                prev = rec["prev"]
            if rec["prev"] != prev:
                raise ValueError(f"journal hash chain broken in {path}")
            prev = hashlib.sha256(line.encode()).hexdigest()
            out.append(rec)
        return out


def read_all(root: Path) -> list[dict]:
    """Read every daily file in order and verify the chain across file boundaries."""
    out, anchor = [], "0" * 64
    for f in sorted(Path(root).glob("*.jsonl")):
        recs = Journal.read(f, anchor)
        if recs:
            anchor = hashlib.sha256(Path(f).read_text().splitlines()[-1].encode()).hexdigest()
        out += recs
    return out
