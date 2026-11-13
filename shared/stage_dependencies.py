from __future__ import annotations

from collections.abc import Iterable


WEBODM_OUTPUT_FORCE_STAGES = frozenset(
    {
        "webodm",
        "webodm_task4",
        "webodm_task2",
    }
)


def expand_webodm_output_dependencies(
    force_stages: Iterable[str],
) -> set[str]:
    """Return force stages plus consumers of refreshed WebODM outputs."""
    expanded = set(force_stages)
    if expanded.intersection(WEBODM_OUTPUT_FORCE_STAGES):
        expanded.update({"quality_gate", "qgis"})
    return expanded
