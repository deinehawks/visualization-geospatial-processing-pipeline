# shared/constants.py
from __future__ import annotations

# ---------------------------------------------------------------------------
# WebODM restart-from stage names
# ---------------------------------------------------------------------------
# Maps user-facing aliases (typed at the quality gate prompt) to the internal
# ODM stage names that NodeODM accepts for the rerun-from option.
#
# Valid domain (WebODM 3.x / NodeODM):
#   dataset | opensfm | openmvs | odm_filterpoints | odm_meshing |
#   mvs_texturing | odm_georeferencing | odm_dem | odm_orthophoto |
#   odm_report | odm_postprocess
#
# WebODM UI label       → internal stage name
#   Load Dataset        → dataset
#   Structure from Motion → opensfm
#   Multi View Stereo   → openmvs
#   Point Filtering     → odm_filterpoints
#   Meshing             → odm_meshing
#   Texturing           → mvs_texturing
#   Georeferencing      → odm_georeferencing
#   DEM                 → odm_dem
#   Orthophoto          → odm_orthophoto
#   Report              → odm_report
#   Postprocess         → odm_postprocess
# ---------------------------------------------------------------------------

WEBODM_RESTART_STAGES: dict[str, str] = {
    # canonical internal names (always accepted)
    "dataset":              "dataset",
    "opensfm":              "opensfm",
    "openmvs":              "openmvs",
    "odm_filterpoints":     "odm_filterpoints",
    "odm_meshing":          "odm_meshing",
    "mvs_texturing":        "mvs_texturing",
    "odm_georeferencing":   "odm_georeferencing",
    "odm_dem":              "odm_dem",
    "odm_orthophoto":       "odm_orthophoto",
    "odm_report":           "odm_report",
    "odm_postprocess":      "odm_postprocess",

    # short aliases (quality gate prompt convenience)
    "load_dataset":           "dataset",
    "sfm":                    "opensfm",
    "structure_from_motion":  "opensfm",
    "mvs":                    "openmvs",
    "multi_view_stereo":      "openmvs",
    "filterpoints":           "odm_filterpoints",
    "point_filtering":        "odm_filterpoints",
    "meshing":                "odm_meshing",
    "texturing":              "mvs_texturing",
    "georeferencing":         "odm_georeferencing",
    "dem":                    "odm_dem",
    "orthophoto":             "odm_orthophoto",
    "report":                 "odm_report",
    "postprocess":            "odm_postprocess",
}

# Unique set of valid internal stage names — use for validation messages
WEBODM_RESTART_STAGE_NAMES: frozenset[str] = frozenset(WEBODM_RESTART_STAGES.values())