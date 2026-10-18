"""Build data/transit_index.json from the official LA Metro bus GTFS. Owner: Dev B, ticket B13 (#47).

    python scripts/build_transit.py                       # downloads the feed (~22 MB) to a temp dir
    python scripts/build_transit.py --zip gtfs_bus.zip    # use a feed you already downloaded

For every neighborhood centroid in data/places.json and every school in data/schools.json, finds the
Metro bus lines that go directly (no transfer) from a stop near home to a stop near school, on a weekday
morning, and records the median *scheduled* ride time. The agent only reads the index at runtime: no
network, no live arrivals, no routing engine. Times are what the timetable says, never a promise.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import statistics
import sys
import tempfile
import urllib.request
import zipfile
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import load_config, load_data  # noqa: E402
from core.matcher import haversine_km  # noqa: E402

OUT = ROOT / "data" / "transit_index.json"


def _rows(z: zipfile.ZipFile, name: str):
    with z.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))


def _minutes(hms: str) -> float:
    h, m, s = (int(x) for x in hms.strip().split(":"))  # GTFS allows hours >= 24
    return h * 60 + m + s / 60


def _active_services(z: zipfile.ZipFile, day: date) -> set[str]:
    """Service ids running on `day`: calendar.txt weekday flags, then calendar_dates.txt exceptions."""
    weekday = day.strftime("%A").lower()
    ymd = day.strftime("%Y%m%d")
    active = {r["service_id"] for r in _rows(z, "calendar.txt")
              if r[weekday] == "1" and r["start_date"] <= ymd <= r["end_date"]}
    if "calendar_dates.txt" in z.namelist():
        for r in _rows(z, "calendar_dates.txt"):
            if r["date"] == ymd:
                (active.add if r["exception_type"] == "1" else active.discard)(r["service_id"])
    return active


def _near(points: dict[str, tuple[float, float]], stops: dict[str, tuple[float, float]], radius_m: float):
    """{stop_id: {point_key: walk_m}} for stops within radius of each point (straight-line meters)."""
    out: dict[str, dict[str, int]] = defaultdict(dict)
    for key, (lat, lng) in points.items():
        for sid, (slat, slng) in stops.items():
            if abs(slat - lat) > 0.01 or abs(slng - lng) > 0.012:  # cheap box before haversine
                continue
            m = haversine_km(lat, lng, slat, slng) * 1000
            if m <= radius_m:
                out[sid][key] = round(m)
    return out


def build(zip_path: Path) -> dict:
    cfg = load_config("transit")
    z = zipfile.ZipFile(zip_path)
    feed = next(_rows(z, "feed_info.txt"), {})
    day = date.fromisoformat(cfg["reference_weekday"])
    services = _active_services(z, day)
    start, end = (_minutes(t) for t in cfg["board_window"])

    places = {k: (v["lat"], v["lng"]) for k, v in load_data("places")["neighborhoods"].items()}
    schools = {s["code"]: (s["lat"], s["lng"]) for s in load_data("schools") if s.get("lat") and s.get("lng")}
    stop_rows = list(_rows(z, "stops.txt"))
    stops = {r["stop_id"]: (float(r["stop_lat"]), float(r["stop_lon"])) for r in stop_rows}
    stop_names = {r["stop_id"]: r["stop_name"].strip() for r in stop_rows}
    near_home = _near(places, stops, cfg["home_radius_m"])
    near_school = _near(schools, stops, cfg["school_radius_m"])

    routes = {r["route_id"]: r["route_short_name"] or r["route_id"] for r in _rows(z, "routes.txt")}
    trips = {r["trip_id"]: routes.get(r["route_id"], r["route_id"])
             for r in _rows(z, "trips.txt") if r["service_id"] in services}

    # One pass over stop_times: keep only the stops near a home or a school, per running trip.
    hits: dict[str, list[tuple[int, float, str]]] = defaultdict(list)
    for r in _rows(z, "stop_times.txt"):
        sid = r["stop_id"]
        if r["trip_id"] in trips and (sid in near_home or sid in near_school):
            t = r["departure_time"] or r["arrival_time"]
            if t:
                hits[r["trip_id"]].append((int(r["stop_sequence"]), _minutes(t), sid))

    max_km, min_km = load_config("matcher")["max_km"], cfg["min_km"]
    pair_ok = {(p, c) for p, pc in places.items() for c, sc in schools.items()
               if min_km <= haversine_km(*pc, *sc) <= max_km}

    # rides[(place, code, route)] -> list of (minutes, board_stop, alight_stop, walk_home, walk_school)
    rides: dict[tuple[str, str, str], list] = defaultdict(list)
    for trip_id, seq in hits.items():
        seq.sort()
        route = trips[trip_id]
        best: dict[tuple[str, str], tuple] = {}
        for i, (_, t_board, s_board) in enumerate(seq):
            if not start <= t_board <= end:
                continue
            for place, walk_h in near_home.get(s_board, {}).items():
                for _, t_alight, s_alight in seq[i + 1:]:
                    for code, walk_s in near_school.get(s_alight, {}).items():
                        if (place, code) not in pair_ok:
                            continue
                        cand = (walk_h + walk_s, t_alight - t_board, s_board, s_alight, walk_h, walk_s)
                        if (place, code) not in best or cand < best[(place, code)]:
                            best[(place, code)] = cand  # nearest stop pair on this trip
        for (place, code), (_, mins, sb, sa, wh, ws) in best.items():
            rides[(place, code, route)].append((mins, sb, sa, wh, ws))

    index: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for (place, code, route), trips_ in rides.items():
        if len(trips_) < cfg["min_trips"]:
            continue  # a line that passes once in the morning isn't a way to get to school
        common = max(set((t[1], t[2]) for t in trips_), key=[(t[1], t[2]) for t in trips_].count)
        same = [t for t in trips_ if (t[1], t[2]) == common]
        index[place][code].append({
            "route": route,
            "minutes": round(statistics.median(t[0] for t in same)),
            "board_stop": stop_names.get(common[0], common[0]),
            "alight_stop": stop_names.get(common[1], common[1]),
            "walk_home_m": same[0][3],
            "walk_m": same[0][4],
            "trips": len(trips_),
        })
    for place in index:
        for code in index[place]:
            index[place][code] = sorted(index[place][code], key=lambda o: (o["minutes"], o["walk_m"]))[:cfg["max_routes"]]

    return {
        "source_url": cfg["source_url"],
        "terms_url": cfg["terms_url"],
        "feed_publisher": feed.get("feed_publisher_name"),
        "feed_start_date": feed.get("feed_start_date"),
        "feed_end_date": feed.get("feed_end_date"),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": (f"Direct Metro bus lines (no transfers) on {cfg['reference_weekday']} ({day.strftime('%A')}), "
                   f"boarding {cfg['board_window'][0]}-{cfg['board_window'][1]} within {cfg['home_radius_m']} m "
                   f"of the neighborhood centroid, getting off within {cfg['school_radius_m']} m of the school. "
                   "minutes = median scheduled ride on the most common stop pair; walk_m = stop to school, "
                   "walk_home_m = centroid to stop (straight line)."),
        "places": {p: dict(sorted(c.items())) for p, c in sorted(index.items())},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zip", type=Path, help="a downloaded gtfs_bus.zip (default: download it)")
    args = ap.parse_args()
    zip_path = args.zip
    if zip_path is None:
        zip_path = Path(tempfile.mkdtemp()) / "gtfs_bus.zip"
        url = load_config("transit")["source_url"]
        print(f"downloading {url} …")
        urllib.request.urlretrieve(url, zip_path)  # noqa: S310 - official Metro feed
    index = build(zip_path)
    OUT.write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    pairs = sum(len(c) for c in index["places"].values())
    print(f"wrote {OUT.relative_to(ROOT)}: {len(index['places'])} places, {pairs} place-school pairs "
          f"with a direct line (feed {index['feed_start_date']}-{index['feed_end_date']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
