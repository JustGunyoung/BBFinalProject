"""Reading and writing the data files.

history.jsonl grows by one line per snapshot. cache.json is replaced whole, via
a temp file and os.replace, so a reader never sees a half-written file.
"""

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)


def append_history(path, snap: dict) -> None:
    """Append one snapshot as one JSON line and force it to disk."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(snap, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_history(path) -> list:
    """All snapshots in the file. Blank and unparseable lines are skipped
    (a crash can leave a half-written last line). A missing file gives []."""
    path = Path(path)
    if not path.exists():
        return []
    history = []
    with open(path, encoding="utf-8") as f:
        for number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                snap = json.loads(line)
            except json.JSONDecodeError:
                log.warning("skipping unreadable line %d of %s", number, path)
                continue
            if isinstance(snap, dict):
                history.append(snap)
            else:
                log.warning("skipping line %d of %s: not an object", number, path)
    return history


def last_snapshot(path) -> dict | None:
    """The newest snapshot in the file, or None if there is none."""
    history = read_history(path)
    return history[-1] if history else None


def write_cache(path, obj: dict, attempts: int = 5) -> None:
    """Write obj as JSON to path atomically.

    On Windows os.replace raises PermissionError while a reader has the file
    open, so it is retried a few times with a short sleep.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05 * (attempt + 1))
