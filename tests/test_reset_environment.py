from pathlib import Path

import pytest

from tools.reset_environment import (
    REPOSITORY_ROOT, SENTINEL_NAME, _require_within, main,
    reset_environment, validate_target_root,
)


def approved_root(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / SENTINEL_NAME).write_text("test-owned", encoding="utf-8")
    return root


def test_reset_removes_only_logs_and_pipeline_sqlite_files(tmp_path):
    root = approved_root(tmp_path)
    logs = root / "logs"
    logs.mkdir()
    (logs / "run.log").write_text("log", encoding="utf-8")
    for name in ("pipeline.db", "pipeline.db-wal", "pipeline.db-shm"):
        (root / name).write_text("sqlite", encoding="utf-8")
    unrelated = root / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")
    removed = reset_environment(root)
    assert {path.name for path in removed} == {"logs", "pipeline.db", "pipeline.db-wal", "pipeline.db-shm"}
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert (root / SENTINEL_NAME).is_file()


def test_reset_requires_explicit_destructive_flag(tmp_path):
    root = approved_root(tmp_path)
    with pytest.raises(SystemExit, match="--allow-destructive-reset"):
        main([str(root)])


def test_reset_rejects_missing_sentinel(tmp_path):
    with pytest.raises(ValueError, match="sentinel"):
        validate_target_root(tmp_path)


def test_reset_rejects_repository_root():
    with pytest.raises(ValueError, match="Repository root"):
        validate_target_root(REPOSITORY_ROOT)


def test_reset_rejects_filesystem_root():
    with pytest.raises(ValueError, match="Filesystem root"):
        validate_target_root(Path(REPOSITORY_ROOT.anchor))


def test_containment_guard_rejects_path_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ValueError, match="escapes"):
        _require_within(root / ".." / "outside", root.resolve())
