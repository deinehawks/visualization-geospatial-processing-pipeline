from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


PUBLICATION_LOCK_NAME = ".publication.lock"
PUBLICATION_LOCK_VERSION = 1


class PublicationLockError(RuntimeError):
    """Base error for publication ownership failures."""


class PublicationLockedError(PublicationLockError):
    """Raised when another or unreadable publication lock already exists."""


class PublicationLockFormatError(PublicationLockError):
    """Raised when a publication lock cannot be trusted."""


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
    lock_path = root / PUBLICATION_LOCK_NAME
    if lock_path.is_symlink():
        raise PublicationLockFormatError(
            f"Publication lock must not be a symbolic link: {lock_path}"
        )

    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
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
