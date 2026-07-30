from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal


PUBLICATION_LOCK_NAME = ".publication.lock"
PUBLICATION_LOCK_VERSION = 1
PUBLICATION_LOCK_RECOVERY_DIR = ".publication-lock-recovery"


class PublicationLockError(RuntimeError):
    """Base error for publication ownership failures."""


class PublicationLockedError(PublicationLockError):
    """Raised when another or unreadable publication lock already exists."""


class PublicationLockFormatError(PublicationLockError):
    """Raised when a publication lock cannot be trusted."""


class PublicationLockNotFoundError(PublicationLockError):
    """Raised when no publication lock exists to diagnose or recover."""


class PublicationLockRecoveryError(PublicationLockError):
    """Raised when explicit publication-lock recovery cannot proceed safely."""


class PublicationLockOwnershipError(PublicationLockError):
    """Raised when a caller tries to release a lock it does not own."""


@dataclass(frozen=True)
class PublicationLock:
    path: Path
    published_root: Path
    run_id: str
    survey_id: str
    owner_token: str
    created_at: str


@dataclass(frozen=True)
class PublicationLockDiagnosis:
    path: Path
    published_root: Path
    status: Literal["valid", "malformed"]
    raw_sha256: str
    observed_at: str
    lock: PublicationLock | None
    created_at_valid: bool
    age_seconds: float | None
    format_error: str | None
    recovery_confirmation: str


@dataclass(frozen=True)
class PublicationLockRecovery:
    recovery_id: str
    recovered_at: str
    reason: str
    evidence_path: Path
    record_path: Path
    diagnosis: PublicationLockDiagnosis



def diagnose_publication_lock(
    published_root: Path,
    *,
    observed_at: str | None = None,
) -> PublicationLockDiagnosis:
    """Inspect a lock without changing it or declaring it stale by age."""

    root = _resolve_published_root(published_root)
    lock_path = root / PUBLICATION_LOCK_NAME
    raw = _read_lock_bytes(lock_path)
    raw_sha256 = hashlib.sha256(raw).hexdigest()
    safe_observed_at, observed_time = _required_aware_timestamp(
        observed_at or datetime.now(timezone.utc).isoformat(),
        "observed_at",
    )

    lock: PublicationLock | None
    format_error: str | None
    try:
        lock = _parse_publication_lock(raw=raw, root=root)
        status: Literal["valid", "malformed"] = "valid"
        format_error = None
    except PublicationLockFormatError as exc:
        lock = None
        status = "malformed"
        format_error = str(exc)

    created_at_valid = False
    age_seconds: float | None = None
    if lock is not None:
        try:
            _, created_time = _required_aware_timestamp(
                lock.created_at,
                "created_at",
            )
        except ValueError:
            pass
        else:
            created_at_valid = True
            age_seconds = (observed_time - created_time).total_seconds()

    confirmation = _recovery_confirmation(
        status=status,
        raw_sha256=raw_sha256,
        lock=lock,
    )
    return PublicationLockDiagnosis(
        path=lock_path,
        published_root=root,
        status=status,
        raw_sha256=raw_sha256,
        observed_at=safe_observed_at,
        lock=lock,
        created_at_valid=created_at_valid,
        age_seconds=age_seconds,
        format_error=format_error,
        recovery_confirmation=confirmation,
    )


