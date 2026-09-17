from __future__ import annotations

import hashlib
import shutil

import pytest

from shared.raw_input_recovery import (
    RAW_RECOVERY_OWNERSHIP_NAME,
    recover_missing_raw_inputs,
)


def _paths(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    images = tmp_path / "surveys" / "2026" / "AH-026016" / "rgb" / "images"
    images.mkdir(parents=True)
    return source, images / "raw", images


def test_recovery_stages_verifies_and_atomically_installs_raw(tmp_path):
    source, raw_dir, images_dir = _paths(tmp_path)
    nested = source / "capture"
    nested.mkdir()
    (source / "image-001.jpg").write_bytes(b"one")
    (nested / "image-002.jpeg").write_bytes(b"two-two")
    path_dir = images_dir / "path"
    excluded_dir = images_dir / "cross-runs"
    path_dir.mkdir()
    excluded_dir.mkdir()
    (path_dir / "existing.jpg").write_bytes(b"keep-path")
    (excluded_dir / "excluded.jpg").write_bytes(b"keep-excluded")

    result = recover_missing_raw_inputs(
        source_dir=source,
        raw_dir=raw_dir,
        run_id="run-recovery",
    )

    assert result["status"] == "recovered"
    assert result["changed"] is True
    assert result["image_count"] == 2
    assert result["image_bytes"] == 10
    assert (raw_dir / "image-001.jpg").read_bytes() == b"one"
    assert (raw_dir / "image-002.jpeg").read_bytes() == b"two-two"
    assert (raw_dir / RAW_RECOVERY_OWNERSHIP_NAME).is_file()
    assert (path_dir / "existing.jpg").read_bytes() == b"keep-path"
    assert (excluded_dir / "excluded.jpg").read_bytes() == b"keep-excluded"


def test_recovery_resumes_owned_staging_after_copy_failure(tmp_path):
    source, raw_dir, _images_dir = _paths(tmp_path)
    (source / "image-001.jpg").write_bytes(b"one")
    (source / "image-002.jpg").write_bytes(b"two")
    calls = []

    def fail_second_copy(source_image, destination):
        calls.append(source_image.name)
        if len(calls) == 2:
            raise OSError("injected copy failure")
        return shutil.copy2(source_image, destination)

    with pytest.raises(OSError, match="injected copy failure"):
        recover_missing_raw_inputs(
            source_dir=source,
            raw_dir=raw_dir,
            run_id="run-recovery",
            copy_file=fail_second_copy,
        )

    suffix = hashlib.sha256(b"run-recovery").hexdigest()[:16]
    staging = raw_dir.with_name(f".raw-recovery-{suffix}.tmp")
    assert not raw_dir.exists()
    assert (staging / RAW_RECOVERY_OWNERSHIP_NAME).is_file()
    assert (staging / "image-001.jpg").is_file()

    result = recover_missing_raw_inputs(
        source_dir=source,
        raw_dir=raw_dir,
        run_id="run-recovery",
    )

    assert result["status"] == "recovered"
    assert sorted(path.name for path in raw_dir.glob("*.jpg")) == [
        "image-001.jpg",
        "image-002.jpg",
    ]
    assert not staging.exists()


def test_recovery_refuses_mismatched_nonempty_raw(tmp_path):
    source, raw_dir, _images_dir = _paths(tmp_path)
    (source / "image-001.jpg").write_bytes(b"source")
    raw_dir.mkdir()
    (raw_dir / "different.jpg").write_bytes(b"existing")

    with pytest.raises(ValueError, match="partial, mismatched"):
        recover_missing_raw_inputs(
            source_dir=source,
            raw_dir=raw_dir,
            run_id="run-recovery",
        )

    assert (raw_dir / "different.jpg").read_bytes() == b"existing"


def test_recovery_refuses_flattened_filename_collision_before_writing(tmp_path):
    source, raw_dir, images_dir = _paths(tmp_path)
    (source / "one").mkdir()
    (source / "two").mkdir()
    (source / "one" / "same.jpg").write_bytes(b"one")
    (source / "two" / "SAME.JPG").write_bytes(b"two")

    with pytest.raises(ValueError, match="duplicate destination filenames"):
        recover_missing_raw_inputs(
            source_dir=source,
            raw_dir=raw_dir,
            run_id="run-recovery",
        )

    assert not raw_dir.exists()
    assert list(images_dir.glob(".raw-recovery-*.tmp")) == []


def test_recovery_is_idempotent_when_raw_already_matches(tmp_path):
    source, raw_dir, _images_dir = _paths(tmp_path)
    (source / "image-001.jpg").write_bytes(b"source")
    raw_dir.mkdir()
    (raw_dir / "image-001.jpg").write_bytes(b"source")

    result = recover_missing_raw_inputs(
        source_dir=source,
        raw_dir=raw_dir,
        run_id="run-recovery",
    )

    assert result["status"] == "already_present"
    assert result["changed"] is False
