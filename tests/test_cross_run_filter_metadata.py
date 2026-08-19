import importlib.util
import logging
import sys
import types


if "exifread" not in sys.modules and importlib.util.find_spec("exifread") is None:
    exifread_stub = types.ModuleType("exifread")
    exifread_stub.__spec__ = importlib.util.spec_from_loader("exifread", loader=None)
    exifread_stub.process_file = lambda *args, **kwargs: {}
    sys.modules["exifread"] = exifread_stub

from modules.cross_run_image_filter import cross_run_image_filter as filter_module


def test_cross_run_filter_returns_image_classifications_with_all_reasons(
    tmp_path,
    monkeypatch,
):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output" / "path"
    input_dir.mkdir()
    for name in ("a.jpg", "b.jpg", "c.jpg", "d.jpg"):
        (input_dir / name).write_bytes(name.encode("ascii"))

    monkeypatch.setattr(
        filter_module,
        "get_gps",
        lambda path: (1.0, float(ord(path.stem) - ord("a"))),
    )
    monkeypatch.setattr(
        filter_module,
        "calculate_distances_and_bearings",
        lambda files: [(name, 10.0, 5.0) for name, _ in files],
    )
    monkeypatch.setattr(
        filter_module,
        "calculate_adaptive_parameters",
        lambda distances, bearings, logger: {
            "cluster_ratio": 0.4,
            "normal_distance_ratio": 0.5,
            "aggressive_distance_ratio": 0.7,
            "bridge_gap": 2.0,
            "median_distance": 10.0,
            "cv_distance": 0.0,
        },
    )
    monkeypatch.setattr(filter_module, "calculate_typical_distance", lambda values: 10.0)
    monkeypatch.setattr(
        filter_module,
        "detect_cross_runs_adaptive",
        lambda metrics, logger, verbose=False: (["b.jpg"], {"threshold": 40.0}),
    )
    monkeypatch.setattr(
        filter_module,
        "get_cross_run_regions",
        lambda files, cross_runs, window_size=3: {"b.jpg", "c.jpg"},
    )
    monkeypatch.setattr(
        filter_module,
        "detect_close_clusters",
        lambda *args, **kwargs: {"b.jpg"},
    )
    monkeypatch.setattr(
        filter_module,
        "filter_close_images",
        lambda *args, **kwargs: {"b.jpg", "c.jpg"},
    )
    monkeypatch.setattr(
        filter_module,
        "exclude_ranges",
        lambda *args, **kwargs: {"d.jpg"},
    )

    result = filter_module.run_filter(
        input_dir=input_dir,
        output_dir=output_dir,
        logger=logging.Logger("test.cross-run.metadata"),
    )

    classifications = {
        item["relative_path"]: item
        for item in result["image_classifications"]
    }
    assert classifications["a.jpg"] == {
        "relative_path": "a.jpg",
        "disposition": "kept",
        "reasons": [],
    }
    assert classifications["b.jpg"]["reasons"] == ["cluster", "too_close"]
    assert classifications["c.jpg"]["reasons"] == ["too_close"]
    assert classifications["d.jpg"]["reasons"] == ["cross_run_range"]
    assert sorted(path.name for path in output_dir.iterdir()) == ["a.jpg"]
    assert sorted(path.name for path in output_dir.parent.joinpath("cross-runs").iterdir()) == [
        "b.jpg",
        "c.jpg",
        "d.jpg",
    ]
