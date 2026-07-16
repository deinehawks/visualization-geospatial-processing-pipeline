import pytest

from shared.experiment_naming import resolve_rgb_exp01_names


@pytest.mark.parametrize(
    ("flight_test_code", "crossrun_enabled", "filter_tag", "suffix"),
    [
        ("STM_1Ha_A2S_50m_85f75s_3mps", True, "F", ""),
        ("STM_1Ha_A2S_50m_85f75s_3mps", False, "NF", ""),
        ("STM_1Ha_A2S_50m_85f75s_3mps-DJIFP", True, "F", "-DJIFP"),
        ("STM_1Ha_A2S_50m_85f75s_3mps-DJIFP", False, "NF", "-DJIFP"),
    ],
)
def test_resolve_rgb_exp01_names(flight_test_code, crossrun_enabled, filter_tag, suffix):
    names = resolve_rgb_exp01_names(flight_test_code, crossrun_enabled)
    stem = f"RGB-A2S-50m-{filter_tag}"

    assert names.base_id == f"{stem}{suffix}"
    assert names.survey_id == names.base_id
    assert names.task1_name == f"{stem}-T1{suffix}"
    assert names.task2_name == f"{stem}-T2{suffix}"
    assert names.task3_name == f"{stem}-T3{suffix}"
    assert names.task4_name == f"{stem}-T4{suffix}"
    assert names.crossrun_label == filter_tag
    assert names.has_djifp is bool(suffix)
