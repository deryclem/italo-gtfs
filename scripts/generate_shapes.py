#!/usr/bin/env python3
"""
Generates high-precision GTFS shapes.txt for Italo trips using pfaedle and the
pre-filtered OpenStreetMap extracts built by refresh_osm.py: rail infrastructure
(data/italo-rail.osm.pbf) and, around the coach stops only, the road network
(data/italo-bus.osm.pbf). Applies a fast pure-Python collinear decimation (3m
tolerance) to keep the GTFS lightweight.
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
RAIL_PBF = REPO_ROOT / "data" / "italo-rail.osm.pbf"
BUS_PBF = REPO_ROOT / "data" / "italo-bus.osm.pbf"
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
    Simplifies shapes.txt in place by dropping collinear points along straight lines.

    No longer part of the pipeline: it measures each point against the last point
    it kept rather than the original line, so the error compounds along a curve
    and a steady bend collapses into a chord. It cut Firenze - Lucca from 516
    points to 151, opening a 9 km straight line across the countryside.
    gtfstidy -s runs a true Douglas-Peucker in tidy_feed() instead.
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
    print(f"Simplified shapes: {total_before:,} -> {total_after:,} points (-{reduction:.1f}%)")


def _run_pass(pfaedle_bin: str, cfg_path: Path, osm_pbf: Path, mode: str, gtfs_dir: Path, out_path: Path) -> bool:
    """Map-matches one mode against its own OSM extract."""
    res = subprocess.run(
        [
            pfaedle_bin,
            "-c", str(cfg_path),
            "-x", str(osm_pbf),
            "-m", mode,
            "-D",  # Drop existing and recalculate
            "-F",  # Preserve non-standard / extra GTFS fields
            "-o", str(out_path),
            str(gtfs_dir),
        ],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        print(f"pfaedle failed on {mode} (code {res.returncode}):\n{res.stderr}")
        return False
    return True


def generate_shapes(
    gtfs_dir: Path,
    rail_pbf: Path = RAIL_PBF,
    bus_pbf: Path = BUS_PBF,
    cfg_path: Path = DEFAULT_CFG,
    simplify_tolerance_m: float = 3.0,
) -> bool:
    """
    Runs pfaedle map-matching on gtfs_dir in place, once per mode.

    Each mode gets its own OSM extract (see refresh_osm.py): keeping every road
    class around all 138 stations, rather than the 15 the coaches serve, costs
    205 MB against 24 MB for the two files. Each pass only writes shapes for its
    own trips, so the results are merged back on trip_id.
    """
    missing = [p for p in (rail_pbf, bus_pbf) if not p.exists()]
    if missing:
        print(f"OSM PBF file(s) not found: {', '.join(p.name for p in missing)}. Skipping shapes generation.")
        return False

    pfaedle_bin = find_pfaedle()
    print(f"Running pfaedle map-matcher ({pfaedle_bin})...")

    with tempfile.TemporaryDirectory(prefix="pfaedle_out_") as tmp_out:
        tmp = Path(tmp_out)
        rail_out, bus_out = tmp / "rail", tmp / "bus"
        for mode, pbf, out in (("rail", rail_pbf, rail_out), ("bus", bus_pbf, bus_out)):
            out.mkdir()
            if not _run_pass(pfaedle_bin, cfg_path, pbf, mode, gtfs_dir, out):
                return False

        merge_passes(gtfs_dir, [rail_out, bus_out])

        # Tables unrelated to shapes are identical in both passes.
        for fname in ("stop_times.txt", "attributions.txt", "transfers.txt"):
            src = rail_out / fname
            if src.exists():
                shutil.copy2(src, gtfs_dir / fname)

        shapes_file = gtfs_dir / "shapes.txt"
        if shapes_file.exists():
            line_count = sum(1 for _ in open(shapes_file, encoding="utf-8")) - 1
            print(f"Final shapes.txt: {line_count:,} coordinate points")
        return True


def merge_passes(gtfs_dir: Path, outs: list[Path]) -> None:
    """
    Merges the per-mode pfaedle runs: each pass assigns shape_ids to its own
    trips and leaves the others blank, so trips.txt takes whichever pass matched
    a trip, and shapes.txt keeps the geometries those ids point at.
    """
    shape_of: dict[str, str] = {}
    for out in outs:
        with open(out / "trips.txt", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("shape_id"):
                    shape_of[row["trip_id"]] = row["shape_id"]

    trips_file = gtfs_dir / "trips.txt"
    with open(trips_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        trips = list(reader)
        fields = list(reader.fieldnames or [])
    if "shape_id" not in fields:
        fields.append("shape_id")
    for row in trips:
        row["shape_id"] = shape_of.get(row["trip_id"], "")
    with open(trips_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(trips)

    kept = set(shape_of.values())
    rows: list[dict[str, str]] = []
    header: list[str] = []
    for out in outs:
        with open(out / "shapes.txt", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            header = header or list(reader.fieldnames or [])
            rows.extend(row for row in reader if row["shape_id"] in kept)
    with open(gtfs_dir / "shapes.txt", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)

    matched = sum(1 for row in trips if row["shape_id"])
    print(f"Merged {len(outs)} passes: {matched:,}/{len(trips):,} trips matched, {len(kept)} shapes")


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
