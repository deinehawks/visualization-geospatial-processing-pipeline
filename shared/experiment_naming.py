from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass
class ExperimentNames:
    base_id: str
    survey_id: str
    task1_name: str
    task2_name: str
    task1_export_id: str
    task2_export_id: str
    crossrun_label: str
    has_djifp: bool

def resolve_rgb_exp01_names(
    flight_test_code: str,
    crossrun_enabled: bool,
    naming_mode: str = "altitude",
) -> ExperimentNames:
    flight_test_code = flight_test_code.strip()

    has_djifp = flight_test_code.endswith("-DJIFP")
    core_name = flight_test_code[:-6] if has_djifp else flight_test_code

    parts = core_name.split("_")
    if len(parts) < 4:
        raise ValueError(
            f"Invalid flight test code: {flight_test_code!r}. "
            "Expected underscore-separated format like "
            "'STM_1Ha_A2S_50m_85f75s_3mps' or 'BCO-005_5.4Ha_M3C_60m_85f75s_5mps_p45'."
        )

    filter_tag = "F" if crossrun_enabled else "NF"
    suffix = "-DJIFP" if has_djifp else ""

    site_code = parts[0]
    aircraft = parts[2]
    angle = parts[6]

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
        stem = f"RGB-{site_code}-{aircraft}-{filter_tag}-{angle}"

    else:
        raise ValueError(
            f"Unknown naming_mode={naming_mode!r}. "
            "Expected 'altitude' or 'site'."
        )

    base_id = f"{stem}{suffix}"
    task1 = f"{stem}-T1{suffix}"
    task2 = f"{stem}-T2{suffix}"

    return ExperimentNames(
        base_id=base_id,
        survey_id=base_id,
        task1_name=task1,
        task2_name=task2,
        task1_export_id=task1,
        task2_export_id=task2,
        crossrun_label=filter_tag,
        has_djifp=has_djifp,
    )