from __future__ import annotations
from pathlib import Path
from shared.config import load_pipeline_config
from pipelines.rgb_pipeline import RGBPipeline
from shared.experiment_naming import resolve_rgb_exp01_names
import argparse


def _print_pipeline_summary(state: dict) -> None:

    from pathlib import Path

    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    GREY = "\033[90m"
    MAGENTA = "\033[95m"
    W = 62

    success = state.get("success", False)
    paused = state.get("paused", False)
    canceled = state.get("canceled", False)

    if success:
        status_line = f"{GREEN}{BOLD}[OK]  PIPELINE COMPLETED SUCCESSFULLY{RESET}"
    elif paused:
        status_line = f"{YELLOW}{BOLD}[--]  PIPELINE PAUSED{RESET}"
    elif canceled:
        status_line = f"{YELLOW}{BOLD}[--]  PIPELINE CANCELED{RESET}"
    else:
        status_line = f"{RED}{BOLD}[!!]  PIPELINE FAILED{RESET}"

    def row(label: str, value: str, color: str = WHITE) -> None:
        print(f"  {GREY}{label:<22}{RESET}{color}{value}{RESET}")

    def section(title: str) -> None:
        print(f"\n  {CYAN}{BOLD}{title}{RESET}")
        print(f"  {GREY}{'─' * (W - 4)}{RESET}")

    def short_path(p: str) -> str:
        parts = Path(p).parts
        return str(Path(*parts[-3:])) if len(parts) >= 3 else p

    print()
    print(f"{MAGENTA}{'═' * W}{RESET}")
    print(f"  {status_line}")
    print(f"{MAGENTA}{'═' * W}{RESET}")

    # ── Run ───────────────────────────────────────────────────────────────
    section("RUN")
    row("Run ID", state.get("run_id", "—"))
    if state.get("error"):
        row("Error", state["error"], RED)

    # ── Data segregation ──────────────────────────────────────────────────
    seg = state.get("data_segregation") or {}
    if seg:
        section("DATA SEGREGATION")
        row("Survey ID",   seg.get("survey_id", "—"), CYAN)
        row("Images",      str(seg.get("image_count", "—")))
        row("KML",         seg.get("kml_file", "—"))
        if seg.get("survey_path"):
            row("Survey path", f".../{short_path(seg['survey_path'])}")

    # ── Cross-run filter ──────────────────────────────────────────────────
    flt = state.get("cross_run_filter") or {}
    if flt:
        section("CROSS-RUN FILTER")
        row("Total images",  str(flt.get("total_images", "—")))
        row("Kept",          str(flt.get("total_kept", "—")),     GREEN)
        row("Excluded",      str(flt.get("total_excluded", "—")), YELLOW)
        row("Cross-runs",    str(flt.get("cross_runs_detected", "—")))
        row("Too-close",     str(flt.get("too_close_exclusions", "—")))
        row("Clusters",      str(flt.get("cluster_exclusions", "—")))
        row("Raw deleted",   str(flt.get("raw_deleted", False)))

    # ── KML boundary ──────────────────────────────────────────────────────
    kml = state.get("kml_boundary") or {}
    if kml:
        section("KML BOUNDARY")
        row("Processed", str(kml.get("processed", "—")), GREEN)
        row("Failed",    str(kml.get("failed", "—")),
            RED if kml.get("failed") else WHITE)
        pf = (kml.get("processed_files") or [{}])[0]
        if pf.get("geojson"):
            row("GeoJSON", Path(pf["geojson"]).name)

    # ── WebODM ────────────────────────────────────────────────────────────
    web = state.get("webodm") or {}
    if web:
        section("WEBODM")
        row("Project",
            f"{web.get('project_name', '—')}  (ID={web.get('project_id', '—')})",
            CYAN)

        for label, task in [("Task 1", web.get("task1")), ("Task 2", web.get("task2"))]:
            if not task:
                continue
            ok = task.get("success", False)
            rt = task.get("runtime_seconds") or 0
            m, s = divmod(int(rt), 60)
            mark = "[OK]" if ok else "[!!]"
            row(label,
                f"{task.get('name', '—')}  {mark}  {m}m {s}s",
                GREEN if ok else RED)

        dls = web.get("downloads") or {}
        t1d = dls.get("task1") or {}
        t2d = dls.get("task2") or {}

        if t1d.get("orthomosaic"):
            row("Ortho (unbounded)", Path(t1d["orthomosaic"]).name)
        if t2d.get("orthomosaic"):
            row("Ortho (bounded)",   Path(t2d["orthomosaic"]).name)
        if t2d.get("pointcloud_laz"):
            row("Point cloud (LAZ)", Path(t2d["pointcloud_laz"]).name)
        if t2d.get("all_assets_zip"):
            row("Assets ZIP",        Path(t2d["all_assets_zip"]).name)

    # ── Quality gate ──────────────────────────────────────────────────────
    qg = state.get("quality_gate") or {}
    if qg:
        section("QUALITY GATE")
        passed = qg.get("passed")
        row("Decision",
            "PASSED" if passed else ("FAILED" if passed is False else "—"),
            GREEN if passed else RED)
        if qg.get("restarts"):
            row("Restarts", str(qg["restarts"]))

    # ── QGIS ──────────────────────────────────────────────────────────────
    qgis = state.get("qgis") or {}
    if qgis and not qgis.get("skipped"):
        section("QGIS")
        ub = qgis.get("unbounded") or {}
        bd = qgis.get("bounded") or {}
        if ub.get("clipped"):
            row("Clipped (unbounded)", Path(ub["clipped"]).name)
        if bd.get("clipped"):
            row("Clipped (bounded)",   Path(bd["clipped"]).name)
        tiles_cfg = qgis.get("tiles") or {}
        row("Tiles enabled", str(tiles_cfg.get("enabled", "—")))
        row("Zoom",          str(tiles_cfg.get("zoom",    "—")))

    print()
    print(f"{MAGENTA}{'═' * W}{RESET}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Run RGB experiment")
    parser.add_argument("--survey", required=True,
                        help="Survey folder name inside FIELD_DATA_ROOT")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--run-id",
        default=None,
        help="Run ID to resume (required when --resume is set)",
    )
    parser.add_argument(
        "--node-id",
        type=int,
        default=None,
        help="Override WEBODM_NODE_ID for this run",
    )
    args = parser.parse_args()

    if args.resume and not args.run_id:
        parser.error("--run-id is required when using --resume")

    config = load_pipeline_config()

    if args.node_id is not None:
        config["webodm"]["node_id"] = args.node_id
        
    exp_cfg = config.get("experiment", {})
    exp_enabled = bool(exp_cfg.get("enabled", False))

    survey_id_override = None
    task_name_overrides = {}
    export_name_overrides = {}
    crossrun_enabled_override = None
    use_year_subdir_override = None

    if exp_enabled and exp_cfg.get("profile") == "rgb_exp_01":
        crossrun_enabled_override = bool(exp_cfg.get("crossrun_enabled", True))
        use_year_subdir_override = bool(exp_cfg.get("use_year_subdir", True))

        naming_mode = str(exp_cfg.get("naming_mode", "altitude"))

        names = resolve_rgb_exp01_names(
            args.survey,
            crossrun_enabled_override,
            naming_mode=naming_mode,
        )

        survey_id_override = names.base_id
        task_name_overrides = {
            "task1": names.task1_name,
            "task2": names.task2_name,
        }
        export_name_overrides = {
            "task1": names.task1_export_id,
            "task2": names.task2_export_id,
        }

        print("Experiment profile: rgb_exp_01")
        print(f"Resolved survey ID : {names.base_id}")
        print(f"Resolved Task 1    : {names.task1_name}")
        print(f"Resolved Task 2    : {names.task2_name}")
        print(f"Cross-run enabled  : {crossrun_enabled_override}")
        print(f"Naming mode        : {naming_mode}")

    field_data_root = Path(config["paths"]["field_data_root"])
    surveys_root = Path(config["paths"]["surveys_root"])

    node_id = int(config["webodm"].get("node_id") or 0)

    source_dir = field_data_root / args.survey
    if not source_dir.exists():
        raise FileNotFoundError(f"Survey folder not found: {source_dir}")

    print("\n===== EXPERIMENT SETTINGS =====")
    print(f"Source dir: {source_dir}")
    print(f"Outputs (SURVEYS_ROOT): {surveys_root}")
    print(f"WebODM Node ID: {node_id}")
    print("===============================\n")

    pipeline = RGBPipeline(
    base_dir=Path("."),
    config=config,
    source_dir=source_dir,
    surveys_root=surveys_root,
    year=args.year,
    run_id=args.run_id,
    survey_id_override=survey_id_override,
    task_name_overrides=task_name_overrides,
    export_name_overrides=export_name_overrides,
    crossrun_enabled_override=crossrun_enabled_override,
    use_year_subdir_override=use_year_subdir_override,
    )

    result = pipeline.run(resume=args.resume)
    _print_pipeline_summary(result)


if __name__ == "__main__":
    main()
