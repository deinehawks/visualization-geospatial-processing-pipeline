from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any
import uuid

from shared.config import load_pipeline_config
from shared.db.repo import PipelineRepo
from shared.paths import db_path
from shared.artifacts import RUN_WORKSPACE_OWNERSHIP_NAME
from shared.storage_preflight import (
    StorageCapacityError,
    build_storage_preflight,
    format_storage_report,
    is_sqlite_full_error,
    require_storage_capacity,
)
from modules.data_segregation.data_segregation import (
    resolve_source_dataset_dir,
    validate_source_uav_folder,
)


def read_run_state_read_only(database: Path, run_id: str) -> dict | None:
    resolved = Path(database).resolve(strict=True)
    connection = sqlite3.connect(
        f'{resolved.as_uri()}?mode=ro',
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        columns = {
            row['name']
            for row in connection.execute('PRAGMA table_info(runs)').fetchall()
        }
        workspace_expression = (
            'workspace_root'
            if 'workspace_root' in columns
            else 'NULL AS workspace_root'
        )
        row = connection.execute(
            (
                'SELECT run_id, survey_id, status, source_dir, surveys_root, '
                f'year, {workspace_expression} FROM runs WHERE run_id=?'
            ),
            (run_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def read_latest_stage_statuses_read_only(
    database: Path,
    run_id: str,
) -> dict[str, str]:
    resolved = Path(database).resolve(strict=True)
    connection = sqlite3.connect(
        f'{resolved.as_uri()}?mode=ro',
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            'SELECT id, stage_name, status FROM stages '
            'WHERE run_id=? ORDER BY id',
            (run_id,),
        ).fetchall()
        return {str(row['stage_name']): str(row['status']) for row in rows}
    finally:
        connection.close()


def resolve_storage_stage_needs(
    *,
    resume: bool,
    latest_stage_statuses: dict[str, str],
    force_stages: set[str],
    qgis_enabled: bool,
) -> dict[str, bool]:
    if not resume:
        return {
            'include_upload_cache': True,
            'include_qgis_staging': qgis_enabled,
            'include_workspace': True,
            'include_published_outputs': True,
        }

    def will_run(stage_name: str, *aliases: str) -> bool:
        forced = any(
            candidate in force_stages
            for candidate in (stage_name, *aliases)
        )
        return forced or latest_stage_statuses.get(stage_name) != 'completed'

    webodm_needed = will_run('webodm', 'webodm_task4')
    quality_gate_needed = will_run('quality_gate')
    qgis_needed = qgis_enabled and will_run('qgis')
    earlier_workspace_stage_needed = any(
        will_run(stage_name)
        for stage_name in (
            'data_segregation',
            'cross_run_filter',
            'kml_boundary',
        )
    )
    return {
        'include_upload_cache': webodm_needed or quality_gate_needed,
        'include_qgis_staging': qgis_needed,
        'include_workspace': (
            earlier_workspace_stage_needed
            or webodm_needed
            or quality_gate_needed
            or qgis_needed
        ),
        'include_published_outputs': (
            earlier_workspace_stage_needed
            or webodm_needed
            or quality_gate_needed
            or qgis_needed
        ),
    }


def resolve_cli_surveys_root(
    *,
    config: dict[str, Any],
    resume: bool,
    run_record: dict | None,
) -> Path:
    configured_root = Path(config['paths']['surveys_root'])
    persisted_root = (run_record or {}).get('surveys_root')
    if resume and persisted_root:
        return Path(str(persisted_root))
    return configured_root


def resolve_cli_workspace_root(
    *,
    base_dir: Path,
    config: dict[str, Any],
    resume: bool,
    run_id: str,
    run_record: dict | None,
    rebind_to_configured: bool = False,
    rebind_confirmation: str | None = None,
) -> Path:
    legacy_root = Path(base_dir) / 'data' / 'workspaces'
    configured = (config.get('paths') or {}).get('workspace_root')
    configured_root = Path(configured) if configured else legacy_root
    if not resume:
        return configured_root

    persisted = (run_record or {}).get('workspace_root')
    if rebind_to_configured:
        expected = f'REBIND WORKSPACE {run_id}'
        if rebind_confirmation != expected:
            raise ValueError(
                'Workspace rebind requires exact confirmation: '
                f'{expected!r}'
            )
        if configured_root == legacy_root:
            raise ValueError(
                'Workspace rebind requires WORKSPACE_ROOT outside the legacy root'
            )
        if persisted and Path(str(persisted)) != configured_root:
            raise ValueError(
                'Refusing to replace an existing persisted workspace root: '
                f'{persisted}'
            )
        target_workspace = configured_root / run_id
        target_ownership = target_workspace / RUN_WORKSPACE_OWNERSHIP_NAME
        if target_workspace.exists():
            if not target_ownership.is_file():
                raise ValueError(
                    'Configured run workspace already exists without ownership: '
                    f'{target_workspace}'
                )
            ownership = json.loads(target_ownership.read_text(encoding='utf-8'))
            if ownership.get('run_id') != run_id:
                raise ValueError(
                    'Configured workspace ownership does not match resumed run: '
                    f'{target_ownership}'
                )
        return configured_root

    if persisted:
        return Path(str(persisted))

    legacy_workspace = legacy_root / run_id
    ownership_path = legacy_workspace / RUN_WORKSPACE_OWNERSHIP_NAME
    if ownership_path.is_file():
        ownership = json.loads(ownership_path.read_text(encoding='utf-8'))
        if ownership.get('run_id') != run_id:
            raise ValueError(
                'Legacy workspace ownership does not match resumed run: '
                f'{ownership_path}'
            )
    return legacy_root


def resolve_cli_source_dir(
    *,
    survey: str,
    resume: bool,
    run_id: str | None,
    logger: logging.Logger,
    date_hint: str | None = None,
    uav_folder: str | None = None,
    repository: PipelineRepo | None = None,
    run_record: dict | None = None,
) -> Path:
    if resume:
        if not run_id:
            raise ValueError("run_id is required when resume=True")
        run = run_record
        if run is None:
            repo = repository or PipelineRepo(db_path(Path(".")))
            run = repo.get_run(run_id)
        if not run:
            raise ValueError(
                f"Cannot resume run {run_id!r}: no run record was found."
            )
        source_dir = run.get("source_dir")
        if not source_dir:
            raise ValueError(
                f"Cannot resume run {run_id!r}: run record has no source_dir."
            )
        resolved = Path(source_dir)
        validate_source_uav_folder(resolved, uav_folder)
        logger.info("Resume source dataset restored from run state: %s", resolved)
        return resolved

    survey_arg = Path(survey)

    return resolve_source_dataset_dir(
        survey_arg,
        logger,
        date_hint=date_hint,
        uav_folder=uav_folder,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run production RGB pipeline")
    parser.add_argument("--survey", required=True, help="Survey folder inside FIELD_DATA_ROOT")
    parser.add_argument(
        "--uav",
        default=None,
        metavar="FOLDER",
        help=(
            "Restrict dataset lookup to an exact UAV parent-folder name, "
            "for example M3M_A."
        ),
    )
    parser.add_argument(
        "--rgb",
        action="store_true",
        help="Select only DJI RGB source images whose names end in _D.JPG.",
    )
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--survey-id", default=None, help="Optional fixed survey ID, e.g. AH-026002")
    parser.add_argument("--node-id", type=int, default=None)
    parser.add_argument(
        "--disable-cross-run",
        action="store_true",
        help="Disable the cross-run filter stage and copy raw images directly into path output.",
    )
    parser.add_argument(
        "--force-stage",
        action="append",
        default=[],
        choices=[
            "data_segregation",
            "cross_run_filter",
            "kml_boundary",
            "webodm",
            "webodm_task4",
            "quality_gate",
            "qgis",
            "activate_publication",
        ],
        help=(
            "Force a completed stage to rerun during resume. "
            "webodm_task4 reconciles/downloads the existing Task 4. "
            "Can be used multiple times."
        ),
    )
    parser.add_argument(
        "--activate-publication",
        action="store_true",
        help="Activate publication after quality approval when paired with the exact confirmation phrase.",
    )
    parser.add_argument(
        "--publication-confirmation",
        default=None,
        help='Exact confirmation phrase required with --activate-publication, e.g. "PUBLISH AH-026019 run-001".',
    )
    parser.add_argument(
        "--keep-workspace",
        action="store_true",
        help="Retain the completed run workspace instead of cleaning it after verified success.",
    )
    parser.add_argument(
        "--date",
        default=None,
        metavar="YYYYMMDD",
        help=(
            "Date folder to use when multiple matches exist for --survey "
            "(e.g. 20260414). Skips the interactive prompt."
        ),
    )
    parser.add_argument(
        "--storage-preflight-only",
        action="store_true",
        help="Report storage readiness without constructing or running the pipeline.",
    )
    parser.add_argument(
        "--rebind-workspace-to-configured-root",
        action="store_true",
        help=(
            "For a legacy resume, use WORKSPACE_ROOT for the new attempt while "
            "retaining the old workspace unchanged."
        ),
    )
    parser.add_argument(
        "--workspace-rebind-confirmation",
        default=None,
        help='Exact phrase required for rebind: "REBIND WORKSPACE <run-id>".',
    )
    parser.add_argument(
        "--recover-empty-webodm-project",
        choices=["task4"],
        default=None,
        help=(
            "Explicitly authorize creation of Task 4 in a persisted WebODM "
            "project only after WebODM confirms that the project has zero tasks."
        ),
    )
    parser.add_argument(
        "--webodm-recovery-project-id",
        type=int,
        default=None,
        help="Persisted WebODM project ID expected by empty-project recovery.",
    )
    parser.add_argument(
        "--webodm-recovery-confirmation",
        default=None,
        help=(
            'Exact phrase required for recovery: "CREATE TASK4 IN EMPTY WEBODM '
            'PROJECT <project-id> FOR RUN <run-id>".'
        ),
    )

    # WebODM task mode: mutually exclusive flags
    task_group = parser.add_mutually_exclusive_group()
    task_group.add_argument(
        "--task2",
        action="store_true",
        help="Run WebODM Task 2 only (3d , legacy mode)",
    )
    task_group.add_argument(
        "--task4",
        action="store_true",
        help="Run WebODM Task 4 only (bounded orthomosaic, default)",
    )
    task_group.add_argument(
        "--both-tasks",
        action="store_true",
        help="Run WebODM Task 4 then Task 2 (Orthomosaic + 3D).",
    )

    args = parser.parse_args()

    if args.resume and not args.run_id:
        parser.error("--run-id is required when using --resume")
    if args.activate_publication and not args.publication_confirmation:
        parser.error("--publication-confirmation is required when using --activate-publication")
    if args.publication_confirmation and not args.activate_publication:
        parser.error("--publication-confirmation requires --activate-publication")
    if args.rebind_workspace_to_configured_root and not args.resume:
        parser.error("--rebind-workspace-to-configured-root requires --resume")
    if args.workspace_rebind_confirmation and not args.rebind_workspace_to_configured_root:
        parser.error(
            "--workspace-rebind-confirmation requires "
            "--rebind-workspace-to-configured-root"
        )
    recovery_companions_present = bool(
        args.webodm_recovery_project_id is not None
        or args.webodm_recovery_confirmation
    )
    if recovery_companions_present and not args.recover_empty_webodm_project:
        parser.error(
            "--webodm-recovery-project-id and --webodm-recovery-confirmation "
            "require --recover-empty-webodm-project"
        )
    if args.recover_empty_webodm_project:
        if not args.resume:
            parser.error("--recover-empty-webodm-project requires --resume")
        if args.webodm_recovery_project_id is None:
            parser.error(
                "--webodm-recovery-project-id is required when using "
                "--recover-empty-webodm-project"
            )
        if args.webodm_recovery_project_id <= 0:
            parser.error("--webodm-recovery-project-id must be positive")
        if "webodm_task4" not in set(args.force_stage or []):
            parser.error(
                "--recover-empty-webodm-project task4 requires "
                "--force-stage webodm_task4"
            )
        expected_recovery_confirmation = (
            "CREATE TASK4 IN EMPTY WEBODM PROJECT "
            f"{args.webodm_recovery_project_id} FOR RUN {args.run_id}"
        )
        if args.webodm_recovery_confirmation != expected_recovery_confirmation:
            parser.error(
                "--webodm-recovery-confirmation must exactly match: "
                f"{expected_recovery_confirmation!r}"
            )

    config: dict[str, Any] = load_pipeline_config()
    base_dir = Path(".")
    database_path = db_path(base_dir)
    selected_run_id = args.run_id or str(uuid.uuid4())

    run_record = None
    latest_stage_statuses: dict[str, str] = {}
    if args.resume:
        try:
            run_record = read_run_state_read_only(database_path, selected_run_id)
        except FileNotFoundError:
            parser.error(
                f"Cannot resume run {selected_run_id!r}: state database not found."
            )
        if run_record is None:
            parser.error(
                f"Cannot resume run {selected_run_id!r}: no run record was found."
            )
        latest_stage_statuses = read_latest_stage_statuses_read_only(
            database_path,
            selected_run_id,
        )

    if args.node_id is not None:
        config["webodm"]["node_id"] = args.node_id

    surveys_root = resolve_cli_surveys_root(
        config=config,
        resume=args.resume,
        run_record=run_record,
    )

    resolver_logger = logging.getLogger("rgb.source_resolver")

    source_dir = resolve_cli_source_dir(
        survey=args.survey,
        resume=args.resume,
        run_id=selected_run_id,
        logger=resolver_logger,
        date_hint=args.date,
        uav_folder=args.uav,
        run_record=run_record,
    )

    # Determine webodm_mode from CLI flags
    if args.task2:
        webodm_mode = "task2"
        mode_display = "Task 2 only (3d)"
    elif args.both_tasks:
        webodm_mode = "both"
        mode_display = "Orthomosaic + 3D (Task 4 then Task 2)"
    elif args.task4:
        webodm_mode = "task4"
        mode_display = "Task 4 only (bounded orthomosaic)"
    else:
        # Default to task4
        webodm_mode = "task4"
        mode_display = "Task 4 only (bounded orthomosaic, default)"

    workspace_root = resolve_cli_workspace_root(
        base_dir=base_dir,
        config=config,
        resume=args.resume,
        run_id=selected_run_id,
        run_record=run_record,
        rebind_to_configured=args.rebind_workspace_to_configured_root,
        rebind_confirmation=args.workspace_rebind_confirmation,
    )
    configured_cache_root = (config.get("paths") or {}).get("upload_cache_root")
    upload_cache_root = (
        Path(configured_cache_root)
        if configured_cache_root
        else Path(os.getenv("TEMP") or tempfile.gettempdir())
    )
    qgis_local_staging = (config.get("qgis") or {}).get("local_staging") or {}
    configured_qgis_staging = str(qgis_local_staging.get("dir") or "").strip()
    qgis_staging_root = (
        Path(configured_qgis_staging)
        if configured_qgis_staging
        else upload_cache_root / "qgis-staging"
    )
    storage_config = config.get("storage") or {}
    storage_stage_needs = resolve_storage_stage_needs(
        resume=args.resume,
        latest_stage_statuses=latest_stage_statuses,
        force_stages=set(args.force_stage or []),
        qgis_enabled=bool((config.get("qgis") or {}).get("enabled", True)),
    )
    exports_config = config.get("exports") or {}
    task4_all_assets_zip_enabled = bool(
        storage_stage_needs["include_upload_cache"]
        and webodm_mode in {"task4", "both"}
        and exports_config.get("enabled", False)
        and (exports_config.get("all_assets_zip") or {}).get(
            "enabled",
            False,
        )
    )
    storage_report = build_storage_preflight(
        source_dir=source_dir,
        database_path=database_path,
        logs_dir=base_dir / "data" / "logs",
        checkpoint_dir=base_dir / "data" / "logs",
        upload_cache_root=upload_cache_root,
        qgis_staging_root=qgis_staging_root,
        workspace_root=workspace_root,
        surveys_root=surveys_root,
        temp_root=Path(tempfile.gettempdir()),
        webodm_mode=webodm_mode,
        **storage_stage_needs,
        include_task4_all_assets_zip=task4_all_assets_zip_enabled,
        min_free_gb=int(storage_config.get("min_free_gb", 10)),
        min_free_percent=int(storage_config.get("min_free_percent", 5)),
        published_min_free_gb=int(
            storage_config.get("published_min_free_gb", 10)
        ),
        published_min_free_percent=int(
            storage_config.get("published_min_free_percent", 0)
        ),
        rgb_only=args.rgb,
    )
    print(format_storage_report(storage_report))
    try:
        require_storage_capacity(storage_report)
    except StorageCapacityError:
        raise SystemExit(3)
    if args.storage_preflight_only:
        return

    print("\n===== PRODUCTION RGB PIPELINE =====")
    print(f"Source dir   : {source_dir}")
    print(f"Surveys root : {surveys_root}")
    print(f"Year         : {args.year}")
    print(f"Survey ID    : {args.survey_id or 'auto-generate'}")
    print(f"WebODM mode  : {mode_display}")
    print(f"Cross-run    : {'disabled' if args.disable_cross_run else 'enabled'}")
    print(f"UAV folder   : {args.uav or 'any'}")
    print(f"Image mode   : {'DJI *_D.JPG only' if args.rgb else 'all JPG/JPEG'}")
    print(f"Workspace    : {workspace_root}")
    if args.rebind_workspace_to_configured_root:
        print(
            "Legacy workspace retained: "
            f"{base_dir / 'data' / 'workspaces' / selected_run_id}"
        )
    print("===================================\n")

    from pipelines.rgb_pipeline import RGBPipeline

    try:
        pipeline = RGBPipeline(
            base_dir=base_dir,
            config=config,
            source_dir=source_dir,
            surveys_root=surveys_root,
            year=args.year,
            run_id=selected_run_id,
            workspace_root=workspace_root,
            survey_id_override=args.survey_id,

            # production mode
            use_year_subdir_override=True,
            export_name_overrides=None, # using naming templates
            crossrun_enabled_override=False if args.disable_cross_run else None,

            # WebODM task mode
            skip_task1_webodm=True,
            webodm_mode=webodm_mode,
            task1_bounded=False,
            force_segregation=False,
            rgb_only=args.rgb,
        )

        result = pipeline.run(
            resume=args.resume,
            force_stages=set(args.force_stage or []),
            empty_webodm_recovery=(
                {
                    "operation": args.recover_empty_webodm_project,
                    "project_id": args.webodm_recovery_project_id,
                    "confirmation": args.webodm_recovery_confirmation,
                }
                if args.recover_empty_webodm_project
                else None
            ),
            publication_confirmation=(
                args.publication_confirmation if args.activate_publication else None
            ),
            keep_workspace=args.keep_workspace,
        )
    except sqlite3.Error as exc:
        if not is_sqlite_full_error(exc):
            raise
        print(
            "Pipeline state write failed because SQLite reported a full disk. "
            f"Database: {database_path}"
        )
        raise SystemExit(4) from exc

    print("\n===== PIPELINE RESULT =====")
    print(f"Success : {result.get('success')}")
    print(f"Run ID  : {result.get('run_id')}")
    print(f"Error   : {result.get('error', '—')}")
    print("===========================\n")


if __name__ == "__main__":
    main()

