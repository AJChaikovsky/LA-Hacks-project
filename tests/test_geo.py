"""B11: exact address -> distance and bus eligibility, with centroid fallback and no address kept or logged."""
import io
import json
import logging
import socket

import pytest

from core import geo
from core.matcher import MILE_KM, haversine_km, home_coords, match, place_coords, reason_codes, schools
from core.types import FamilyProfile

ADDRESS = "1831 E 1st St, Los Angeles, CA 90033"  # Mariachi Plaza (public landmark)
STEAM = next(s for s in schools() if s.code == "1804703")  # Belvedere STEAM


@pytest.fixture(autouse=True)
def fresh_geo(monkeypatch):
    geo._cache.clear()
    monkeypatch.setattr(geo, "MIN_INTERVAL_S", 0)
    yield
    geo._cache.clear()


def fake_nominatim(monkeypatch, lat: float, lng: float):
    calls = []

    def urlopen(req, timeout):
        calls.append((req.full_url, timeout, req.get_header("User-agent")))
        return io.BytesIO(json.dumps([{"lat": str(lat), "lon": str(lng)}]).encode())
    monkeypatch.setattr(geo.urllib.request, "urlopen", urlopen)
    return calls


def point_miles_west_of(school, miles: float) -> tuple[float, float]:
    # 1 degree of longitude at this latitude ~ 92.1 km; good enough for a test fixture
    return round(school.lat, 3), round(school.lng - miles * MILE_KM / 92.1, 3)


def test_geocode_rounds_and_sends_policy_headers(monkeypatch):
    calls = fake_nominatim(monkeypatch, 34.047481, -118.219082)
    assert geo.geocode(ADDRESS) == (34.047, -118.219)
    url, timeout, agent = calls[0]
    assert timeout == 3 and "choices-counselor" in agent and "bounded=1" in url and "countrycodes=us" in url
    assert geo.geocode(ADDRESS.upper()) == (34.047, -118.219) and len(calls) == 1  # cached


@pytest.mark.parametrize("miles,bus", [(1.5, False), (2.5, True)])
def test_exact_distance_applies_the_two_mile_bus_rule(monkeypatch, miles, bus):
    lat, lng = point_miles_west_of(STEAM, miles)
    fake_nominatim(monkeypatch, lat, lng)
    p = geo.apply_address(FamilyProfile(current_grade=5, interests=["arts"], home_neighborhood="Boyle Heights"), ADDRESS)
    assert (p.home_lat, p.home_lng) == (lat, lng) and home_coords(p) == (lat, lng)
    km = haversine_km(lat, lng, STEAM.lat, STEAM.lng)
    assert abs(km / MILE_KM - miles) < 0.15
    assert ("bus_possible" in reason_codes(p, STEAM)) is bus


def test_bus_rule_is_secondary_only(monkeypatch):
    lat, lng = point_miles_west_of(STEAM, 3)
    fourth_grader = FamilyProfile(current_grade=4, home_lat=lat, home_lng=lng)
    assert "bus_possible" not in reason_codes(fourth_grader, STEAM)


def test_geocoder_down_falls_back_to_the_centroid(monkeypatch, caplog):
    def timeout(req, timeout):
        raise socket.timeout("timed out")
    monkeypatch.setattr(geo.urllib.request, "urlopen", timeout)
    base = FamilyProfile(current_grade=5, interests=["arts"], home_neighborhood="Boyle Heights")
    with caplog.at_level(logging.WARNING, logger="geo"):
        p = geo.apply_address(base, ADDRESS)
    assert p == base and home_coords(p) == place_coords("Boyle Heights")
    assert match(p) == match(base)  # same answer as if no address had been given
    assert "geocoder unavailable" in caplog.text


def test_address_is_never_logged_or_stored(monkeypatch, caplog):
    fake_nominatim(monkeypatch, 34.047, -118.219)
    with caplog.at_level(logging.DEBUG):
        p = geo.apply_address(FamilyProfile(home_neighborhood="Boyle Heights"), ADDRESS)
    dumped = p.model_dump_json()
    for fragment in ("1831", "1st St", "E 1st", "90033"):
        assert fragment not in caplog.text and fragment not in dumped
    assert all(len(k) == 64 and "1831" not in k for k in geo._cache)  # cache keyed by a hash


def test_blank_or_unknown_address_changes_nothing(monkeypatch):
    monkeypatch.setattr(geo.urllib.request, "urlopen", lambda req, timeout: io.BytesIO(b"[]"))
    base = FamilyProfile(home_neighborhood="Sylmar")
    assert geo.apply_address(base, "") == base and geo.apply_address(base, None) == base
    assert geo.apply_address(base, "123 Nowhere Rd, Atlantis") == base
