#!/usr/bin/env python3
"""
Generates high-precision GTFS shapes.txt for Italo train trips using pfaedle
and pre-filtered OpenStreetMap railway infrastructure (data/italo-rail.osm.pbf).
"""

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


def generate_shapes(
    gtfs_dir: Path,
    osm_pbf: Path = DEFAULT_OSM_PBF,
    cfg_path: Path = DEFAULT_CFG,
) -> bool:
    """
    Runs pfaedle map-matching on gtfs_dir in place.
    Updates shapes.txt, trips.txt, stop_times.txt, and attributions.txt.
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
            line_count = sum(1 for _ in open(shapes_file, encoding="utf-8")) - 1
            print(f"✅ Generated shapes.txt ({line_count:,} coordinate points)")
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
