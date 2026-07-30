import json
from pathlib import Path

import pytest

from shared.filesystem_validation import (
    FILESYSTEM_VALIDATION_SENTINEL,
    validate_publication_filesystem,
    write_filesystem_validation_report,
)
from tools.filesystem_validation import main as filesystem_validation_cli_main


def _validation_root(tmp_path):
    root = tmp_path / "validation-root"
    root.mkdir()
    (root / FILESYSTEM_VALIDATION_SENTINEL).write_text("owned by pytest", encoding="utf-8")
    return root


def test_filesystem_validation_requires_explicit_allow_and_sentinel(tmp_path):
    root = tmp_path / "validation-root"
    root.mkdir()

    with pytest.raises(ValueError, match="allow_destructive_validation"):
        validate_publication_filesystem(
            root,
            validation_id="validation-001",
        )

    with pytest.raises(ValueError, match="missing sentinel"):
        validate_publication_filesystem(
            root,
            validation_id="validation-001",
            allow_destructive_validation=True,
        )


def test_filesystem_validation_runs_under_disposable_root_and_cleans_up(tmp_path):
    root = _validation_root(tmp_path)

    result = validate_publication_filesystem(
        root,
        validation_id="validation-001",
        allow_destructive_validation=True,
    )

    assert result.passed is True
    assert [check.name for check in result.checks] == [
        "exclusive_create",
        "file_replace",
        "directory_rename",
        "json_visibility",
    ]
    assert not result.run_root.exists()
    assert (root / FILESYSTEM_VALIDATION_SENTINEL).is_file()


def test_filesystem_validation_large_tree_check_is_optional_and_counted(tmp_path):
    root = _validation_root(tmp_path)

    result = validate_publication_filesystem(
        root,
        validation_id="validation-large-tree",
        allow_destructive_validation=True,
        large_tree_files=12,
    )

    assert result.passed is True
    large_tree = next(check for check in result.checks if check.name == "large_tree_rename")
    assert large_tree.status == "passed"
    assert large_tree.details["file_count"] == 12
    assert large_tree.details["observed_file_count"] == 12
    assert "rename_elapsed_seconds" in large_tree.details
    assert not result.run_root.exists()


def test_filesystem_validation_rejects_negative_large_tree_count(tmp_path):
    root = _validation_root(tmp_path)

    with pytest.raises(ValueError, match="large_tree_files"):
        validate_publication_filesystem(
            root,
            validation_id="validation-negative-large-tree",
            allow_destructive_validation=True,
            large_tree_files=-1,
        )

def test_filesystem_validation_can_keep_workdir_and_write_report(tmp_path):
    root = _validation_root(tmp_path)

    result = validate_publication_filesystem(
        root,
        validation_id="validation-keep",
        allow_destructive_validation=True,
        keep_workdir=True,
    )
    report_path = root / "reports" / "validation-keep.json"
    write_filesystem_validation_report(result, report_path)

    assert result.passed is True
    assert result.run_root.is_dir()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["run_root"] == str(result.run_root)


def test_filesystem_validation_records_failed_rename_without_exception(tmp_path):
    root = _validation_root(tmp_path)

    def failing_rename(source, destination):
        raise OSError("rename unavailable")

    result = validate_publication_filesystem(
        root,
        validation_id="validation-fail",
        allow_destructive_validation=True,
        rename_path=failing_rename,
    )

    assert result.passed is False
    directory_check = next(check for check in result.checks if check.name == "directory_rename")
    assert directory_check.status == "failed"
    assert "rename unavailable" in directory_check.error
    assert not result.run_root.exists()


def test_filesystem_validation_cli_requires_ack_and_outputs_json(tmp_path, capsys):
    root = _validation_root(tmp_path)
    args = [
        "--root", str(root),
        "--validation-id", "cli-validation",
    ]

    assert filesystem_validation_cli_main(args) == 2
    missing_ack = json.loads(capsys.readouterr().out)
    assert missing_ack["status"] == "error"
    assert "allow_destructive_validation" in missing_ack["error"]

    report_path = root / "reports" / "cli-validation.json"
    assert filesystem_validation_cli_main(
        args
        + [
            "--allow-destructive-validation",
            "--large-tree-files", "3",
            "--report-path", str(report_path),
        ]
    ) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "passed"
    assert any(check["name"] == "large_tree_rename" for check in output["checks"])
    assert output["report_path"] == str(report_path)
    assert report_path.is_file()
