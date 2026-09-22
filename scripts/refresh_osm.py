#!/usr/bin/env python3
"""
Downloads the latest Italy OpenStreetMap extract from Geofabrik and filters it
into the two files generate_shapes.py map-matches against.

Rail and coach are filtered separately because pfaedle keeps every road class
within 20 km of *any* stop of the feed it is given. Filtering both modes at once
therefore drags in the residential streets of Milano, Roma, Torino and Bari,
where no ItaloBus ever runs: 205 MB, over GitHub's 100 MB per-file limit. Giving
the coach pass a feed holding only the coach trips narrows that to the 15 stops
they actually serve, and the two files together come to about 24 MB.

Intended to run once a month or on demand when the network changes.
"""

import csv
import io
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
import urllib.request

REPO_ROOT = Path(__file__).resolve().parent.parent
RAIL_PBF = REPO_ROOT / "data" / "italo-rail.osm.pbf"
BUS_PBF = REPO_ROOT / "data" / "italo-bus.osm.pbf"
CFG_FILE = REPO_ROOT / "scripts" / "pfaedle.cfg"
GTFS_ZIP = REPO_ROOT / "gtfs-italo.zip"
GEOFABRIK_URL = "https://download.geofabrik.de/europe/italy-latest.osm.pbf"


def write_coach_only_feed(gtfs_zip: Path, out_zip: Path) -> int:
    """
    Writes a copy of the feed holding only its coach trips, so pfaedle's bounding
    boxes cover the coach stops alone. Shapes are dropped (pfaedle recomputes
    them) and transfers pointing at the removed rail trips are filtered out,
    which the GTFS parser would otherwise reject.
    """
    with zipfile.ZipFile(gtfs_zip) as zf:
        names = set(zf.namelist())

        def read(name: str) -> list[dict[str, str]]:
            if name not in names:
                return []
            with zf.open(name) as f:
                return list(csv.DictReader(io.TextIOWrapper(f, "utf-8")))

        routes = read("routes.txt")
        trips = read("trips.txt")
        stop_times = read("stop_times.txt")
        transfers = read("transfers.txt")

        coach_routes = {r["route_id"] for r in routes if r.get("route_type") == "3"}
        coach_trips = {t["trip_id"] for t in trips if t["route_id"] in coach_routes}

        def dump(zout: zipfile.ZipFile, name: str, rows: list[dict[str, str]], fields: list[str]) -> None:
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in fields})
            zout.writestr(name, buf.getvalue())

        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zout:
            for name in ("agency.txt", "stops.txt", "calendar.txt", "calendar_dates.txt", "feed_info.txt"):
                if name in names:
                    zout.writestr(name, zf.read(name))

            dump(zout, "routes.txt", [r for r in routes if r["route_id"] in coach_routes], list(routes[0]))

            trip_fields = [f for f in trips[0] if f != "shape_id"]
            dump(zout, "trips.txt", [t for t in trips if t["trip_id"] in coach_trips], trip_fields)

            dump(zout, "stop_times.txt", [s for s in stop_times if s["trip_id"] in coach_trips], list(stop_times[0]))

            if transfers:
                kept = [
                    t for t in transfers
                    if (not t.get("from_trip_id") or t["from_trip_id"] in coach_trips)
                    and (not t.get("to_trip_id") or t["to_trip_id"] in coach_trips)
                ]
                dump(zout, "transfers.txt", kept, list(transfers[0]))

    return len(coach_trips)


def refresh_osm() -> None:
    from scripts.generate_shapes import find_pfaedle

    pfaedle_bin = find_pfaedle()

    with tempfile.TemporaryDirectory(prefix="osm_refresh_") as tmpdir:
        tmp = Path(tmpdir)
        tmp_pbf = tmp / "italy-latest.osm.pbf"
        print("Downloading latest Italy OSM extract from Geofabrik (~2.1 GB)...")
        urllib.request.urlretrieve(GEOFABRIK_URL, tmp_pbf)
        print(f"Downloaded ({tmp_pbf.stat().st_size / 1024 / 1024:.1f} MB)")

        coach_zip = tmp / "coach-only.zip"
        coach_trips = write_coach_only_feed(GTFS_ZIP, coach_zip)
        print(f"Coach-only feed: {coach_trips} trips")

        RAIL_PBF.parent.mkdir(parents=True, exist_ok=True)
        for mode, feed, target in (("rail", GTFS_ZIP, RAIL_PBF), ("bus", coach_zip, BUS_PBF)):
            print(f"Filtering {mode} network to {target.name}...")
            res = subprocess.run(
                [
                    pfaedle_bin,
                    "-c", str(CFG_FILE),
                    "-x", str(tmp_pbf),
                    "-X", str(target),
                    "-m", mode,
                    str(feed),
                ],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                sys.exit(f"Failed to filter {mode} OSM file:\n{res.stderr}")
            print(f"   {target.name}: {target.stat().st_size / 1024 / 1024:.2f} MB")

        total = (RAIL_PBF.stat().st_size + BUS_PBF.stat().st_size) / 1024 / 1024
        print(f"Updated OSM data ({total:.2f} MB total)")


if __name__ == "__main__":
    refresh_osm()
