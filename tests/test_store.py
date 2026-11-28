"""History file and cache writer."""

import json

from tracker import store


def test_missing_file_gives_empty_list(tmp_path):
    assert store.read_history(tmp_path / "nope.jsonl") == []
    assert store.last_snapshot(tmp_path / "nope.jsonl") is None


def test_append_creates_parent_and_round_trips(tmp_path):
    path = tmp_path / "data" / "history.jsonl"
    store.append_history(path, {"block": 1, "big": "123456789012345678901234567890"})
    store.append_history(path, {"block": 2})
    assert [s["block"] for s in store.read_history(path)] == [1, 2]
    assert store.last_snapshot(path) == {"block": 2}
    assert len(path.read_text().splitlines()) == 2


def test_half_written_last_line_is_skipped(tmp_path):
    path = tmp_path / "history.jsonl"
    store.append_history(path, {"block": 1})
    with open(path, "a", encoding="utf-8") as f:
        f.write('\n{"block": 2, "pools": [{"fee"')  # crash in the middle of a line
    assert store.read_history(path) == [{"block": 1}]


def test_blank_lines_are_skipped(tmp_path):
    path = tmp_path / "history.jsonl"
    path.write_text('{"block": 1}\n\n   \n{"block": 2}\n', encoding="utf-8")
    assert len(store.read_history(path)) == 2


def test_write_cache_is_atomic_and_leaves_no_tmp(tmp_path):
    path = tmp_path / "data" / "cache.json"
    store.write_cache(path, {"a": 1})
    store.write_cache(path, {"a": 2})
    assert json.loads(path.read_text()) == {"a": 2}
    assert [p.name for p in path.parent.iterdir()] == ["cache.json"]


def test_write_cache_retries_permission_error(tmp_path, monkeypatch):
    path = tmp_path / "cache.json"
    real_replace = store.os.replace
    failures = []

    def flaky(src, dst):
        if len(failures) < 2:
            failures.append(1)
            raise PermissionError("in use")
        real_replace(src, dst)

    monkeypatch.setattr(store.os, "replace", flaky)
    monkeypatch.setattr(store.time, "sleep", lambda s: None)
    store.write_cache(path, {"ok": True})
    assert json.loads(path.read_text()) == {"ok": True}
    assert len(failures) == 2
