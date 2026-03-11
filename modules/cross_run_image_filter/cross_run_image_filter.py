"""
Cross-Run Image Filter

Filters drone images by detecting:
- cross-run turns using bearing change distribution analysis
- close clusters (turning / hovering)
- too-close images (distance threshold)
- cross-run range exclusions (paired cross-runs within max_gap)

Outputs (STANDARD):
- kept images copied to: <output_dir>             (rgb/images/path)
- excluded images copied to: <output_dir>/../cross-runs  (rgb/images/cross-runs)

Design:
- No CLI parsing here
- Logger is injected
- Returns structured summary for pipeline state tracking
"""

from __future__ import annotations

import logging
import math
import shutil
import time
from pathlib import Path
from statistics import mean, stdev
from typing import Dict, List, Optional, Set, Tuple

import exifread

from shared.logging import (
    log_ok,
    log_progress,
    log_progress_done,
    log_section,
    log_step,
    log_warn,
)


# ============================================================
# EXIF / GPS
# ============================================================

def get_gps(path: Path) -> Optional[Tuple[float, float]]:
    """Extract GPS (lat, lon) from EXIF."""
    with path.open("rb") as f:
        tags = exifread.process_file(f, details=False)

    lat = tags.get("GPS GPSLatitude")
    lat_ref = tags.get("GPS GPSLatitudeRef")
    lon = tags.get("GPS GPSLongitude")
    lon_ref = tags.get("GPS GPSLongitudeRef")

    if not lat or not lon or not lat_ref or not lon_ref:
        return None

    def convert(values):
        d = float(values.values[0].num) / float(values.values[0].den)
        m = float(values.values[1].num) / float(values.values[1].den)
        s = float(values.values[2].num) / float(values.values[2].den)
        return d + m / 60 + s / 3600

    lat_v = convert(lat)
    if str(lat_ref.values).upper() != "N":
        lat_v = -lat_v

    lon_v = convert(lon)
    if str(lon_ref.values).upper() != "E":
        lon_v = -lon_v

    return lat_v, lon_v


# ============================================================
# GEO METRICS
# ============================================================