def recover_publication_lock(
    *,
    diagnosis: PublicationLockDiagnosis,
    confirmation: str,
    reason: str,
    recovery_id: str | None = None,
    recovered_at: str | None = None,
    rename_path: Callable[[Path, Path], object] | None = None,
) -> PublicationLockRecovery:
    """Archive an unchanged diagnosed lock after explicit operator approval.

    The lock is moved to retained evidence rather than deleted. Age is never
    sufficient authorization, and this helper does not acquire a replacement
    lock or decide whether the diagnosed owner process is alive.
    """

    root = _resolve_published_root(diagnosis.published_root)
    expected_lock_path = root / PUBLICATION_LOCK_NAME
    if Path(diagnosis.path).resolve(strict=False) != expected_lock_path:
        raise PublicationLockRecoveryError(
            "Diagnosis lock path does not match its published root"
        )

    expected_confirmation = _recovery_confirmation(
        status=diagnosis.status,
        raw_sha256=diagnosis.raw_sha256,
        lock=diagnosis.lock,
    )
    if diagnosis.recovery_confirmation != expected_confirmation:
        raise PublicationLockRecoveryError("Diagnosis confirmation data is inconsistent")
    if confirmation != expected_confirmation:
        raise PublicationLockRecoveryError(
            "Explicit recovery confirmation does not match the diagnosis"
        )

    safe_reason = _required_text(reason, "reason")
    safe_recovery_id = _safe_recovery_id(recovery_id or uuid.uuid4().hex)
    safe_recovered_at, _ = _required_aware_timestamp(
        recovered_at or datetime.now(timezone.utc).isoformat(),
        "recovered_at",
    )

    current = diagnose_publication_lock(
        root,
        observed_at=diagnosis.observed_at,
    )
    if not _diagnosis_identity_matches(diagnosis, current):
        raise PublicationLockRecoveryError(
            "Publication lock changed after diagnosis; refusing recovery"
        )

    evidence_root = root / PUBLICATION_LOCK_RECOVERY_DIR
    evidence_path = evidence_root / f"{safe_recovery_id}.lock.json"
    record_path = evidence_root / f"{safe_recovery_id}.recovery.json"
    if evidence_path.exists() or record_path.exists():
        raise FileExistsError(
            f"Publication lock recovery ID already exists: {safe_recovery_id}"
        )
    evidence_root.mkdir(parents=True, exist_ok=True)

    rename = rename_path or _rename_path
    renamed = False
    try:
        rename(expected_lock_path, evidence_path)
        renamed = True
        archived_raw = _read_evidence_bytes(evidence_path)
        archived_digest = hashlib.sha256(archived_raw).hexdigest()
        if archived_digest != diagnosis.raw_sha256:
            raise PublicationLockRecoveryError(
                "Publication lock changed during recovery quarantine"
            )
        if diagnosis.status == "valid":
            archived_lock = _parse_publication_lock(raw=archived_raw, root=root)
            if archived_lock != diagnosis.lock:
                raise PublicationLockRecoveryError(
                    "Publication lock ownership changed during recovery quarantine"
                )

        record = {
            "recovery_version": 1,
            "recovery_id": safe_recovery_id,
            "recovered_at": safe_recovered_at,
            "reason": safe_reason,
            "published_root": str(root),
            "lock_status": diagnosis.status,
            "lock_sha256": diagnosis.raw_sha256,
            "lock_evidence_path": str(evidence_path),
            "diagnosed_at": diagnosis.observed_at,
            "diagnosed_age_seconds": diagnosis.age_seconds,
            "created_at_valid": diagnosis.created_at_valid,
            "format_error": diagnosis.format_error,
            "run_id": diagnosis.lock.run_id if diagnosis.lock else None,
            "survey_id": diagnosis.lock.survey_id if diagnosis.lock else None,
            "created_at": diagnosis.lock.created_at if diagnosis.lock else None,
            "owner_token_sha256": (
                hashlib.sha256(diagnosis.lock.owner_token.encode("utf-8")).hexdigest()
                if diagnosis.lock
                else None
            ),
        }
        _write_json_exclusive(record_path, record)
    except Exception as recovery_error:
        if renamed and evidence_path.exists():
            try:
                _restore_quarantined_lock(
                    evidence_path=evidence_path,
                    lock_path=expected_lock_path,
                )
            except Exception as restore_error:
                raise PublicationLockRecoveryError(
                    "Publication lock recovery failed and quarantined lock restoration "
                    f"was incomplete: {recovery_error}"
                ) from restore_error
        raise

    return PublicationLockRecovery(
        recovery_id=safe_recovery_id,
        recovered_at=safe_recovered_at,
        reason=safe_reason,
        evidence_path=evidence_path,
        record_path=record_path,
        diagnosis=diagnosis,
    )


