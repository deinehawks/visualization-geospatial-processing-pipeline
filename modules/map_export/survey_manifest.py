from __future__ import annotations
from pathlib import Path
import sqlite3
import json
import logging

logger = logging.getLogger("rgb.map_export")

_manifest_index_cache: dict[Path, dict[str, dict]] = {}


def _extract_dataset_folder(survey_name: str) -> str:
    return Path(survey_name).name


def _build_manifest_index(surveys_root: Path) -> dict[str, dict]:
    index: dict[str, dict] = {}

    for manifest_path in surveys_root.rglob("manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        dataset_folder: str | None = None

        # New schema
        ctx = manifest.get("source_context") or {}
        dataset_folder = ctx.get("source_dataset_folder")

        # Old schema fallback: last segment of source_folder
        if not dataset_folder:
            source_folder = manifest.get("source_folder")
            if source_folder:
                dataset_folder = Path(source_folder).name

        if not dataset_folder:
            continue

        index[dataset_folder.lower()] = manifest

    return index


def _get_manifest_index(surveys_root: Path) -> dict[str, dict]:
    if surveys_root not in _manifest_index_cache:
        logger.info("Building manifest index under %s (one-time scan)...", surveys_root)
        _manifest_index_cache[surveys_root] = _build_manifest_index(surveys_root)
        logger.info("Manifest index built: %d entries", len(_manifest_index_cache[surveys_root]))
    return _manifest_index_cache[surveys_root]


def _resolve_survey_id_from_db(db_path: Path | None, source_dir: str) -> str | None:
    if db_path is None or not Path(db_path).exists():
        return None

    normalized = str(Path(source_dir))

    try:
        con = sqlite3.connect(str(db_path))
        try:
            cur = con.cursor()
            cur.execute(
                """
                SELECT survey_id
                FROM runs
                WHERE source_dir = ? COLLATE NOCASE
                  AND status = 'completed'
                  AND survey_id IS NOT NULL
                ORDER BY finished_at DESC
                LIMIT 1
                """,
                (normalized,),
            )
            row = cur.fetchone()
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        logger.warning("pipeline.db unreadable/malformed, falling back to manifest scan: %s", exc)
        return None

    return row[0] if row else None


def _resolve_survey_id_from_manifest(surveys_root: Path, survey_name: str) -> str | None:
    dataset_folder = _extract_dataset_folder(survey_name)
    index = _get_manifest_index(surveys_root)
    manifest = index.get(dataset_folder.lower())
    return manifest.get("survey_id") if manifest else None


def resolve_survey_id(surveys_root: Path, db_path: Path | None, survey_name: str) -> str:
    survey_id = _resolve_survey_id_from_db(db_path, survey_name)

    if survey_id:
        logger.info("Resolved survey_id via DB: %s -> %s", survey_name, survey_id)
        return survey_id

    survey_id = _resolve_survey_id_from_manifest(surveys_root, survey_name)

    if survey_id:
        logger.info("Resolved survey_id via manifest scan: %s -> %s", survey_name, survey_id)
        return survey_id

    # --- DEBUG: dump what we extracted vs what's actually in the index ---
    dataset_folder = _extract_dataset_folder(survey_name)
    index = _get_manifest_index(surveys_root)
    logger.error("Looking for dataset folder: %r", dataset_folder.lower())
    logger.error("Index has %d entries. Sample keys:", len(index))
    for sample_key in list(index.keys())[:15]:
        logger.error("  %r", sample_key)
    # --- END DEBUG ---

    raise FileNotFoundError(
        f"Could not resolve survey_id for {survey_name} "
        f"(checked pipeline.db and manifest.json under {surveys_root})"
    )


def load_manifest(surveys_root: Path, survey_id: str) -> dict:
    matches = list(surveys_root.rglob(f"{survey_id}/rgb/manifest.json"))

    if not matches:
        raise FileNotFoundError(
            f"manifest.json not found under {surveys_root}/**/{survey_id}/rgb/"
        )

    return json.loads(matches[0].read_text(encoding="utf-8"))