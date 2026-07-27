import json
import os
from pathlib import Path

import pytest

from shared.publication_lock import (
    PUBLICATION_LOCK_NAME,
    PUBLICATION_LOCK_RECOVERY_DIR,
    PublicationLockFormatError,
    PublicationLockNotFoundError,
    PublicationLockRecoveryError,
    acquire_publication_lock,
    diagnose_publication_lock,
    recover_publication_lock,
)
from tools.publication_lock_recovery import main as recovery_cli_main


@pytest.fixture
def publication_root(tmp_path):
    sentinel = tmp_path / ".pytest-owned-publication-lock-recovery-root"
    sentinel.write_text("owned by pytest", encoding="utf-8")
    root = tmp_path / "surveys" / "2026" / "AH-026019" / "rgb"
    return root, sentinel


def _diagnosed_lock(root, *, created_at="2026-07-20T10:00:00+00:00"):
    lock = acquire_publication_lock(
        published_root=root,
        run_id="run-001",
        survey_id="AH-026019",
        owner_token="secret-owner-token",
        created_at=created_at,
    )
    return lock, diagnose_publication_lock(
        root,
        observed_at="2026-07-20T11:00:00+00:00",
    )


def test_diagnosis_is_read_only_and_age_is_diagnostic(publication_root):
    root, sentinel = publication_root
    lock, diagnosis = _diagnosed_lock(root)
    original = lock.path.read_bytes()

    assert diagnosis.status == "valid"
    assert diagnosis.lock == lock
    assert diagnosis.created_at_valid is True
    assert diagnosis.age_seconds == 3600
    assert diagnosis.recovery_confirmation.endswith(diagnosis.raw_sha256)
    assert lock.path.read_bytes() == original
    assert not (root / PUBLICATION_LOCK_RECOVERY_DIR).exists()
    assert sentinel.is_file()


def test_age_alone_never_authorizes_recovery(publication_root):
    root, sentinel = publication_root
    lock, diagnosis = _diagnosed_lock(root, created_at="2020-01-01T00:00:00+00:00")

    with pytest.raises(PublicationLockRecoveryError, match="confirmation"):
        recover_publication_lock(
            diagnosis=diagnosis,
            confirmation="RECOVER BECAUSE OLD",
            reason="Owner verified stopped",
        )

    assert lock.path.is_file()
    assert sentinel.is_file()


def test_invalid_created_at_never_produces_trusted_age(publication_root):
    root, _ = publication_root
    lock, diagnosis = _diagnosed_lock(root, created_at="not-a-timestamp")

    assert diagnosis.status == "valid"
    assert diagnosis.lock == lock
    assert diagnosis.created_at_valid is False
    assert diagnosis.age_seconds is None


def test_explicit_recovery_archives_exact_evidence_and_audit(publication_root):
    root, sentinel = publication_root
    lock, diagnosis = _diagnosed_lock(root)
    original = lock.path.read_bytes()

    recovery = recover_publication_lock(
        diagnosis=diagnosis,
        confirmation=diagnosis.recovery_confirmation,
        reason="Operator verified the original process stopped",
        recovery_id="recovery-001",
        recovered_at="2026-07-20T12:00:00+00:00",
    )

    assert not lock.path.exists()
    assert recovery.evidence_path.read_bytes() == original
    record_text = recovery.record_path.read_text(encoding="utf-8")
    record = json.loads(record_text)
    assert record["reason"] == "Operator verified the original process stopped"
    assert record["lock_sha256"] == diagnosis.raw_sha256
    assert record["run_id"] == "run-001"
    assert record["owner_token_sha256"]
    assert "secret-owner-token" not in record_text
    assert sentinel.is_file()


@pytest.mark.parametrize(
    ("confirmation", "reason", "message"),
    [
        ("wrong confirmation", "Verified stopped", "confirmation"),
        (None, "Verified stopped", "confirmation"),
        ("expected", "", "reason"),
    ],
)
def test_recovery_requires_confirmation_and_reason(
    publication_root, confirmation, reason, message
):
    root, sentinel = publication_root
    lock, diagnosis = _diagnosed_lock(root)
    supplied = diagnosis.recovery_confirmation if confirmation == "expected" else confirmation

    with pytest.raises((PublicationLockRecoveryError, ValueError), match=message):
        recover_publication_lock(
            diagnosis=diagnosis,
            confirmation=supplied,
            reason=reason,
        )

    assert lock.path.is_file()
    assert sentinel.is_file()