def acquire_publication_lock(
    *,
    published_root: Path,
    run_id: str,
    survey_id: str,
    owner_token: str | None = None,
    created_at: str | None = None,
) -> PublicationLock:
    """Atomically claim one survey publication target.

    Existing, malformed, and potentially stale locks all fail closed. This
    helper intentionally does not guess whether another owner is still alive
    and does not automatically steal locks.
    """

    root = _resolve_published_root(published_root)
    safe_run_id = _required_text(run_id, "run_id")
    safe_survey_id = _required_text(survey_id, "survey_id")
    safe_owner_token = _required_text(
        owner_token or uuid.uuid4().hex,
        "owner_token",
    )
    safe_created_at = _required_text(
        created_at or datetime.now(timezone.utc).isoformat(),
        "created_at",
    )

    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / PUBLICATION_LOCK_NAME
    payload = {
        "lock_version": PUBLICATION_LOCK_VERSION,
        "run_id": safe_run_id,
        "survey_id": safe_survey_id,
        "owner_token": safe_owner_token,
        "created_at": safe_created_at,
        "published_root": str(root),
    }

    try:
        descriptor = os.open(
            lock_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError as exc:
        try:
            existing = read_publication_lock(root)
        except PublicationLockError as read_error:
            raise PublicationLockedError(
                f"Publication lock exists but is unreadable or malformed: {lock_path}"
            ) from read_error
        raise PublicationLockedError(
            "Publication target is already locked: "
            f"run_id={existing.run_id} survey_id={existing.survey_id} "
            f"created_at={existing.created_at}"
        ) from exc

    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())

    return PublicationLock(
        path=lock_path,
        published_root=root,
        run_id=safe_run_id,
        survey_id=safe_survey_id,
        owner_token=safe_owner_token,
        created_at=safe_created_at,
    )


def read_publication_lock(published_root: Path) -> PublicationLock:
    """Read and validate the current publication lock without changing it."""

    root = _resolve_published_root(published_root)
    raw = _read_lock_bytes(root / PUBLICATION_LOCK_NAME)
    return _parse_publication_lock(raw=raw, root=root)


def release_publication_lock(lock: PublicationLock) -> None:
    """Release a lock only when the on-disk ownership token still matches."""

    expected_root = _resolve_published_root(lock.published_root)
    expected_path = expected_root / PUBLICATION_LOCK_NAME
    if Path(lock.path).resolve(strict=False) != expected_path:
        raise PublicationLockOwnershipError(
            "Publication lock handle path does not match the published root"
        )

    try:
        current = read_publication_lock(expected_root)
    except PublicationLockError as exc:
        raise PublicationLockOwnershipError(
            "Publication lock cannot be released because its ownership is unreadable"
        ) from exc

    if (
        current.run_id != lock.run_id
        or current.survey_id != lock.survey_id
        or current.owner_token != lock.owner_token
    ):
        raise PublicationLockOwnershipError(
            "Publication lock ownership changed; refusing to release it"
        )

    expected_path.unlink()



def _read_lock_bytes(lock_path: Path) -> bytes:
    try:
        path_stat = lock_path.lstat()
    except FileNotFoundError as exc:
        raise PublicationLockNotFoundError(
            f"Publication lock does not exist: {lock_path}"
        ) from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    file_attributes = getattr(path_stat, "st_file_attributes", 0)
    if stat.S_ISLNK(path_stat.st_mode) or file_attributes & reparse_flag:
        raise PublicationLockFormatError(
            f"Publication lock must not be a symbolic link or reparse point: {lock_path}"
        )
    try:
        return lock_path.read_bytes()
    except OSError as exc:
        raise PublicationLockFormatError(
            f"Unable to read publication lock: {lock_path}"
        ) from exc


def _read_evidence_bytes(evidence_path: Path) -> bytes:
    try:
        path_stat = evidence_path.lstat()
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        file_attributes = getattr(path_stat, "st_file_attributes", 0)
        if stat.S_ISLNK(path_stat.st_mode) or file_attributes & reparse_flag:
            raise PublicationLockRecoveryError(
                "Recovered lock evidence became a symlink or reparse point"
            )
        return evidence_path.read_bytes()
    except PublicationLockRecoveryError:
        raise
    except OSError as exc:
        raise PublicationLockRecoveryError(
            f"Unable to read recovered lock evidence: {evidence_path}"
        ) from exc


