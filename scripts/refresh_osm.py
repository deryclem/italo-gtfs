#!/usr/bin/env python3
"""
Downloads the latest Italy OpenStreetMap extract from Geofabrik and filters
it to data/italo-rail.osm.pbf using pfaedle.

Intended to run once a month or on demand when railway infrastructure changes.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import urllib.request

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET_PBF = REPO_ROOT / "data" / "italo-network.osm.pbf"
CFG_FILE = REPO_ROOT / "scripts" / "pfaedle.cfg"
GTFS_ZIP = REPO_ROOT / "gtfs-italo.zip"
GEOFABRIK_URL = "https://download.geofabrik.de/europe/italy-latest.osm.pbf"


def refresh_osm() -> None:
    from scripts.generate_shapes import find_pfaedle

    pfaedle_bin = find_pfaedle()

    with tempfile.TemporaryDirectory(prefix="osm_refresh_") as tmpdir:
        tmp_pbf = Path(tmpdir) / "italy-latest.osm.pbf"
        print(f"📥 Downloading latest Italy OSM extract from Geofabrik (~700 MB)...")
        urllib.request.urlretrieve(GEOFABRIK_URL, tmp_pbf)
        print(f"✅ Downloaded ({tmp_pbf.stat().st_size / 1024 / 1024:.1f} MB)")

        TARGET_PBF.parent.mkdir(parents=True, exist_ok=True)
        print(f"🚆 Filtering rail and bus network for Italo to {TARGET_PBF}...")
        cmd = [
            pfaedle_bin,
            "-c", str(CFG_FILE),
            "-x", str(tmp_pbf),
            "-X", str(TARGET_PBF),
            "-m", "rail,bus",
            str(GTFS_ZIP),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            sys.exit(f"❌ Failed to filter OSM file:\n{res.stderr}")

        print(f"🎉 Updated {TARGET_PBF} ({TARGET_PBF.stat().st_size / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    refresh_osm()
