#!/usr/bin/env python3
"""
Italo GTFS generator.

Downloads Italo's official NeTEx feed from the Italian National Access Point (NAP / CCISS),
converts it to GTFS via badger (vendor/badger), enriches it with official metadata,
fixes chronological stop sequencing and calendar bitmasks, and packages a validated
GTFS feed (gtfs-italo.zip).

Usage:
    python3 generate.py
"""

import csv
import gzip
import re
import shutil
import subprocess
import sys
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import requests  # pip install requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_retry = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
HTTP = requests.Session()
HTTP.mount("https://", HTTPAdapter(max_retries=_retry))


# ── Settings ──────────────────────────────────────────────────────────────────

OUTPUT_ZIP = Path("gtfs-italo.zip")

# Italian NAP (National Access Point / CCISS) asset for Italo NeTEx Livello 1 feed.
# Catalog dataset: https://www.cciss.it/nap/mmtis/public/en/catalog/Dataset/1814124
# Uses "checkedResource" to ensure consistent and active coverage dates.
NETEX_URL = "https://www.cciss.it/nap/mmtis/public/api/v1/download/blob/Asset/1814124/checkedResource"

BADGER_DIR = Path("vendor/badger")
BADGER_PYTHON = BADGER_DIR / ".venv" / "bin" / "python3"

WORK_DIR = Path("work")
NETEX_GZ = WORK_DIR / "italo_netex.xml.gz"
NETEX_DB = WORK_DIR / "netex.mdbx"
GTFS_DB = WORK_DIR / "gtfs.mdbx"

# Sanity check identifiers
EXPECTED_OPERATOR_NAME = "Italo"

# Official agency metadata
AGENCY_ID = "IT::Operator:1"
AGENCY_NAME = "Italo - Nuovo Trasporto Viaggiatori"
AGENCY_URL = "https://www.italotreno.it"
AGENCY_TIMEZONE = "Europe/Rome"
AGENCY_LANG = "it"
AGENCY_PHONE = "+39 06 07 08"
AGENCY_EMAIL = "italo@pec.ntvspa.it"

# Italo branding colors
ROUTE_COLOR = "E30037"       # Official Italo Ruby Red
ROUTE_TEXT_COLOR = "FFFFFF"  # High-contrast white text

STALE_STATE_FILE = Path(".last_publication_timestamp")


# ── Download & Freshness ──────────────────────────────────────────────────────

def download_netex() -> Path:
    print(f"Downloading NeTEx feed from {NETEX_URL}")
    WORK_DIR.mkdir(exist_ok=True)
    with HTTP.get(NETEX_URL, stream=True, timeout=120) as response:
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "gzip" not in content_type:
            sys.exit(f"Unexpected Content-Type: {content_type!r} (expected gzip)")
        with open(NETEX_GZ, "wb") as f:
            shutil.copyfileobj(response.raw, f)
    print(f"Downloaded {NETEX_GZ.stat().st_size:,} bytes")
    return NETEX_GZ


def read_publication_timestamp_and_operator(netex_gz: Path) -> tuple[str, bool]:
    """
    Reads just enough of the NeTEx file to pull PublicationTimestamp
    and confirm Italo's operator declaration is present.
    """
    publication_timestamp = None
    has_operator = False
    with gzip.open(netex_gz, "rt", encoding="utf-8") as f:
        for _ in range(500):
            line = f.readline()
            if not line:
                break
            if publication_timestamp is None:
                m = re.search(r"<PublicationTimestamp>([^<]+)</PublicationTimestamp>", line)
                if m:
                    publication_timestamp = m.group(1)
            if EXPECTED_OPERATOR_NAME in line or "IT::Operator:1" in line:
                has_operator = True
            if publication_timestamp and has_operator:
                break
        else:
            if not has_operator:
                f.seek(0)
                for line in f:
                    if EXPECTED_OPERATOR_NAME in line or "IT::Operator:1" in line:
                        has_operator = True
                        break

    if publication_timestamp is None:
        sys.exit("Could not find PublicationTimestamp in downloaded NeTEx file")
    return publication_timestamp, has_operator


