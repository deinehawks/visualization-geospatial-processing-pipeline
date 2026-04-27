from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass
class ExperimentNames:
    base_id: str
    survey_id: str
    task1_name: str       # unbounded, cross-run filtered
    task2_name: str       # bounded,   cross-run filtered
    task3_name: str       # bounded,   cross-run filtered  (skip-task1 variant)
    task4_name: str       # bounded T1 (task1-bounded variant)
    task1_export_id: str
    task2_export_id: str
    task3_export_id: str
    task4_export_id: str
    crossrun_label: str
    has_djifp: bool


def resolve_rgb_exp01_names(
    flight_test_code: str,
    crossrun_enabled: bool,
    naming_mode: str = "altitude",
) -> ExperimentNames:
    """
    Resolve all task names and export IDs for an RGB experiment run.

    Task suffix conventions:
        T1  — WebODM unbounded (no boundary applied), cross-run filter
        T2  — WebODM bounded,  cross-run filter
        T3  — WebODM bounded,  cross-run filter  (--skip-task1-webodm)
        T4  — WebODM Task 1 with boundary applied (--task1-bounded + --skip-task2-webodm)

    T3 and T4 share the same stem as T1/T2 but carry a different suffix
    so their output files are self-describing and never overwrite T1/T2.
    """
    flight_test_code = flight_test_code.strip()

    has_djifp = flight_test_code.endswith("-DJIFP")
    core_name = flight_test_code[:-6] if has_djifp else flight_test_code

    parts = core_name.split("_")
    if len(parts) < 4:
        raise ValueError(
            f"Invalid flight test code: {flight_test_code!r}. "
            "Expected underscore-separated format like "
            "'STM_1Ha_A2S_50m_85f75s_3mps' or 'BCO-005_5.4Ha_M3C_60m_85f75s_5mps'."
        )

    filter_tag = "F" if crossrun_enabled else "NF"
    suffix = "-DJIFP" if has_djifp else ""

    site_code = parts[0]
    aircraft   = parts[2]
    speed      = parts[5] if len(parts) > 5 else ""
    angle      = parts[4] if len(parts) > 4 else ""

    # Altitude: find first token matching \d+m anywhere in the parts list
    altitude = next(
        (p for p in parts if re.fullmatch(r"\d+m", p, flags=re.IGNORECASE)),
        None,
    )

    if naming_mode == "altitude":
        if not altitude:
            raise ValueError(
                f"Could not find altitude token like '50m' in: {flight_test_code!r}"
            )
        stem = f"RGB-{aircraft}-{altitude}-{filter_tag}"

    elif naming_mode == "site":
        stem = f"RGB-{site_code}-{aircraft}-{filter_tag}"

    elif naming_mode == "angle":
        stem = f"RGB-{site_code}-{aircraft}-{angle}-{filter_tag}"

    elif naming_mode == "speed":
        if not altitude:
            raise ValueError(
                f"Could not find altitude token like '50m' in: {flight_test_code!r}"
            )
        stem = f"RGB-{site_code}-{aircraft}-{altitude}-{speed}-{filter_tag}"

    else:
        raise ValueError(
            f"Unknown naming_mode={naming_mode!r}. "
            "Expected 'altitude', 'site', 'speed', or 'angle'."
        )

    base_id = f"{stem}{suffix}"

    task1 = f"{stem}-T1{suffix}"   # unbounded
    task2 = f"{stem}-T2{suffix}"   # bounded
    task3 = f"{stem}-T3{suffix}"
    task4 = f"{stem}-T4{suffix}"

    return ExperimentNames(
        base_id=base_id,
        survey_id=base_id,
        task1_name=task1,
        task2_name=task2,
        task3_name=task3,
        task4_name=task4,
        task1_export_id=task1,
        task2_export_id=task2,
        task3_export_id=task3,
        task4_export_id=task4,
        crossrun_label=filter_tag,
        has_djifp=has_djifp,
    )