def test_changed_lock_after_diagnosis_fails_closed(publication_root):
    root, sentinel = publication_root
    lock, diagnosis = _diagnosed_lock(root)
    payload = json.loads(lock.path.read_text(encoding="utf-8"))
    payload["run_id"] = "run-002"
    lock.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PublicationLockRecoveryError, match="changed after diagnosis"):
        recover_publication_lock(
            diagnosis=diagnosis,
            confirmation=diagnosis.recovery_confirmation,
            reason="Owner verified stopped",
        )

    assert json.loads(lock.path.read_text(encoding="utf-8"))["run_id"] == "run-002"
    assert sentinel.is_file()


def test_malformed_lock_can_only_be_explicitly_archived(publication_root):
    root, sentinel = publication_root
    root.mkdir(parents=True)
    lock_path = root / PUBLICATION_LOCK_NAME
    lock_path.write_bytes(b"{not-json")
    diagnosis = diagnose_publication_lock(root)

    assert diagnosis.status == "malformed"
    assert diagnosis.lock is None
    assert diagnosis.recovery_confirmation.startswith("RECOVER MALFORMED ")

    recovery = recover_publication_lock(
        diagnosis=diagnosis,
        confirmation=diagnosis.recovery_confirmation,
        reason="Operator inspected malformed evidence and verified no active owner",
        recovery_id="malformed-001",
    )

    assert not lock_path.exists()
    assert recovery.evidence_path.read_bytes() == b"{not-json"
    assert sentinel.is_file()


def test_race_during_quarantine_restores_changed_lock(publication_root):
    root, sentinel = publication_root
    lock, diagnosis = _diagnosed_lock(root)

    def racing_rename(source, destination):
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["run_id"] = "run-raced"
        source.write_text(json.dumps(payload), encoding="utf-8")
        os.rename(source, destination)

    with pytest.raises(PublicationLockRecoveryError, match="changed during"):
        recover_publication_lock(
            diagnosis=diagnosis,
            confirmation=diagnosis.recovery_confirmation,
            reason="Owner verified stopped",
            recovery_id="race-001",
            rename_path=racing_rename,
        )

    assert lock.path.is_file()
    assert json.loads(lock.path.read_text(encoding="utf-8"))["run_id"] == "run-raced"
    assert not (root / PUBLICATION_LOCK_RECOVERY_DIR / "race-001.lock.json").exists()
    assert sentinel.is_file()


def test_recovery_id_collision_preserves_lock(publication_root):
    root, sentinel = publication_root
    lock, diagnosis = _diagnosed_lock(root)
    evidence_root = root / PUBLICATION_LOCK_RECOVERY_DIR
    evidence_root.mkdir()
    existing = evidence_root / "existing.lock.json"
    existing.write_text("existing evidence", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exists"):
        recover_publication_lock(
            diagnosis=diagnosis,
            confirmation=diagnosis.recovery_confirmation,
            reason="Owner verified stopped",
            recovery_id="existing",
        )

    assert lock.path.is_file()
    assert existing.read_text(encoding="utf-8") == "existing evidence"
    assert sentinel.is_file()


def test_diagnosis_rejects_missing_and_symlink_locks(publication_root):
    root, sentinel = publication_root
    root.mkdir(parents=True)
    with pytest.raises(PublicationLockNotFoundError):
        diagnose_publication_lock(root)

    target = root / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = root / PUBLICATION_LOCK_NAME
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(PublicationLockFormatError, match="symbolic link|reparse point"):
        diagnose_publication_lock(root)
    assert sentinel.is_file()


def test_cli_requires_acknowledgement_and_supports_explicit_recovery(
    publication_root, capsys
):
    root, sentinel = publication_root
    lock, diagnosis = _diagnosed_lock(root)

    assert recovery_cli_main(["diagnose", "--published-root", str(root)]) == 0
    diagnosed_output = json.loads(capsys.readouterr().out)
    assert diagnosed_output["lock_sha256"] == diagnosis.raw_sha256
    assert lock.path.is_file()

    common = [
        "recover",
        "--published-root", str(root),
        "--expected-digest", diagnosis.raw_sha256,
        "--confirmation", diagnosis.recovery_confirmation,
        "--reason", "Operator verified process stopped",
        "--recovery-id", "cli-001",
    ]
    assert recovery_cli_main(common) == 2
    assert "--allow-recovery" in capsys.readouterr().out
    assert lock.path.is_file()

    assert recovery_cli_main(common + ["--allow-recovery"]) == 0
    recovered_output = json.loads(capsys.readouterr().out)
    assert recovered_output["status"] == "recovered"
    assert not lock.path.exists()
    assert Path(recovered_output["evidence_path"]).is_file()
    assert Path(recovered_output["record_path"]).is_file()
    assert sentinel.is_file()