def haversine_distance(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, p1)
    lat2, lon2 = map(math.radians, p2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * \
        math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * math.asin(math.sqrt(a)) * 6_371_000


def bearing(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, p1)
    lat2, lon2 = map(math.radians, p2)
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * \
        math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def calculate_distances_and_bearings(
    files_with_gps: List[Tuple[str, Tuple[float, float]]],
) -> List[Tuple[str, float, float]]:
    results: List[Tuple[str, float, float]] = []
    last_point = None
    last_bearing = None

    for fname, gps in files_with_gps:
        if last_point is None:
            results.append((fname, 0.0, 0.0))
            last_point = gps
            continue

        dist = haversine_distance(last_point, gps)
        b = bearing(last_point, gps)

        bearing_change = 0.0 if last_bearing is None else min(
            abs(b - last_bearing), 360 - abs(b - last_bearing))
        results.append((fname, dist, bearing_change))

        last_bearing = b
        last_point = gps

    return results


# ============================================================
# ADAPTIVE PARAMETERS
# ============================================================

def calculate_adaptive_parameters(
    distances: List[float],
    bearing_changes: List[float],
    logger: logging.Logger,
) -> Dict[str, float]:
    sorted_distances = sorted(d for d in distances if d > 0)

    if len(sorted_distances) < 10:
        return {
            "cluster_ratio":            0.40,
            "normal_distance_ratio":    0.50,
            "aggressive_distance_ratio": 0.70,
            "bridge_gap":               2,
            "median_distance":          float(sorted_distances[len(sorted_distances) // 2]) if sorted_distances else 0.0,
            "cv_distance":              0.0,
        }

    median_dist = sorted_distances[len(sorted_distances) // 2]
    p10_dist = sorted_distances[int(len(sorted_distances) * 0.10)]
    mean_dist = sum(sorted_distances) / len(sorted_distances)
    std_dist = (sum((d - mean_dist) ** 2 for d in sorted_distances) /
                len(sorted_distances)) ** 0.5
    cv_dist = std_dist / mean_dist if mean_dist > 0 else 0.0

    sorted_bearings = sorted(b for b in bearing_changes if b > 0)
    median_bearing = sorted_bearings[len(
        sorted_bearings) // 2] if sorted_bearings else 0.0

    cluster_ratio = 0.35 if cv_dist < 0.3 else (
        0.40 if cv_dist < 0.5 else 0.50)

    spread_ratio = p10_dist / median_dist if median_dist > 0 else 0.5
    normal_distance_ratio = 0.40 if spread_ratio < 0.3 else (
        0.50 if spread_ratio < 0.5 else 0.60)

    aggressive_distance_ratio = min(normal_distance_ratio + 0.20, 0.75)
    bridge_gap = 3 if median_bearing < 5 else (2 if median_bearing < 15 else 1)

    return {
        "cluster_ratio":            float(cluster_ratio),
        "normal_distance_ratio":    float(normal_distance_ratio),
        "aggressive_distance_ratio": float(aggressive_distance_ratio),
        "bridge_gap":               float(bridge_gap),
        "median_distance":          float(median_dist),
        "cv_distance":              float(cv_dist),
    }


def calculate_typical_distance(distances: List[float]) -> Optional[float]:
    filtered = sorted(d for d in distances if d > 1.0)
    if len(filtered) < 3:
        return None
    return float(filtered[len(filtered) // 2])


# ============================================================
# CROSS-RUN DETECTION
# ============================================================

def detect_cross_runs_adaptive(
    metrics: List[Tuple[str, float, float]],
    logger: logging.Logger,
    *,
    verbose: bool = False,
) -> Tuple[List[str], Dict[str, float]]:
    if len(metrics) < 3:
        return [], {"threshold": 0.0}

    bearing_changes = [bc for _, _, bc in metrics[1:]]
    if len(bearing_changes) < 2:
        return [], {"threshold": 0.0}

    sorted_changes = sorted(bearing_changes)
    avg_change = mean(bearing_changes)
    std_change = stdev(bearing_changes) if len(bearing_changes) > 1 else 0.0
    median_change = sorted_changes[len(sorted_changes) // 2]

    p75 = sorted_changes[int(len(sorted_changes) * 0.75)]
    p85 = sorted_changes[int(len(sorted_changes) * 0.85)]
    p90 = sorted_changes[int(len(sorted_changes) * 0.90)]
    p95 = sorted_changes[int(len(sorted_changes) * 0.95)]

    gaps = [(sorted_changes[i] - sorted_changes[i - 1], sorted_changes[i - 1],
             sorted_changes[i], i) for i in range(1, len(sorted_changes))]
    p70_idx = int(len(sorted_changes) * 0.70)
    upper_gaps = [(g, lo, hi, idx) for g, lo, hi, idx in gaps if idx > p70_idx]

    gap_threshold = None
    if upper_gaps:
        median_upper_gap = sorted(
            g for g, *_ in upper_gaps)[len(upper_gaps) // 2]
        significant_gaps = [(g, lo, hi) for g, lo, hi,
                            _ in upper_gaps if median_upper_gap > 0 and g > median_upper_gap * 2]
        if significant_gaps:
            largest = max(significant_gaps, key=lambda x: x[0])
            gap_threshold = largest[1] + (largest[0] / 2)

    q1 = sorted_changes[int(len(sorted_changes) * 0.25)]
    q3 = sorted_changes[int(len(sorted_changes) * 0.75)]
    iqr = q3 - q1
    iqr_threshold = q3 + (2.0 * iqr)

    candidates = []
    if gap_threshold and gap_threshold > p75:
        candidates.append(("Gap Detection", float(gap_threshold), 1))
    candidates += [
        ("85th Percentile",         float(p85),          2),
        ("Modified IQR (q3+2*iqr)", float(iqr_threshold), 3),
        ("90th Percentile",         float(p90),          4),
    ]

    valid = [(name, val, prio)
             for name, val, prio in candidates if p85 <= val <= (p95 + 30)]
    if valid:
        selected_method, threshold, _ = min(valid, key=lambda x: x[2])
    else:
        selected_method, threshold = "85th Percentile (fallback)", float(p85)

    threshold = max(threshold, 40.0)
    cross_runs = [fname for fname, _, bc in metrics if bc > threshold]

    logger.info(
        f"Cross-run detection | method={selected_method} threshold={threshold:.2f}° "
        f"avg={avg_change:.2f}° median={median_change:.2f}° std={std_change:.2f}° "
        f"p75={p75:.2f}° p85={p85:.2f}° p90={p90:.2f}° p95={p95:.2f}° "
        f"detected={len(cross_runs)}"
    )

    if verbose and cross_runs:
        for fname, dist, bc in metrics:
            if bc > threshold:
                logger.debug(
                    f"  cross-run: {fname} | bearing_change={bc:.2f}° dist={dist:.2f}m")

    return cross_runs, {"method": selected_method, "threshold": float(threshold)}


def get_cross_run_regions(files: List[str], cross_runs: List[str], window_size: int = 3) -> Set[str]:
    if not cross_runs:
        return set()
    file_indices = {fname: idx for idx, fname in enumerate(files)}
    region: Set[str] = set()
    for cr_name in cross_runs:
        if cr_name not in file_indices:
            continue
        cr_idx = file_indices[cr_name]
        for offset in range(-window_size, window_size + 1):
            target_idx = cr_idx + offset
            if 0 <= target_idx < len(files):
                region.add(files[target_idx])
    return region


# ============================================================
# CLUSTER + TOO-CLOSE
# ============================================================

def detect_close_clusters(
    files_with_gps: List[Tuple[str, Tuple[float, float]]],
    typical_distance: Optional[float],
    cross_runs_set: Set[str],
    min_distance_ratio: float,
    bridge_gap: int,
    logger: logging.Logger,
    *,
    verbose: bool = False,
) -> Set[str]:
    if typical_distance is None:
        return set()

    min_distance = typical_distance * min_distance_ratio
    current_cluster: List[str] = []
    last_point = None
    last_fname = None
    all_clusters:  List[List[str]] = []

    for fname, gps in files_with_gps:
        if last_point is None:
            last_point = gps
            last_fname = fname
            current_cluster = [fname]
            continue

        dist = haversine_distance(last_point, gps)
        if dist < min_distance:
            if not current_cluster and last_fname:
                current_cluster = [last_fname]
            current_cluster.append(fname)
        else:
            if len(current_cluster) >= 2:
                all_clusters.append(current_cluster)
            current_cluster = []

        last_point = gps
        last_fname = fname

    if len(current_cluster) >= 2:
        all_clusters.append(current_cluster)

    files_list = [fname for fname, _ in files_with_gps]
    file_to_idx = {fname: i for i, fname in enumerate(files_list)}

    merged_clusters: List[List[str]] = []
    i = 0
    while i < len(all_clusters):
        current = all_clusters[i]
        while i + 1 < len(all_clusters):
            nxt = all_clusters[i + 1]
            current_end_idx = file_to_idx[current[-1]]
            next_start_idx = file_to_idx[nxt[0]]
            if next_start_idx - current_end_idx - 1 <= bridge_gap:
                bridged = current.copy()
                for idx in range(current_end_idx + 1, next_start_idx + 1):
                    bridged.append(files_list[idx])
                bridged.extend(nxt)
                current = bridged
                i += 1
            else:
                break
        merged_clusters.append(current)
        i += 1

    if merged_clusters:
        log_ok(logger, f"Close clusters detected: {len(merged_clusters)}")

    cluster_images: Set[str] = set()
    for cluster in merged_clusters:
        start_idx = file_to_idx[cluster[0]]
        end_idx = file_to_idx[cluster[-1]]
        expanded = cluster.copy()

        if start_idx - 1 >= 0 and files_list[start_idx - 1] in cross_runs_set:
            expanded.insert(0, files_list[start_idx - 1])
        if end_idx + 1 < len(files_list) and files_list[end_idx + 1] in cross_runs_set:
            expanded.append(files_list[end_idx + 1])

        cluster_images.update(expanded)

        if verbose:
            logger.debug(
                f"  cluster: {expanded[0]} → {expanded[-1]} ({len(expanded)} images)")

    return cluster_images


def filter_close_images(
    files_with_gps: List[Tuple[str, Tuple[float, float]]],
    typical_distance: Optional[float],
    cross_run_regions: Set[str],
    min_distance_ratio: float,
    aggressive_ratio: float,
    logger: logging.Logger,
    *,
    verbose: bool = False,
) -> Set[str]:
    if typical_distance is None:
        return set()

    min_distance_normal = typical_distance * min_distance_ratio
    min_distance_aggressive = typical_distance * aggressive_ratio

    logger.info(
        f"Distance thresholds | typical={typical_distance:.2f}m "
        f"normal={min_distance_normal:.2f}m "
        f"cross_region={min_distance_aggressive:.2f}m"
    )

    too_close: Set[str] = set()
    last_kept_point = None
    last_kept_name: Optional[str] = None

    for fname, gps in files_with_gps:
        if last_kept_point is None:
            last_kept_point = gps
            last_kept_name = fname
            continue

        dist = haversine_distance(last_kept_point, gps)
        threshold = min_distance_aggressive if fname in cross_run_regions else min_distance_normal

        if dist < threshold:
            too_close.add(fname)
            if verbose:
                logger.debug(
                    f"  too_close ({'CROSS' if fname in cross_run_regions else 'NORMAL'}): "
                    f"{fname} {dist:.2f}m from {last_kept_name} (< {threshold:.2f}m)"
                )
        else:
            last_kept_point = gps
            last_kept_name = fname

    log_ok(logger, f"Too-close images excluded: {len(too_close)}")
    return too_close


def exclude_ranges(
    files: List[str],
    cross_runs: List[str],
    max_gap: int,
    logger: logging.Logger,
) -> Set[str]:
    if len(cross_runs) < 2:
        log_ok(logger, "Range exclusions: none (need ≥2 cross-runs)")
        return set()

    file_indices = {fname: idx for idx, fname in enumerate(files)}
    excluded: Set[str] = set()
    pair_count = 0

    i = 0
    while i < len(cross_runs) - 1:
        start_name = cross_runs[i]
        end_name = cross_runs[i + 1]

        if start_name not in file_indices or end_name not in file_indices:
            i += 1
            continue

        start_idx, end_idx = sorted(
            [file_indices[start_name], file_indices[end_name]])

        if end_idx - start_idx <= max_gap:
            pair_count += 1
            for idx in range(start_idx, end_idx + 1):
                excluded.add(files[idx])
            i += 2
        else:
            i += 1

    log_ok(
        logger, f"Range exclusion pairs applied: {pair_count} | images_excluded={len(excluded)}")
    return excluded


# ============================================================
# HELPER
# ============================================================

def _reset_dir(dir_path: Path) -> None:
    if dir_path.exists():
        shutil.rmtree(dir_path)
    dir_path.mkdir(parents=True, exist_ok=True)


# ============================================================
# PIPELINE ENTRY
# ============================================================

def run_filter(
    input_dir: Path,
    output_dir: Path,
    logger: logging.Logger,
    sensitivity: float = 1.5,   # kept for compatibility (not used)
    max_gap: int = 10,
    cross_run_window: int = 3,
    *,
    reset_outputs: bool = True,
    verbose: bool = False,
) -> Dict[str, object]:

    input_dir = Path(input_dir)
    output_dir = Path(output_dir)

    if not input_dir.exists() or not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")

    excluded_dir = output_dir.parent / "cross-runs"

    if reset_outputs:
        _reset_dir(output_dir)
        _reset_dir(excluded_dir)
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        excluded_dir.mkdir(parents=True, exist_ok=True)

    log_section(logger, "CROSS-RUN IMAGE FILTER")
    logger.info(f"Input     : {input_dir}")
    logger.info(f"Kept    → : {output_dir}")
    logger.info(f"Excluded→ : {excluded_dir}")

    # ── Step 1: Collect files ──────────────────────────────────────────────
    log_step(logger, 1, "Collect image files")
    files = sorted(
        p.name for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")
    )
    log_ok(logger, f"Images found: {len(files)}")

    # ── Step 2: Extract GPS ────────────────────────────────────────────────
    log_step(logger, 2, "Extract GPS from EXIF")

    files_with_gps: List[Tuple[str, Tuple[float, float]]] = []
    no_gps_files:   List[str] = []
    total_gps = len(files)
    t0 = time.perf_counter()

    for i, fname in enumerate(files, 1):
        gps = get_gps(input_dir / fname)
        if gps is None:
            no_gps_files.append(fname)
        else:
            files_with_gps.append((fname, gps))

        if i % 25 == 0 or i == total_gps:
            log_progress(logger, "Reading EXIF", current=i,
                         total=total_gps, elapsed=time.perf_counter() - t0)

    log_progress_done(logger, "Reading EXIF", total=total_gps,
                      elapsed=time.perf_counter() - t0)
    log_ok(logger, f"Images with GPS: {len(files_with_gps)}")

    if no_gps_files:
        log_warn(
            logger, f"Images without GPS (kept by default): {len(no_gps_files)}")

    if len(files_with_gps) < 3:
        raise RuntimeError(
            "Not enough images with GPS data for analysis (need ≥ 3)")

    # ── Step 3: Compute metrics ────────────────────────────────────────────
    log_step(logger, 3, "Compute distances & bearing changes")
    metrics = calculate_distances_and_bearings(files_with_gps)
    distances = [d for _, d, _ in metrics if d > 0]
    bearing_changes = [bc for _, _, bc in metrics if bc > 0]
    typical_distance = calculate_typical_distance(distances)
    log_ok(
        logger, f"Typical inter-image distance: {typical_distance:.2f}m" if typical_distance else "Typical distance: N/A")

    # ── Step 4: Adaptive params ────────────────────────────────────────────
    log_step(logger, 4, "Compute adaptive parameters")
    adaptive_params = calculate_adaptive_parameters(
        distances, bearing_changes, logger)
    log_ok(
        logger,
        f"cluster={adaptive_params['cluster_ratio']:.2f} "
        f"normal={adaptive_params['normal_distance_ratio']:.2f} "
        f"aggr={adaptive_params['aggressive_distance_ratio']:.2f} "
        f"bridge_gap={int(adaptive_params['bridge_gap'])}",
    )

    # ── Step 5: Detect cross-runs ──────────────────────────────────────────
    log_step(logger, 5, "Detect cross-runs")
    cross_runs, cross_run_stats = detect_cross_runs_adaptive(
        metrics, logger, verbose=verbose)
    log_ok(logger, f"Cross-runs detected: {len(cross_runs)}")

    # ── Step 6: Cross-run regions ──────────────────────────────────────────
    log_step(logger, 6, "Identify cross-run regions")
    cross_run_regions = get_cross_run_regions(
        files, cross_runs, window_size=cross_run_window)
    log_ok(logger, f"Cross-run region images: {len(cross_run_regions)}")

    # ── Step 7: Cluster detection ──────────────────────────────────────────
    log_step(logger, 7, "Detect close clusters")
    close_clusters = detect_close_clusters(
        files_with_gps,
        typical_distance,
        set(cross_runs),
        min_distance_ratio=float(adaptive_params["cluster_ratio"]),
        bridge_gap=int(adaptive_params["bridge_gap"]),
        logger=logger,
        verbose=verbose,
    )
    log_ok(logger, f"Cluster exclusions: {len(close_clusters)}")

    # ── Step 8: Too-close filter ───────────────────────────────────────────
    log_step(logger, 8, "Filter too-close images")
    too_close = filter_close_images(
        files_with_gps,
        typical_distance,
        cross_run_regions,
        min_distance_ratio=float(adaptive_params["normal_distance_ratio"]),
        aggressive_ratio=float(adaptive_params["aggressive_distance_ratio"]),
        logger=logger,
        verbose=verbose,
    )

    # ── Step 9: Range exclusions ───────────────────────────────────────────
    log_step(logger, 9, "Exclude cross-run ranges")
    range_excluded = exclude_ranges(
        files, cross_runs, max_gap=max_gap, logger=logger)

    # ── Step 10: Copy outputs ──────────────────────────────────────────────
    log_step(logger, 10, "Copy files to output directories")

    all_excluded = set(close_clusters) | set(too_close) | set(range_excluded)
    kept_files = [f for f in files if f not in all_excluded]
    excluded_files = [f for f in files if f in all_excluded]
    total_to_copy = len(kept_files) + len(excluded_files)
    copied = 0
    t0 = time.perf_counter()

    for fname in kept_files:
        shutil.copy2(input_dir / fname, output_dir / fname)
        copied += 1
        if copied % 25 == 0 or copied == total_to_copy:
            log_progress(logger, "Copying files", current=copied,
                         total=total_to_copy, elapsed=time.perf_counter() - t0)

    for fname in excluded_files:
        shutil.copy2(input_dir / fname, excluded_dir / fname)
        copied += 1
        if copied % 25 == 0 or copied == total_to_copy:
            log_progress(logger, "Copying files", current=copied,
                         total=total_to_copy, elapsed=time.perf_counter() - t0)

    log_progress_done(logger, "Copying files",
                      total=total_to_copy, elapsed=time.perf_counter() - t0)

    # ── Summary ───────────────────────────────────────────────────────────
    summary = {
        "input_dir":            str(input_dir),
        "output_dir":           str(output_dir),
        "excluded_dir":         str(excluded_dir),
        "total_images":         len(files),
        "images_with_gps":      len(files_with_gps),
        "images_without_gps":   len(no_gps_files),
        "cross_runs_detected":  len(cross_runs),
        "range_exclusions":     len(range_excluded),
        "cluster_exclusions":   len(close_clusters),
        "too_close_exclusions": len(too_close),
        "total_excluded":       len(excluded_files),
        "total_kept":           len(kept_files),
        "typical_distance_m":   float(typical_distance) if typical_distance is not None else None,
        "adaptive_params":      adaptive_params,
        "cross_run_stats":      cross_run_stats,
        "reset_outputs":        bool(reset_outputs),
    }

    log_section(logger, "CROSS-RUN FILTER SUMMARY")
    logger.info(
        f"total={summary['total_images']} | kept={summary['total_kept']} | "
        f"excluded={summary['total_excluded']} | cross_runs={summary['cross_runs_detected']} | "
        f"clusters={summary['cluster_exclusions']} | too_close={summary['too_close_exclusions']} | "
        f"range={summary['range_exclusions']}"
    )

    return summary