def check_freshness(publication_timestamp: str) -> None:
    if STALE_STATE_FILE.exists():
        last_timestamp = STALE_STATE_FILE.read_text().strip()
        if last_timestamp == publication_timestamp:
            print(f"Warning: PublicationTimestamp unchanged since last run ({publication_timestamp})")


# ── Metadata Streaming Extraction ─────────────────────────────────────────────

@dataclass
class ItaloMetadata:
    calendar_dates: dict[str, list[str]]           # service_id -> sorted list of active YYYYMMDD dates
    calendar_ranges: dict[str, tuple[str, str]]    # service_id -> (start_date, end_date)
    train_numbers: dict[str, str]                  # trip_id -> train number (e.g. "9954", "8973 / 1060")
    stopplace_codes: dict[str, str]                # stop_id -> 3-letter station code (e.g. "MC_", "TOP")


def extract_train_number(trip_id: str) -> str:
    """
    Extracts commercial train/bus number from trip identifier.
    e.g. 'IT::ServiceJourney:9992--1-1-2-1' -> '9992'
         'IT::ServiceJourney:8973-1060-1-2-1' -> '8973 / 1060'
         'IT::ServiceJourney:1004-8192-1-1-1' -> '8192'
    """
    raw = trip_id.split(":")[-1]
    m = re.match(r"^(\d+)(?:-(\d+))?", raw)
    if m:
        p1 = m.group(1)
        p2 = m.group(2)
        if p2 and int(p2) > 10:
            return p1 if p1 == p2 else f"{p1} / {p2}"
        return p1
    return raw


def read_netex_metadata(netex_gz: Path) -> ItaloMetadata:
    """
    Extracts calendar bitmasks, service dates, train numbers and station codes
    directly from NeTEx XML in a single low-memory streaming pass.
    """
    pattern_period = re.compile(
        r'<UicOperatingPeriod id="([^"]+)"[^>]*>.*?'
        r'<FromDate>([^<]+)</FromDate>.*?'
        r'<ToDate>([^<]+)</ToDate>.*?'
        r'<ValidDayBits>([^<]+)</ValidDayBits>',
        re.DOTALL,
    )
    pattern_sj = re.compile(r'<ServiceJourney id="([^"]+)"', re.DOTALL)
    pattern_sp = re.compile(r'<StopPlace id="([^"]+)"', re.DOTALL)

    calendar_dates: dict[str, list[str]] = {}
    calendar_ranges: dict[str, tuple[str, str]] = {}
    train_numbers: dict[str, str] = {}
    stopplace_codes: dict[str, str] = {}

    with gzip.open(netex_gz, "rt", encoding="utf-8") as f:
        overlap = ""
        while True:
            chunk = f.read(4 * 1024 * 1024)
            if not chunk:
                break
            text = overlap + chunk

            # Parse UicOperatingPeriod to build calendar dates
            for m in pattern_period.finditer(text):
                period_id = m.group(1)
                from_str = m.group(2)
                to_str = m.group(3)
                bits = m.group(4).strip()

                service_id = period_id.replace("IT::UicOperatingPeriod:", "IT::DayType:")
                d_from = datetime.fromisoformat(from_str).date()
                d_to = datetime.fromisoformat(to_str).date()

                start_date = d_from.strftime("%Y%m%d")
                end_date = d_to.strftime("%Y%m%d")
                calendar_ranges[service_id] = (start_date, end_date)

                active_dates = []
                for i, bit in enumerate(bits):
                    if bit == "1":
                        dt = d_from + timedelta(days=i)
                        active_dates.append(dt.strftime("%Y%m%d"))
                calendar_dates[service_id] = active_dates

            # Parse ServiceJourney IDs to extract train numbers
            for m in pattern_sj.finditer(text):
                sj_id = m.group(1)
                train_numbers[sj_id] = extract_train_number(sj_id)

            # Parse StopPlace IDs to extract stop codes (e.g. IT::StopPlace:TOP -> TOP)
            for m in pattern_sp.finditer(text):
                sp_id = m.group(1)
                code = sp_id.split(":")[-1].replace("-", "_")
                stopplace_codes[sp_id] = code

            overlap = text[-65536:]

    return ItaloMetadata(
        calendar_dates=calendar_dates,
        calendar_ranges=calendar_ranges,
        train_numbers=train_numbers,
        stopplace_codes=stopplace_codes,
    )