def _parse_publication_lock(*, raw: bytes, root: Path) -> PublicationLock:
    lock_path = root / PUBLICATION_LOCK_NAME
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicationLockFormatError(
            f"Unable to read publication lock: {lock_path}"
        ) from exc
    if not isinstance(payload, dict):
        raise PublicationLockFormatError(
            "Publication lock must contain a JSON object"
        )
    lock_version = payload.get("lock_version")
    if (
        isinstance(lock_version, bool)
        or not isinstance(lock_version, int)
        or lock_version != PUBLICATION_LOCK_VERSION
    ):
        raise PublicationLockFormatError("Unsupported publication lock version")

    stored_root = _required_lock_text(payload, "published_root")
    if Path(stored_root).resolve(strict=False) != root:
        raise PublicationLockFormatError(
            "Publication lock published_root does not match its location"
        )
    return PublicationLock(
        path=lock_path,
        published_root=root,
        run_id=_required_lock_text(payload, "run_id"),
        survey_id=_required_lock_text(payload, "survey_id"),
        owner_token=_required_lock_text(payload, "owner_token"),
        created_at=_required_lock_text(payload, "created_at"),
    )


def _required_aware_timestamp(value: str, field_name: str) -> tuple[str, datetime]:
    safe_value = _required_text(value, field_name)
    normalized = safe_value[:-1] + "+00:00" if safe_value.endswith("Z") else safe_value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    return safe_value, parsed


def _recovery_confirmation(
    *,
    status: Literal["valid", "malformed"],
    raw_sha256: str,
    lock: PublicationLock | None,
) -> str:
    if status == "valid":
        if lock is None:
            raise PublicationLockRecoveryError(
                "Valid diagnosis must include parsed lock ownership"
            )
        return f"RECOVER {lock.survey_id} {lock.run_id} {raw_sha256}"
    if lock is not None:
        raise PublicationLockRecoveryError(
            "Malformed diagnosis must not include parsed lock ownership"
        )
    return f"RECOVER MALFORMED {raw_sha256}"


def _diagnosis_identity_matches(
    expected: PublicationLockDiagnosis,
    current: PublicationLockDiagnosis,
) -> bool:
    return (
        expected.path == current.path
        and expected.published_root == current.published_root
        and expected.status == current.status
        and expected.raw_sha256 == current.raw_sha256
        and expected.lock == current.lock
        and expected.format_error == current.format_error
        and expected.recovery_confirmation == current.recovery_confirmation
    )


def _safe_recovery_id(value: str) -> str:
    safe_value = _required_text(value, "recovery_id")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", safe_value):
        raise ValueError(
            "recovery_id must contain only letters, digits, dot, underscore, or dash"
        )
    if safe_value in {".", ".."}:
        raise ValueError("recovery_id must be a safe filename component")
    return safe_value


def _rename_path(source: Path, destination: Path) -> None:
    os.rename(source, destination)


def _restore_quarantined_lock(*, evidence_path: Path, lock_path: Path) -> None:
    if lock_path.exists():
        raise PublicationLockRecoveryError(
            "A new publication lock appeared before quarantined evidence could be restored"
        )
    os.rename(evidence_path, lock_path)


def _write_json_exclusive(path: Path, payload: dict[str, object]) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        if path.is_file():
            path.unlink()
        raise


def _resolve_published_root(published_root: Path) -> Path:
    root = Path(published_root).resolve(strict=False)
    if root.parent == root:
        raise ValueError("Published root must not be a filesystem root")
    return root


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value


def _required_lock_text(payload: dict[str, object], field_name: str) -> str:
    try:
        return _required_text(payload.get(field_name), field_name)
    except ValueError as exc:
        raise PublicationLockFormatError(
            f"Publication lock {field_name} is required"
        ) from exc
