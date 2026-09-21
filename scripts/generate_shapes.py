#!/usr/bin/env python3
"""
Generates high-precision GTFS shapes.txt for Italo train trips using pfaedle
and pre-filtered OpenStreetMap railway infrastructure (data/italo-rail.osm.pbf).
Applies a fast pure-Python collinear decimation (3m tolerance) to keep the GTFS
lightweight (~5 MB zip).
"""

import csv
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OSM_PBF = REPO_ROOT / "data" / "italo-rail.osm.pbf"
DEFAULT_CFG = REPO_ROOT / "scripts" / "pfaedle.cfg"


def find_pfaedle() -> str:
    """Finds the pfaedle binary in PATH or standard install locations."""
    p = shutil.which("pfaedle")
    if p:
        return p
    for candidate in [
        Path.home() / ".local" / "bin" / "pfaedle",
        Path("/usr/local/bin/pfaedle"),
        Path("/usr/bin/pfaedle"),
    ]:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise FileNotFoundError(
        "pfaedle binary not found. Please install pfaedle (see https://github.com/ad-freiburg/pfaedle)."
    )


def point_to_segment_distance(lat: float, lon: float, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates perpendicular distance from point to segment in meters."""
    cos_mid = math.cos(math.radians((lat1 + lat2) / 2))
    x = (lon - lon1) * 111320 * cos_mid
    y = (lat - lat1) * 110574
    dx = (lon2 - lon1) * 111320 * cos_mid
    dy = (lat2 - lat1) * 110574
    norm = math.hypot(dx, dy)
    if norm == 0:
        return math.hypot(x, y)
    return abs(x * dy - y * dx) / norm


def simplify_shapes(shapes_file: Path, tolerance_m: float = 3.0) -> None:
    """
    Simplifies shapes.txt in place by dropping collinear points along straight lines,
    reducing points by ~63% and file size by ~65% while keeping precision within 3 meters.
    """
    if not shapes_file.exists():
        return

    shapes_by_id: dict[str, list[tuple[float, float, float]]] = {}
    with open(shapes_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = row["shape_id"]
            if sid not in shapes_by_id:
                shapes_by_id[sid] = []
            shapes_by_id[sid].append((
                float(row["shape_pt_lat"]),
                float(row["shape_pt_lon"]),
                float(row.get("shape_dist_traveled", 0)),
            ))

    total_before = sum(len(pts) for pts in shapes_by_id.values())
    total_after = 0

    tmp_file = shapes_file.with_suffix(".tmp")
    with open(tmp_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence", "shape_dist_traveled"])

        for sid, pts in shapes_by_id.items():
            if len(pts) <= 2:
                simplified = pts
            else:
                simplified = [pts[0]]
                for i in range(1, len(pts) - 1):
                    prev_pt = simplified[-1]
                    next_pt = pts[i + 1]
                    dist = point_to_segment_distance(
                        pts[i][0], pts[i][1],
                        prev_pt[0], prev_pt[1],
                        next_pt[0], next_pt[1],
                    )
                    if dist > tolerance_m:
                        simplified.append(pts[i])
                simplified.append(pts[-1])

            total_after += len(simplified)
            for seq, (lat, lon, d) in enumerate(simplified, start=1):
                writer.writerow([sid, f"{lat:.6f}", f"{lon:.6f}", seq, round(d, 1)])

    shutil.move(tmp_file, shapes_file)
    reduction = (1 - total_after / total_before) * 100 if total_before else 0
    print(f"✨ Simplified shapes: {total_before:,} -> {total_after:,} points (-{reduction:.1f}%)")


def generate_shapes(
    gtfs_dir: Path,
    osm_pbf: Path = DEFAULT_OSM_PBF,
    cfg_path: Path = DEFAULT_CFG,
    simplify_tolerance_m: float = 3.0,
) -> bool:
    """
    Runs pfaedle map-matching on gtfs_dir in place.
    Updates shapes.txt, trips.txt, stop_times.txt, and attributions.txt.
    Then simplifies shapes.txt for optimal file size.
    """
    if not osm_pbf.exists():
        print(f"⚠️  OSM PBF file not found at {osm_pbf}. Skipping shapes generation.")
        return False

    pfaedle_bin = find_pfaedle()
    print(f"🚆 Running pfaedle map-matcher ({pfaedle_bin})...")

    with tempfile.TemporaryDirectory(prefix="pfaedle_out_") as tmp_out:
        out_path = Path(tmp_out)
        cmd = [
            pfaedle_bin,
            "-c", str(cfg_path),
            "-x", str(osm_pbf),
            "-m", "rail",
            "-D",  # Drop existing and recalculate
            "-F",  # Preserve non-standard / extra GTFS fields
            "-o", str(out_path),
            str(gtfs_dir),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"❌ pfaedle failed (code {res.returncode}):\n{res.stderr}")
            return False

        # Copy generated shapes and updated tables back to gtfs_dir
        updated_files = ["shapes.txt", "trips.txt", "stop_times.txt", "attributions.txt"]
        for fname in updated_files:
            src = out_path / fname
            if src.exists():
                shutil.copy2(src, gtfs_dir / fname)

        shapes_file = gtfs_dir / "shapes.txt"
        if shapes_file.exists():
            simplify_shapes(shapes_file, tolerance_m=simplify_tolerance_m)
            line_count = sum(1 for _ in open(shapes_file, encoding="utf-8")) - 1
            print(f"✅ Final shapes.txt: {line_count:,} coordinate points")
        return True


def main() -> None:
    if len(sys.argv) > 1:
        gtfs_dir = Path(sys.argv[1])
    else:
        # Default to work directory or extract from zip
        gtfs_dir = REPO_ROOT / "work" / "gtfs_extracted"
        if not gtfs_dir.exists():
            zip_file = REPO_ROOT / "gtfs-italo.zip"
            if not zip_file.exists():
                sys.exit(f"GTFS directory or zip not found. Usage: {sys.argv[0]} <gtfs_dir>")
            import zipfile
            gtfs_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_file) as zf:
                zf.extractall(gtfs_dir)

    success = generate_shapes(gtfs_dir)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
