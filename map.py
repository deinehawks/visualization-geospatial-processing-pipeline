from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from modules.map_export.boundary_finder import parse_survey_list
from modules.map_export import export_map_package


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Export a combined survey boundary map package from KMZ/KML files."
    )

    parser.add_argument(
        "--surveys",
        "--survey",
        dest="surveys",
        required=True,
        help="Comma-separated survey folder names.",
    )

    parser.add_argument(
        "--name",
        default=None,
        help="Output map package name. Example: tlw-overall-map",
    )

    parser.add_argument(
        "--title",
        default=None,
        help="Human-readable map title.",
    )

    parser.add_argument(
        "--source-root",
        default=None,
        help="Root folder where survey folders are located. Defaults to FIELD_DATA_ROOT.",
    )

    parser.add_argument(
        "--output-root",
        default=None,
        help="Output root folder. Defaults to MAP_EXPORT_ROOT or exports/maps.",
    )

    args = parser.parse_args()

    survey_names = parse_survey_list(args.surveys)

    source_root = Path(
        args.source_root
        or os.getenv("FIELD_DATA_ROOT", "")
    )

    if not str(source_root):
        raise ValueError(
            "Missing source root. Set FIELD_DATA_ROOT in .env or pass --source-root."
        )

    output_root = Path(
        args.output_root
        or os.getenv("MAP_EXPORT_ROOT", "exports/maps")
    )

    map_name = args.name or f"map-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    title = args.title or map_name.replace("-", " ").replace("_", " ").title()

    print()
    print("===== MAP PACKAGE EXPORT =====")
    print(f"Source root : {source_root}")
    print(f"Output root : {output_root}")
    print(f"Map name    : {map_name}")
    print(f"Title       : {title}")
    print(f"Surveys     : {len(survey_names)}")
    for survey in survey_names:
        print(f"  - {survey}")
    print("==============================")
    print()

    result = export_map_package(
        survey_names=survey_names,
        source_root=source_root,
        output_root=output_root,
        map_name=map_name,
        title=title,
    )

    print()
    print("===== MAP PACKAGE RESULT =====")
    print(f"Output folder   : {result.output_dir}")
    print(f"Boundaries      : {result.boundaries_geojson}")
    print(f"Merged boundary : {result.merged_boundary_geojson}")
    print(f"Metadata        : {result.metadata_json}")
    print(f"Style           : {result.style_json}")
    print("==============================")
    print()


if __name__ == "__main__":
    main()