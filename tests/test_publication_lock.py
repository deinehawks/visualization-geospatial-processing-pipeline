import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from shared.publication_lock import (
    PUBLICATION_LOCK_NAME,
    PublicationLock,
    PublicationLockFormatError,
    PublicationLockedError,
    PublicationLockOwnershipError,
    acquire_publication_lock,
    read_publication_lock,
    release_publication_lock,
)


@pytest.fixture
def publication_root(tmp_path):
    sentinel = tmp_path / ".pytest-owned-publication-lock-root"
    sentinel.write_text("owned by pytest", encoding="utf-8")
    root = tmp_path / "surveys" / "2026" / "AH-026019" / "rgb"
    return root, sentinel


def test_publication_lock_acquire_read_release_cycle(publication_root):
    root, sentinel = publication_root

    lock = acquire_publication_lock(
        published_root=root,
        run_id="run-001",
        survey_id="AH-026019",
        owner_token="owner-001",
        created_at="2026-07-20T10:00:00+00:00",
    )
    current = read_publication_lock(root)

    assert lock == current
    assert lock.path == root.resolve() / PUBLICATION_LOCK_NAME
    assert json.loads(lock.path.read_text())["lock_version"] == 1

    release_publication_lock(lock)

    assert not lock.path.exists()
    assert sentinel.read_text(encoding="utf-8") == "owned by pytest"


def test_publication_lock_allows_only_one_concurrent_owner(publication_root):
    root, sentinel = publication_root

    def contender(run_id):
        try:
            return acquire_publication_lock(
                published_root=root,
                run_id=run_id,
                survey_id="AH-026019",
                owner_token=f"owner-{run_id}",
                created_at="2026-07-20T10:00:00+00:00",
            )
        except PublicationLockedError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(contender, ["run-001", "run-002"]))

    locks = [result for result in results if isinstance(result, PublicationLock)]
    failures = [
        result for result in results if isinstance(result, PublicationLockedError)
    ]
    assert len(locks) == 1
    assert len(failures) == 1

    release_publication_lock(locks[0])
    assert sentinel.is_file()


def test_publication_lock_refuses_wrong_owner_release(publication_root):
    root, sentinel = publication_root
    lock = acquire_publication_lock(
        published_root=root,
        run_id="run-001",
        survey_id="AH-026019",
        owner_token="owner-001",
    )
    wrong_owner = replace(lock, owner_token="owner-002")

    with pytest.raises(PublicationLockOwnershipError, match="ownership changed"):
        release_publication_lock(wrong_owner)

    assert lock.path.is_file()
    assert read_publication_lock(root).owner_token == "owner-001"
    release_publication_lock(lock)
    assert sentinel.is_file()


def test_publication_lock_fails_closed_for_malformed_existing_lock(publication_root):
    root, sentinel = publication_root
    root.mkdir(parents=True)
    lock_path = root / PUBLICATION_LOCK_NAME
    lock_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(PublicationLockFormatError, match="Unable to read"):
        read_publication_lock(root)
    with pytest.raises(PublicationLockedError, match="unreadable or malformed"):
        acquire_publication_lock(
            published_root=root,
            run_id="run-002",
            survey_id="AH-026019",
        )

    assert lock_path.read_text(encoding="utf-8") == "{not-json"
    assert sentinel.is_file()


def test_publication_lock_refuses_release_after_file_is_replaced(publication_root):
    root, sentinel = publication_root
    lock = acquire_publication_lock(
        published_root=root,
        run_id="run-001",
        survey_id="AH-026019",
        owner_token="owner-001",
    )
    payload = json.loads(lock.path.read_text(encoding="utf-8"))
    payload["run_id"] = "run-002"
    payload["owner_token"] = "owner-002"
    lock.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PublicationLockOwnershipError, match="ownership changed"):
        release_publication_lock(lock)

    current = read_publication_lock(root)
    assert current.run_id == "run-002"
    release_publication_lock(current)
    assert sentinel.is_file()


def test_publication_lock_rejects_filesystem_root():
    filesystem_root = Path(Path.cwd().anchor)

    with pytest.raises(ValueError, match="filesystem root"):
        acquire_publication_lock(
            published_root=filesystem_root,
            run_id="run-001",
            survey_id="AH-026019",
        )
