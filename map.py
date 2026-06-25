from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

from modules.map_export.boundary_finder import parse_survey_list
from modules.map_export import export_map_package

def load_env_file(path: Path = Path(".env")) -> None:
    """
    Lightweight .env loader so map.py can run both in normal .venv
    and inside QGIS/OSGeo4W Python where python-dotenv may not exist.
    """
    try:
        from dotenv import load_dotenv  # type: ignore[reportMissingImports]

        load_dotenv(path)
        return

    except ModuleNotFoundError:
        pass

    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value

def main() -> None:
    load_env_file()

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

    parser.add_argument(
        "--export-print",
        action="store_true",
        help="Export print-ready PDF and PNG preview using QGIS.",
    )

    parser.add_argument(
        "--logo",
        default=None,
        help="Optional logo image path for the print layout.",
    )

    parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="PNG preview export DPI.",
    )
    parser.add_argument(
        "--location",
        default=None,
        help="Location/address text shown under the map title.",
    )
    parser.add_argument(
        "--map-scale",
        type=int,
        default=None,
        help="Optional fixed map scale denominator. Example: 15000 for 1:15,000.",
    )

    parser.add_argument(
        "--layout-template",
        default="assets/qgis_layouts/client_boundary_map.qpt",
        help="QGIS layout template (.qpt) used for print export.",
    )

    parser.add_argument(
        "--disclaimer",
        default=None,
        help="Disclaimer text shown in the print layout.",
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
        location=args.location,
        map_scale=args.map_scale,
        layout_template=Path(args.layout_template) if args.layout_template else None,
        disclaimer=args.disclaimer,
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

    if args.export_print:
      from modules.map_export.qgis_print_exporter import export_qgis_print_layout

      print("===== QGIS PRINT EXPORT =====", flush=True)

      print_result = export_qgis_print_layout(
          package_dir=result.output_dir,
          title=title,
          logo_path=Path(args.logo) if args.logo else None,
          dpi=args.dpi,
      )

      print(f"PDF     : {print_result['pdf']}")
      print(f"Preview : {print_result['preview']}")
      print(f"CRS     : {print_result['crs']}")
      print("=============================")
      print()


if __name__ == "__main__":
    main()