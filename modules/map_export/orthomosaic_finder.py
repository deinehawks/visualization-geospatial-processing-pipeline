from __future__ import annotations
from pathlib import Path
import logging
import json
from .survey_manifest import resolve_survey_id

logger = logging.getLogger("rgb.map_export")

ORTHOMOSAIC_PATTERN = "orthomosaic-clipped--*.tif"


def collect_orthomosaic_files(
    surveys_root: Path,
    db_path: Path | None,
    survey_names: list[str],
) -> list[Path]:

    if surveys_root is None:
        raise ValueError("survey_root is required")

    selected: list[Path] = []

    for survey_name in survey_names:
        survey_id = resolve_survey_id(surveys_root, db_path, survey_name)

        matches = list(
            surveys_root.rglob(
                f"{survey_id}/rgb/qgis/clipped/ortho/{ORTHOMOSAIC_PATTERN}"
            )
        )

        logger.info("=" * 70)
        logger.info("Survey            : %s", survey_name)
        logger.info("Survey ID         : %s", survey_id)
        logger.info("Searching under   : %s", surveys_root)
        logger.info("Pattern           : %s", ORTHOMOSAIC_PATTERN)
        logger.info("Candidates found  : %d", len(matches))

        if matches:
            logger.info("Candidate orthomosaics:")
            for candidate in sorted(matches):
                logger.info("  • %s", candidate)
        else:
            logger.warning("No orthomosaic candidates found.")

        if not matches:
            raise FileNotFoundError(
                f"No clipped orthomosaic found for survey ID: {survey_id}"
            )

        matches.sort(
            key=lambda p: (
                # Highest priority: Task 4
                "-t4" not in p.name.lower(),

                # Second priority: old filename (no -t2/-t4)
                ("-t2" in p.name.lower() or "-t4" in p.name.lower()),

                # Lowest priority: Task 2
                "-t2" in p.name.lower(),
            )
        )

        selected_file = matches[0]

        logger.info("Selected orthomosaic:")
        logger.info("  → %s", selected_file)
        logger.info("=" * 70)

        selected.append(selected_file)

    return selected