# gtfs-italo

[![GTFS Validator](https://img.shields.io/badge/MobilityData%20Validator-0%20errors-brightgreen)](https://github.com/MobilityData/gtfs-validator)
[![Updated: Weekly](https://img.shields.io/badge/Updated-Weekly%20(Mondays)-blue)](https://github.com/deryclem/italo-gtfs/actions)
[![License: CC BY 4.0](https://img.shields.io/badge/License-CC_BY_4.0-lightgrey.svg)](https://creativecommons.org/licenses/by/4.0/)

Official [GTFS](https://gtfs.org) schedule feed for **Italo** (Nuovo Trasporto Viaggiatori S.p.A.), converted and enriched from official NeTEx timetable data published through Italy's National Access Point (NAP / CCISS).

This feed also feeds into [Panto](https://getpanto.app), a real-time train tracking app currently in beta.

📦 **[Download latest GTFS package (`gtfs-italo.zip`)](./gtfs-italo.zip)**

---

## 🚆 Services Covered

The feed covers Italo's national high-speed rail and connecting bus network published in their official NeTEx feed:

| Service | Brand | Type | Description |
|---|---|---|---|
| **Italo AV** | Italo | High-speed rail (`route_type=2`) | Premium high-speed services operating on dedicated AV/AC corridors (Turin, Milan, Venice, Bologna, Florence, Rome, Naples, Salerno, Reggio Calabria, etc.) |
| **ItaloBus** | ItaloBus | Connecting bus (`route_type=3`) | Integrated connecting motorcoach services linking high-speed rail hubs to regional destinations (Cortina d'Ampezzo, Sorrento, Lucca, etc.) |

---

## 📊 Where the data comes from

| Source | What it provides |
|---|---|
| [Italy's NAP (CCISS)](https://www.cciss.it/nap/mmtis/public/en/catalog/Dataset/1814124) | Italo's official NeTEx level 1 / 2 feed (Italian/EPIP profile), covering stops, lines, operational calendars, and timetabled passing times. |
| [MMTIS/badger](https://github.com/MMTIS/badger) | Core NeTEx -> GTFS conversion engine (pinned submodule in `vendor/badger`). |
| `generate.py` | Pipeline orchestration: streaming metadata extraction, calendar bitmask reconstruction, chronological stop sequencing, and GTFS enrichments. |

The feed uses the NAP's `checkedResource` download asset to guarantee seamless, active calendar coverage.

---

## 🔍 Features & NeTEx Augmentations

- **Chronological Stop Sequencing**: Corrects a critical upstream NeTEx serialization flaw where stop elements were sorted alphabetically by ID strings (`:10` before `:2`), fixing 683 out-of-order time-travel anomalies and ensuring strictly increasing stop times across all trips.
- **Calendar Reconstruction**: Decodes NeTEx `<UicOperatingPeriod>` bitmasks (`ValidDayBits`) into a complete, standard-compliant `calendar_dates.txt` and `calendar.txt`, overcoming Badger's inability to parse `<DaysOfWeek>Everyday</DaysOfWeek>`.
- **Train Numbers**: Commercial train and bus numbers (`trip_short_name`) are extracted directly from journey identifiers (e.g. `9954`, `8973`, `8192`).
- **Dynamic Headsigns**: `trip_headsign` is computed from the terminal stop name of each journey (e.g. `Milano Centrale`, `Roma Termini`, `Salerno`, `Torino Porta Nuova`).
- **Station Codes & Parent Stations**: `stop_code` is propagated to all parent stations in `stops.txt`, and station names are cleaned of redundant parenthetical city translations.
- **Commercial Restrictions**: `pickup_type=1` is populated at terminus arrivals and `drop_off_type=1` at trip departures in `stop_times.txt`.
- **Accessibility & Branding**:
  - `wheelchair_accessible=1` and `wheelchair_boarding=1` populated for all high-speed rail services and stations (Sala Blu equipped).
  - Official Italo ruby red branding (`#E30037`) with high-contrast white text (`#FFFFFF`) applied in `routes.txt`.
- **Metadata**: Standard `feed_info.txt` and `attributions.txt` generated with publisher metadata, validity dates, and publication timestamp.

---

## ⚙️ Generating

Requires Python 3.12 and [uv](https://github.com/astral-sh/uv).

```bash
git submodule update --init --recursive
cd vendor/badger && uv venv --python 3.12 && uv sync && sh scripts/generate-schema.sh && cd ../..
uv run --python 3.12 --with-requirements requirements.txt python3 generate.py
```

Runs automatically every Monday via GitHub Actions.

---

## 📄 License & Attribution

This GTFS dataset and generator code are licensed under the **[Creative Commons Attribution 4.0 International License (CC-BY-4.0)](https://creativecommons.org/licenses/by/4.0/)** © 2026 Clément Desouche ([@deryclem](https://github.com/deryclem)).

Adapted from official raw NeTEx data published by Italo S.p.A. via Italy's National Access Point (CCISS / Ministry of Infrastructure and Transport) under Italian CAD art. 52 and EU Regulation 2017/1926. Not affiliated with Italo S.p.A.
