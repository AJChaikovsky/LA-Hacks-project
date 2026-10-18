"""B13: direct LA Metro bus lines from the prebuilt GTFS index. Offline; no network at runtime."""
from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import date

os.environ.setdefault("TRANSIT_SEED", "b13-test-seed-not-production")

import pytest

from agents import cards
from agents.messages import TransitRequest
from agents.transit_agent import answer
from core import render
from core.config import load_data
from core.matcher import match
from core.transit import transit_options
from core.types import FamilyProfile

BOYLE = FamilyProfile(language="es", child_name="Diego", current_grade=5, interests=["arts"],
                      home_neighborhood="Boyle Heights", iep=True)
BELVEDERE_STEAM, GRIFFITH, HOLLENBECK = "1804703", "1816801", "1817902"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv("TRANSIT", "on")


def routes(code, profile=BOYLE):
    return {o.route: o for o in transit_options(profile, code)}


def test_boyle_heights_to_eastside_schools_hand_checked_against_metro_timetables():
    # docs/evidence/B13-metro-line-70/74-timetable-*.pdf: line 70 runs on Cesar E Chavez Av past Record Ave
    # (Belvedere); line 74 runs from 1st & Soto east toward Atlantic (Griffith is on 4th St near Mednik).
    steam = routes(BELVEDERE_STEAM)
    assert steam["70"].minutes == 10 and steam["70"].alight_stop == "Cesar E Chavez / Record"
    griffith = routes(GRIFFITH)
    assert griffith["74"].minutes == 15 and griffith["74"].walk_m <= 500


def test_walkable_school_and_unknown_places_return_nothing():
    assert routes(HOLLENBECK) == {}  # 0.9 km from the Boyle Heights centroid: walk, don't bus
    assert transit_options(BOYLE.model_copy(update={"home_neighborhood": "Atlantis"}), BELVEDERE_STEAM) == []
    assert transit_options(BOYLE.model_copy(update={"home_neighborhood": None}), BELVEDERE_STEAM) == []


def test_aliases_resolve():
    assert routes(BELVEDERE_STEAM, BOYLE.model_copy(update={"home_neighborhood": "boyle hts"}))


def test_runs_offline(monkeypatch):
    import urllib.request

    def no_network(*a, **k):
        raise AssertionError("transit must not use the network at runtime")
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    assert routes(BELVEDERE_STEAM)


def test_index_is_sourced_and_covers_the_deadline():
    idx = load_data("transit_index")
    assert idx["source_url"].startswith("https://gitlab.com/LACMTA/") and idx["terms_url"]
    assert idx["feed_start_date"] <= "20261106" <= idx["feed_end_date"]
    for place, by_code in idx["places"].items():
        for code, opts in by_code.items():
            assert opts and all(o["minutes"] > 0 and o["walk_m"] <= 500 and o["trips"] >= 3 for o in opts)


def test_maria_gets_metro_reasons_with_about_wording():
    result = match(BOYLE)
    steam = next(m for m in result if m.code == BELVEDERE_STEAM)
    assert "metro_direct" in steam.reason_codes and steam.transit[0].route == "70"
    hollenbeck = next(m for m in result if m.code == HOLLENBECK)
    assert "metro_direct" not in hollenbeck.reason_codes and hollenbeck.transit == []
    es = render.render_matches(result, BOYLE)
    assert "Línea 70, unos 10 minutos según el horario" in es
    en = render.render_matches(result, BOYLE.model_copy(update={"language": "en"}))
    assert "Line 70, about 10 minutes by the schedule" in en
    assert "will take" not in en and "tardará" not in es


def test_detail_card_has_a_metro_row():
    steam = next(m for m in match(BOYLE) if m.code == BELVEDERE_STEAM)
    rows = cards.school_detail(steam, BOYLE)["summary_rows"]
    metro = [r["value"] for r in rows if r["label"] == "Metro"]
    assert metro and metro[0].startswith("Línea 70 · unos 10 min desde Boyle Heights")
    assert not cards.validate("detail", cards.school_detail(steam, BOYLE))


def test_flag_off_removes_everything(monkeypatch):
    monkeypatch.setenv("TRANSIT", "off")
    assert transit_options(BOYLE, BELVEDERE_STEAM) == []
    assert not any("metro_direct" in m.reason_codes or m.transit for m in match(BOYLE))


def test_specialist_agent_answers_from_the_index():
    reply = answer(TransitRequest(session_id="s1", profile_json=BOYLE.model_dump_json(),
                                  codes=[BELVEDERE_STEAM, HOLLENBECK]))
    options = json.loads(reply.options_json)
    assert options[HOLLENBECK] == [] and options[BELVEDERE_STEAM][0]["route"] == "70"


# --- the builder, on a tiny synthetic feed ---------------------------------

def _zip(files: dict[str, str]) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    buf.seek(0)
    return buf


def test_builder_keeps_direct_weekday_morning_rides_only(monkeypatch, tmp_path):
    from scripts import build_transit

    # Home stop H at the centroid, school stop S next to the school (~3 km east).
    places = {"neighborhoods": {"Home": {"lat": 34.0, "lng": -118.2, "aliases": []}}}
    schools_ = [{"code": "X1", "lat": 34.0, "lng": -118.1675}]
    monkeypatch.setattr(build_transit, "load_data", lambda n: places if n == "places" else schools_)
    trip_rows, times = [], []
    for i, (svc, board, direction) in enumerate([
        ("WK", "07:00:00", "fwd"), ("WK", "07:20:00", "fwd"), ("WK", "07:40:00", "fwd"),  # counted
        ("WK", "11:00:00", "fwd"),    # outside the morning window
        ("WK", "07:10:00", "back"),   # school -> home: wrong direction
        ("SUN", "07:30:00", "fwd"),   # not running on the reference Tuesday
    ]):
        trip_rows.append(f"R1,{svc},T{i},,0,,,")
        first, second = ("H", "S") if direction == "fwd" else ("S", "H")
        h, m = int(board[:2]), int(board[3:5])
        times += [f"T{i},{board},{board},{first},1", f"T{i},{h:02d}:{m + 12:02d}:00,{h:02d}:{m + 12:02d}:00,{second},2"]
    feed = _zip({
        "feed_info.txt": "feed_publisher_name,feed_start_date,feed_end_date\nMetro,20260607,20261212\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                        "WK,1,1,1,1,1,0,0,20260607,20261212\nSUN,0,0,0,0,0,0,1,20260607,20261212\n",
        "routes.txt": "route_id,route_short_name\nR1,99\n",
        "trips.txt": "route_id,service_id,trip_id,trip_headsign,direction_id,block_id,shape_id,x\n" + "\n".join(trip_rows) + "\n",
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\nH,Home Stop,34.0,-118.2\nS,School Stop,34.0,-118.168\n",
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n" + "\n".join(times) + "\n",
    })
    path = tmp_path / "feed.zip"
    path.write_bytes(feed.getvalue())
    index = build_transit.build(path)
    [opt] = index["places"]["Home"]["X1"]
    assert opt["route"] == "99" and opt["minutes"] == 12 and opt["trips"] == 3
    assert opt["board_stop"] == "Home Stop" and opt["alight_stop"] == "School Stop"
    assert date.fromisoformat("2026-10-27").weekday() == 1  # the reference day really is a Tuesday
