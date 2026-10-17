"""Decision records, one JSON object per line, append-only."""

from __future__ import annotations

import json
from pathlib import Path


def records_path(data_dir: Path) -> Path:
    return data_dir / "records.jsonl"


def append(data_dir: Path, record: dict) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    with records_path(data_dir).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def load(data_dir: Path) -> list[dict]:
    path = records_path(data_dir)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
