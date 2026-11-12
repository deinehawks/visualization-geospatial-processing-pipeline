from __future__ import annotations

import logging

import pytest

from modules.webodm.webodm_processor import (
    WebODMInputValidationError,
    WebODMProcessor,
    build_webodm_upload_manifest,
    parse_first_webodm_root_image_count,
)


def _write(path, payload: bytes) -> None:
    path.write_bytes(payload)


def test_upload_manifest_is_deterministic_and_accepts_mixed_jpeg_extensions(tmp_path):
    _write(tmp_path / "B.JPEG", b"bbb")
    _write(tmp_path / "a.jpg", b"a")
    first = build_webodm_upload_manifest(tmp_path)
    second = build_webodm_upload_manifest(tmp_path)

    assert first.persisted() == second.persisted()
    assert first.count == 2
    assert first.total_bytes == 4
    assert [path.name for path in first.files] == ["a.jpg", "B.JPEG"]


def test_upload_manifest_rejects_case_insensitive_duplicate_names(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _write(first / "image.jpg", b"one")
    _write(second / "IMAGE.JPG", b"two")

    with pytest.raises(ValueError, match="Duplicate case-insensitive"):
        build_webodm_upload_manifest(tmp_path, recursive=True)


def test_upload_manifest_detects_mutation_before_upload(tmp_path):
    image = tmp_path / "image.jpg"
    _write(image, b"first")
    before = build_webodm_upload_manifest(tmp_path)
    _write(image, b"changed-size")

    assert build_webodm_upload_manifest(tmp_path) != before


def test_root_count_parser_uses_first_loading_line_and_ignores_submodels():
    output = """
ODM root
Loading 4,288 images
submodels/submodel_0007
Loading 950 images
"""

    assert parse_first_webodm_root_image_count(output) == 4288


class _GuardProcessor(WebODMProcessor):
    def __init__(self, statuses, output, *, cancel_fails=False):
        self.statuses = list(statuses)
        self.output = output
        self.cancel_fails = cancel_fails
        self.cancel_calls = []
        self.logger = logging.getLogger("tests.webodm-input-guard")

    def get_task(self, project_id, task_id, **kwargs):
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return {"id": task_id, "status": status, "images_count": 3}

    def get_task_output(self, project_id, task_id):
        return self.output

    def cancel_task(self, project_id, task_id):
        self.cancel_calls.append((project_id, task_id))
        if self.cancel_fails:
            raise RuntimeError("controlled cancel failure")
        self.statuses = ["canceled"]


def test_authoritative_exact_count_is_verified_once():
    processor = _GuardProcessor(["running", "completed"], "Loading 3 images")
    verified = []

    success, _runtime, _info = processor.wait_for_completion(
        563,
        "replacement-task",
        poll_seconds=0,
        expected_image_count=3,
        require_authoritative_image_count=True,
        input_verified_callback=verified.append,
    )

    assert success is True
    assert verified == [3]
    assert processor.cancel_calls == []


def test_authoritative_mismatch_cancels_and_blocks_replay(caplog):
    processor = _GuardProcessor(
        ["running"],
        "Loading 4 images\nSECRET-TOKEN-OUTPUT",
    )

    with caplog.at_level(logging.ERROR), pytest.raises(
        WebODMInputValidationError,
        match="outcome=confirmed",
    ):
        processor.wait_for_completion(
            563,
            "replacement-task",
            poll_seconds=0,
            expected_image_count=3,
            require_authoritative_image_count=True,
            cancel_confirmation_timeout_seconds=0,
        )

    assert processor.cancel_calls == [(563, "replacement-task")]
    assert "SECRET-TOKEN-OUTPUT" not in caplog.text


def test_missing_authoritative_count_times_out_and_cancels():
    processor = _GuardProcessor(["running"], "still preparing")

    with pytest.raises(WebODMInputValidationError, match="outcome=confirmed"):
        processor.wait_for_completion(
            563,
            "replacement-task",
            poll_seconds=0,
            expected_image_count=3,
            require_authoritative_image_count=True,
            input_count_timeout_seconds=0,
            cancel_confirmation_timeout_seconds=0,
        )


def test_cancellation_failure_is_reported_as_uncertain():
    processor = _GuardProcessor(
        ["running"],
        "Loading 4 images",
        cancel_fails=True,
    )

    with pytest.raises(WebODMInputValidationError, match="outcome=uncertain"):
        processor.wait_for_completion(
            563,
            "replacement-task",
            poll_seconds=0,
            expected_image_count=3,
            require_authoritative_image_count=True,
            cancel_confirmation_timeout_seconds=0,
        )
