"""B12: caseload report, price and on-chain payment check (the live handshake is scripts/caseload_demo.py)."""
from types import SimpleNamespace

import pytest

from core.caseload import build_report, family_report, payment_matches, price
from core.types import FamilyProfile

SELLER = "fetch1k6cxjzkznqdtyp0es7lhlergqgwq9s7xcpwvgy"
MARIA = FamilyProfile(language="es", child_name="Diego", current_grade=5, applying_grade=6, interests=["arts"], iep=True,
                      home_language="Spanish", home_neighborhood="Boyle Heights", parent_work_area="Downtown")


@pytest.mark.parametrize("n,fet,atto", [(1, "0.1", 10**17), (3, "0.3", 3 * 10**17), (50, "5", 5 * 10**18)])
def test_price_per_family(n, fet, atto):
    assert price(n) == (fet, atto)


def test_family_report_is_built_from_core():
    r = family_report(MARIA)
    assert [m["code"] for m in r["matches"]] == ["1804703", "1817902", "1804702"]
    assert r["points"] == {"total": 4, "max": 23, "probably": True, "unknown_factors": []}
    assert r["top_warning"]["severity"] in ("block", "warn", "info")


def test_outside_district_outranks_far_commute():
    leo = FamilyProfile(language="en", child_name="Leo", current_grade=5, interests=["arts"], home_neighborhood="Pasadena")
    assert family_report(leo)["top_warning"] == {"code": "outside_district", "severity": "warn"}


def test_report_has_no_percentages_and_a_disclaimer():
    text = str(build_report([MARIA, FamilyProfile(current_grade=4)]))
    assert "%" not in text and "garantizado" not in text and "(213) 241-4177" in text


TX_EVENTS = {"transfer": {"recipient": SELLER, "sender": "fetch1buyer", "amount": "300000000000000000atestfet"}}


@pytest.mark.parametrize("events,ok", [
    ([TX_EVENTS], True),
    ([{}, TX_EVENTS], True),                                                     # found in a per-message log
    ([{"transfer": {**TX_EVENTS["transfer"], "recipient": "fetch1someoneelse"}}], False),
    ([{"transfer": {**TX_EVENTS["transfer"], "amount": "200000000000000000atestfet"}}], False),  # underpaid
    ([{"transfer": {**TX_EVENTS["transfer"], "amount": "300000000000000000afet"}}], False),      # mainnet denom
    ([], False),
])
def test_payment_matches(events, ok):
    assert payment_matches(events, SELLER, 3 * 10**17, "atestfet") is ok


def test_agents_default_to_testnet():
    from agents.caseload_agent import create_agent
    agent = create_agent("caseload-test-seed-do-not-use-0000000000000000")
    assert agent._ledger.network_config.chain_id == "dorado-1"  # Fetch testnet; uagents defaults to mainnet