# ── Conversion (Badger) ───────────────────────────────────────────────────────

def run_badger_step(*args: str) -> None:
    python = BADGER_PYTHON.absolute() if BADGER_PYTHON.exists() else Path(sys.executable).absolute()
    cmd = [str(python), "-m", *args]
    print(f"Running Badger: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=BADGER_DIR, check=True)


def convert_to_gtfs(netex_gz: Path) -> Path:
    netex_gz_abs = netex_gz.resolve()
    netex_db_abs = NETEX_DB.resolve()
    gtfs_db_abs = GTFS_DB.resolve()
    gtfs_zip_abs = (WORK_DIR / "gtfs_raw.zip").resolve()

    for stale in (netex_db_abs, gtfs_db_abs):
        if stale.exists():
            shutil.rmtree(stale)

    run_badger_step("conv.netex_to_db", str(netex_gz_abs), str(netex_db_abs))
    run_badger_step("conv.gtfs_db_to_db", str(netex_db_abs), str(gtfs_db_abs))
    run_badger_step("conv.gtfs_db_to_gtfs", str(gtfs_db_abs), str(gtfs_zip_abs))

    return gtfs_zip_abs


# ── Post-processing & Enrichments ─────────────────────────────────────────────

def clean_station_name(name: str) -> str:
    """
    Strips trailing English annotations from station names:
    'Milano Centrale (Milan)' -> 'Milano Centrale'
    'Torino Porta Nuova (Turin)' -> 'Torino Porta Nuova'
    'Roma Termini (Rome)' -> 'Roma Termini'
    'Salerno (Amalfi Coast)' -> 'Salerno'
    """
    return re.sub(r"\s*\([^)]*\)$", "", name).strip()


def post_process(gtfs_raw_zip: Path, metadata: ItaloMetadata, publication_timestamp: str) -> None:
    extract_dir = WORK_DIR / "gtfs_extracted"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir()

    with zipfile.ZipFile(gtfs_raw_zip) as zf:
        zf.extractall(extract_dir)

    # 1. agency.txt
    agency_path = extract_dir / "agency.txt"
    with open(agency_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "agency_id",
                "agency_name",
                "agency_url",
                "agency_timezone",
                "agency_lang",
                "agency_phone",
                "agency_fare_url",
                "agency_email",
            ],
        )
        writer.writeheader()
        writer.writerow({
            "agency_id": AGENCY_ID,
            "agency_name": AGENCY_NAME,
            "agency_url": AGENCY_URL,
            "agency_timezone": AGENCY_TIMEZONE,
            "agency_lang": AGENCY_LANG,
            "agency_phone": AGENCY_PHONE,
            "agency_fare_url": AGENCY_URL,
            "agency_email": AGENCY_EMAIL,
        })

    # 2. stops.txt
    stops_path = extract_dir / "stops.txt"
    stops_map: dict[str, str] = {}
    bus_stop_ids: set[str] = set()

    with open(stops_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        stop_rows = list(reader)
        fieldnames = list(reader.fieldnames or [])

    if "wheelchair_boarding" not in fieldnames:
        fieldnames.append("wheelchair_boarding")

    # Pass 1: Collect child codes and detect bus stops
    for row in stop_rows:
        raw_name = row.get("stop_name", "")
        cleaned = clean_station_name(raw_name)
        row["stop_name"] = cleaned
        stops_map[row["stop_id"]] = cleaned

        if "BUS" in raw_name or "BUS" in row["stop_id"]:
            bus_stop_ids.add(row["stop_id"])
            if row.get("parent_station"):
                bus_stop_ids.add(row["parent_station"])

    # Pass 2: Fill stop_code and wheelchair_boarding
    for row in stop_rows:
        stop_id = row["stop_id"]
        loc_type = row.get("location_type", "0")
        is_bus = stop_id in bus_stop_ids or row.get("parent_station") in bus_stop_ids

        # Set wheelchair accessibility: 1 for AV train stations, 0 for bus stops
        row["wheelchair_boarding"] = "0" if is_bus else "1"

        # Propagate stop_code for parent stations if missing
        if loc_type == "1" and not row.get("stop_code"):
            row["stop_code"] = metadata.stopplace_codes.get(stop_id, stop_id.split(":")[-1])

    with open(stops_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(stop_rows)

    # 3. stop_times.txt (CRITICAL: sort chronologically to fix string-sorted XML IDs)
    st_path = extract_dir / "stop_times.txt"
    with open(st_path, newline="", encoding="utf-8") as f:
        st_rows = list(csv.DictReader(f))

    trip_stop_times = defaultdict(list)
    trip_is_bus: set[str] = set()
    trip_destinations: dict[str, str] = {}

    for row in st_rows:
        trip_stop_times[row["trip_id"]].append(row)
        if row["stop_id"] in bus_stop_ids:
            trip_is_bus.add(row["trip_id"])

    fixed_st_rows = []
    for trip_id, stops in trip_stop_times.items():
        # Sort chronologically by arrival or departure time
        sorted_stops = sorted(stops, key=lambda x: x["arrival_time"] or x["departure_time"])
        total = len(sorted_stops)

        for seq, row in enumerate(sorted_stops, start=1):
            row["stop_sequence"] = str(seq)
            row["timepoint"] = "1"

            # Commercial restrictions: no alighting at origin, no boarding at terminus
            if seq == 1:
                row["drop_off_type"] = "1"
            elif seq == total:
                row["pickup_type"] = "1"

            fixed_st_rows.append(row)

        last_stop_id = sorted_stops[-1]["stop_id"]
        trip_destinations[trip_id] = stops_map.get(last_stop_id, "")

    with open(st_path, "w", newline="", encoding="utf-8") as f:
        st_fieldnames = [
            "trip_id",
            "arrival_time",
            "departure_time",
            "stop_id",
            "stop_sequence",
            "stop_headsign",
            "pickup_type",
            "drop_off_type",
            "continuous_pickup",
            "continuous_drop_off",
            "shape_dist_traveled",
            "timepoint",
        ]
        writer = csv.DictWriter(f, fieldnames=st_fieldnames)
        writer.writeheader()
        writer.writerows(fixed_st_rows)

    # 4. routes.txt
    routes_path = extract_dir / "routes.txt"
    with open(routes_path, newline="", encoding="utf-8") as f:
        route_rows = list(csv.DictReader(f))
        r_fields = list(route_rows[0].keys()) if route_rows else []

    # Map each route to bus vs high-speed rail based on trips
    trip_to_route = {}
    trips_path = extract_dir / "trips.txt"
    with open(trips_path, newline="", encoding="utf-8") as f:
        trip_rows = list(csv.DictReader(f))
        for row in trip_rows:
            trip_to_route[row["trip_id"]] = row["route_id"]

    bus_routes: set[str] = set()
    for tid in trip_is_bus:
        if tid in trip_to_route:
            bus_routes.add(trip_to_route[tid])

    for r in route_rows:
        r_id = r["route_id"]
        is_bus_route = r_id in bus_routes

        r["agency_id"] = AGENCY_ID
        r["route_type"] = "3" if is_bus_route else "2"
        r["route_short_name"] = "ItaloBus" if is_bus_route else "Italo"
        r["route_long_name"] = "ItaloBus (Collegamento)" if is_bus_route else "Italo AV (Alta Velocità)"
        r["route_color"] = ROUTE_COLOR
        r["route_text_color"] = ROUTE_TEXT_COLOR

    with open(routes_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=r_fields)
        writer.writeheader()
        writer.writerows(route_rows)

    # 5. trips.txt
    t_fields = list(trip_rows[0].keys()) if trip_rows else []
    for extra in ("trip_headsign", "trip_short_name", "wheelchair_accessible", "bikes_allowed"):
        if extra not in t_fields:
            t_fields.append(extra)

    for r in trip_rows:
        tid = r["trip_id"]
        is_bus = tid in trip_is_bus

        # Train / Bus Number
        r["trip_short_name"] = metadata.train_numbers.get(tid, extract_train_number(tid))

        # Headsign from destination stop
        r["trip_headsign"] = trip_destinations.get(tid, "")

        # Accessibility
        r["wheelchair_accessible"] = "0" if is_bus else "1"
        r["bikes_allowed"] = "0" if is_bus else "1"

    with open(trips_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=t_fields)
        writer.writeheader()
        writer.writerows(trip_rows)

    # 6. calendar.txt & calendar_dates.txt
    cal_path = extract_dir / "calendar.txt"
    cal_dates_path = extract_dir / "calendar_dates.txt"

    all_dates: list[str] = []
    calendar_dates_rows = []
    calendar_rows = []

    for service_id, dates in sorted(metadata.calendar_dates.items()):
        for d in dates:
            all_dates.append(d)
            calendar_dates_rows.append({
                "service_id": service_id,
                "date": d,
                "exception_type": "1",  # Service added for this date
            })

        start_date, end_date = metadata.calendar_ranges.get(
            service_id, (min(dates) if dates else "", max(dates) if dates else "")
        )

        calendar_rows.append({
            "service_id": service_id,
            "monday": "0",
            "tuesday": "0",
            "wednesday": "0",
            "thursday": "0",
            "friday": "0",
            "saturday": "0",
            "sunday": "0",
            "start_date": start_date,
            "end_date": end_date,
        })

    with open(cal_dates_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["service_id", "date", "exception_type"])
        writer.writeheader()
        writer.writerows(calendar_dates_rows)

    with open(cal_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "service_id",
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
                "start_date",
                "end_date",
            ],
        )
        writer.writeheader()
        writer.writerows(calendar_rows)

    # 7. feed_info.txt
    min_date = min(all_dates) if all_dates else ""
    max_date = max(all_dates) if all_dates else ""

    feed_info_path = extract_dir / "feed_info.txt"
    with open(feed_info_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "feed_publisher_name",
            "feed_publisher_url",
            "feed_lang",
            "feed_start_date",
            "feed_end_date",
            "feed_version",
            "feed_license_url",
            "feed_contact_url",
            "feed_contact_email",
        ])
        writer.writerow([
            "Clément Desouche (@deryclem) (via data from Italo S.p.A. / CCISS MMTIS)",
            "https://github.com/deryclem/italo-gtfs",
            AGENCY_LANG,
            min_date,
            max_date,
            publication_timestamp or "",
            "https://creativecommons.org/licenses/by/4.0/",
            "https://github.com/deryclem/italo-gtfs",
            "cdesouche28@gmail.com",
        ])

    # 8. attributions.txt
    attributions_path = extract_dir / "attributions.txt"
    with open(attributions_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "attribution_id",
            "organization_name",
            "is_producer",
            "is_operator",
            "attribution_url",
        ])
        writer.writerow([
            "source-data",
            "Italo S.p.A. / CCISS MMTIS",
            "1",
            "1",
            "https://www.cciss.it/nap/mmtis/public/en/catalog/Dataset/1814124",
        ])
        writer.writerow([
            "converter",
            "Clément Desouche (@deryclem)",
            "0",
            "0",
            "https://github.com/deryclem/italo-gtfs",
        ])

    # 9. Generate shapes via pfaedle (if available)
    try:
        from scripts.generate_shapes import generate_shapes
        generate_shapes(extract_dir)
    except Exception as e:
        print(f"⚠️  Could not generate shapes: {e}")

    # 10. Package final GTFS zip
    if OUTPUT_ZIP.exists():
        OUTPUT_ZIP.unlink()

    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for file in sorted(extract_dir.iterdir()):
            zf.write(file, arcname=file.name)

    print(f"Packaged {OUTPUT_ZIP} ({OUTPUT_ZIP.stat().st_size:,} bytes)")


