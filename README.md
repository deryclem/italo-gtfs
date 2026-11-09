# gtfs-italo

[![Updated: Weekly](https://img.shields.io/badge/Updated-Weekly%20(Mondays)-blue)](https://github.com/deryclem/italo-gtfs/actions)
[![License: CC BY 4.0](https://img.shields.io/badge/License-CC_BY_4.0-lightgrey.svg)](https://creativecommons.org/licenses/by/4.0/)

Unofficial [GTFS](https://gtfs.org) schedule feed for **Italo** (Nuovo Trasporto Viaggiatori S.p.A.), converted from official NeTEx timetable data published through Italy's National Access Point (NAP / CCISS).

This feed also feeds into [Panto](https://getpanto.app), a real-time train tracking app currently in beta.

**[Download latest GTFS package (`gtfs-italo.zip`)](./gtfs-italo.zip)**

---

## Services Covered

The feed covers Italo's national high-speed rail and connecting bus network published in their official NeTEx feed:

| Service | Brand | Type | Description |
|---|---|---|---|
| **Italo AV** | Italo | High-speed rail (`route_type=2`) | High-speed services on the AV/AC corridors (Turin, Milan, Venice, Bologna, Florence, Rome, Naples, Salerno, Reggio Calabria, etc.) |
| **ItaloBus** | ItaloBus | Connecting bus (`route_type=3`) | Coach services connecting high-speed rail hubs to regional destinations (Cortina d'Ampezzo, Sorrento, Lucca, etc.) |

---

## Where the data comes from

| Source | What it provides |
|---|---|
| [Italy's NAP (CCISS)](https://www.cciss.it/nap/mmtis/public/en/catalog/Dataset/1814124) | Italo's official NeTEx level 1 / 2 feed (Italian/EPIP profile), covering stops, lines, operational calendars, and timetabled passing times. |
| [MMTIS/badger](https://github.com/MMTIS/badger) | Core NeTEx -> GTFS conversion engine (pinned submodule in `vendor/badger`). |
| [OpenStreetMap](https://www.openstreetmap.org) via [Geofabrik](https://download.geofabrik.de/europe/italy.html) | Rail network and, around the coach stops only, road network used to draw `shapes.txt`. Filtered extracts are stored in `data/`. |
| [pfaedle](https://github.com/ad-freiburg/pfaedle) | Map-matches every trip onto the OSM network to produce its shape. |
| `generate.py` | Pipeline orchestration: streaming metadata extraction, calendar bitmask reconstruction, chronological stop sequencing, and GTFS adjustments. |

The feed downloads the NAP's `checkedResource` asset.

---

## Features & NeTEx Augmentations

- **Chronological Stop Sequencing**: Fixes an upstream NeTEx ordering bug where stop elements were sorted alphabetically by ID strings (`:10` before `:2`), so some stop times went backwards. Stop times now strictly increase across all trips.
- **Calendar Reconstruction**: Decodes NeTEx `<UicOperatingPeriod>` bitmasks (`ValidDayBits`) into a standard `calendar_dates.txt` and `calendar.txt`, overcoming Badger's inability to parse `<DaysOfWeek>Everyday</DaysOfWeek>`.
- **Train Numbers**: Commercial train and bus numbers (`trip_short_name`) are extracted directly from journey identifiers (e.g. `9954`, `8973`, `8192`).
- **Dynamic Headsigns**: `trip_headsign` is computed from the terminal stop name of each journey (e.g. `Milano Centrale`, `Roma Termini`, `Salerno`, `Torino Porta Nuova`).
- **Station Codes & Parent Stations**: `stop_code` is propagated to all parent stations in `stops.txt`, and station names are cleaned of redundant parenthetical city translations.
- **Commercial Restrictions**: `pickup_type=1` is populated at terminus arrivals and `drop_off_type=1` at trip departures in `stop_times.txt`.
- **Accessibility & Branding**:
  - `wheelchair_accessible=1` and `wheelchair_boarding=1` populated for all high-speed rail services and stations (Sala Blu equipped).
  - Official Italo ruby red branding (`#E30037`) with high-contrast white text (`#FFFFFF`) applied in `routes.txt`.
- **Trip Splitting**: A journey is split in two only when its NeTEx id names two trains (e.g. `8134-8920`). The split goes at the longest stop (at least 20 min), or where the second train is also published on its own with identical arrivals. Each leg keeps its own train number. A single train that merely waits at a station stays one trip. Journeys mixing rail and coach are split at the junction station.
- **Duplicate Removal**: The source repeats a train once per day type and once per onward connection. Trips with the same number, stops and arrival times are merged and their days combined. A trip that is the start or the end of a longer trip with the same number is dropped when the longer trip runs on every day it runs, so no service is lost.
- **Transfers**: `transfers.txt` links the legs of a split journey (bus to train, train to train) with the real waiting time as `min_transfer_time`, and adds 5-minute walking transfers inside Napoli Centrale, Napoli Afragola, Venezia Mestre and Firenze S.M.Novella. The NeTEx itself contains no transfer information, so only journeys the source packs together get a transfer.
- **Shapes**: `shapes.txt` is generated with pfaedle by map-matching each trip on OpenStreetMap, then simplified with a 3 m tolerance to keep the feed light. The OSM extracts in `data/` are refreshed on the 1st of each month by a separate workflow.
- **Metadata**: Standard `feed_info.txt` and `attributions.txt` generated with publisher metadata, validity dates, and publication timestamp.

---

## Known Limitations

- Some journeys chain two trains (e.g. `9928-8918`) without saying where the train changes. When the data does not prove the change point, the journey stays one trip.
- A few times differ by a couple of minutes from Italo's booking site. They come from the NeTEx itself.
- The cleanup rules only act on what the source gets wrong. If Italo fixes its NeTEx they find nothing to change. Background in [issue #2](https://github.com/deryclem/italo-gtfs/issues/2).

---

## Generating

Requires Python 3.12 and [uv](https://github.com/astral-sh/uv). Shapes also need the [pfaedle](https://github.com/ad-freiburg/pfaedle) binary on your `PATH`. [gtfstidy](https://github.com/patrickbr/gtfstidy) is used to tidy the final feed when it is installed.

```bash
git submodule update --init --recursive
cd vendor/badger && uv venv --python 3.12 && uv sync && sh scripts/generate-schema.sh && cd ../..
uv run --python 3.12 --with-requirements requirements.txt python3 generate.py
```

`generate.py` reuses `work/gtfs_raw.zip` while the NeTEx publication timestamp is unchanged, and rebuilds it when the timestamp changes.

To rebuild the OSM extracts in `data/`, run `python3 scripts/refresh_osm.py`.

Runs automatically every Monday via GitHub Actions. The OSM extracts are refreshed on the 1st of each month.

---

## License & Attribution

This GTFS dataset and generator code are licensed under the **[Creative Commons Attribution 4.0 International License (CC-BY-4.0)](https://creativecommons.org/licenses/by/4.0/)** © 2026 Clément Desouche ([@deryclem](https://github.com/deryclem)).

Adapted from official raw NeTEx data published by Italo S.p.A. via Italy's National Access Point (CCISS / Ministry of Infrastructure and Transport) under Italian CAD art. 52 and EU Regulation 2017/1926. Not affiliated with Italo S.p.A.