# ── Sanity Checks ─────────────────────────────────────────────────────────────

def sanity_check(gtfs_zip: Path) -> None:
    print(f"Running sanity checks on {gtfs_zip}...")
    with zipfile.ZipFile(gtfs_zip) as zf:
        names = set(zf.namelist())
        required = {
            "agency.txt",
            "routes.txt",
            "stops.txt",
            "trips.txt",
            "stop_times.txt",
            "calendar.txt",
            "calendar_dates.txt",
            "feed_info.txt",
        }
        missing = required - names
        if missing:
            sys.exit(f"Generated GTFS is missing required files: {sorted(missing)}")

        with zf.open("stops.txt") as f:
            stops = list(csv.DictReader(line.decode("utf-8") for line in f))
            stop_ids = {row["stop_id"] for row in stops}
            parent_without_code = [
                row["stop_id"] for row in stops if row.get("location_type") == "1" and not row.get("stop_code")
            ]
            if parent_without_code:
                sys.exit(f"{len(parent_without_code)} parent stations are missing stop_code")

        with zf.open("stop_times.txt") as f:
            st = list(csv.DictReader(line.decode("utf-8") for line in f))
            used_stop_ids = {row["stop_id"] for row in st}

            # Check strictly increasing stop times (no time travel)
            trip_times = defaultdict(list)
            for row in st:
                trip_times[row["trip_id"]].append((
                    int(row["stop_sequence"]),
                    row["arrival_time"],
                    row["departure_time"],
                ))

            time_travel_trips = []
            for tid, times in trip_times.items():
                sorted_by_seq = sorted(times, key=lambda x: x[0])
                for i in range(len(sorted_by_seq) - 1):
                    if sorted_by_seq[i][2] > sorted_by_seq[i + 1][1]:
                        time_travel_trips.append(tid)
                        break

            if time_travel_trips:
                sys.exit(f"{len(time_travel_trips)} trips have decreasing stop times (e.g. {time_travel_trips[:5]})")

        orphans = used_stop_ids - stop_ids
        if orphans:
            sys.exit(f"stop_times.txt references {len(orphans)} stop_ids missing from stops.txt")

        if len(stop_ids) == 0 or len(used_stop_ids) == 0:
            sys.exit("Generated GTFS has empty stops or stop_times")

        with zf.open("calendar_dates.txt") as f:
            cal_dates = list(csv.DictReader(line.decode("utf-8") for line in f))
            if len(cal_dates) == 0:
                sys.exit("calendar_dates.txt is unexpectedly empty")

    print("All sanity checks passed successfully! (0 errors)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if NETEX_GZ.exists():
        print(f"Using existing downloaded NeTEx feed at {NETEX_GZ}")
        netex_gz = NETEX_GZ
    else:
        netex_gz = download_netex()

    publication_timestamp, has_operator = read_publication_timestamp_and_operator(netex_gz)
    if not has_operator:
        sys.exit(f"Downloaded feed does not contain Italo's operator id ({EXPECTED_OPERATOR_NAME}); refusing to proceed")

    check_freshness(publication_timestamp)
    metadata = read_netex_metadata(netex_gz)

    # Convert via Badger if raw zip does not exist yet
    gtfs_raw_zip = WORK_DIR / "gtfs_raw.zip"
    if not gtfs_raw_zip.exists():
        gtfs_raw_zip = convert_to_gtfs(netex_gz)
    else:
        print(f"Using existing raw GTFS zip at {gtfs_raw_zip}")

    post_process(gtfs_raw_zip, metadata, publication_timestamp)
    sanity_check(OUTPUT_ZIP)

    STALE_STATE_FILE.write_text(publication_timestamp)
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    print(f"\nSuccessfully generated {OUTPUT_ZIP} (PublicationTimestamp: {publication_timestamp})")


if __name__ == "__main__":
    main()
