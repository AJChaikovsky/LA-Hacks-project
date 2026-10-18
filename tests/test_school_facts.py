"""B14: whole-school context from CDE, shown as plain fractions. Offline; no network at runtime."""
from __future__ import annotations

import json
import os
import re

os.environ.setdefault("FACTS_SEED", "b14-test-seed-not-production")

import pytest

from agents import cards
from agents.facts_agent import answer
from agents.messages import FactsRequest
from core import render
from core.config import load_data
from core.matcher import match
from core.school_facts import facts, fraction
from core.types import FamilyProfile, School

MARIA = FamilyProfile(language="es", child_name="Diego", current_grade=5, interests=["arts"],
                      home_neighborhood="Boyle Heights", iep=True)
HOLLENBECK, BELVEDERE_STEAM = "1817902", "1804703"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv("FACTS", "on")


def test_every_program_has_a_cds_code_and_facts_or_a_null_reason():
    rows = load_data("school_facts")["schools"]
    for s in load_data("schools"):
        assert re.fullmatch(r"1964733\d{7}", School.model_validate(s).cds_code or ""), s["code"]
        row = rows[s["cds_code"]]
        for field in ("enrollment", "english_learners", "students_with_disabilities", "chronic_absenteeism_pct"):
            assert row[field] is not None or row.get(f"{field}_null_reason") in ("suppressed", "not_reported", "not_found")


def test_spot_check_against_dataquest():
    # DataQuest 2025-26 Census Day enrollment, Hollenbeck Middle (CDS 19647336058051): 839 total.
    row = load_data("school_facts")["schools"]["19647336058051"]
    assert (row["enrollment"], row["english_learners"], row["students_with_disabilities"]) == (839, 91, 145)
    f = facts(HOLLENBECK)
    assert f["enrollment"] == 840 and f["iep"] == (1, 6) and f["year"] == "2025-26"
    assert facts(BELVEDERE_STEAM)["iep"] == (1, 4)  # 178 of 718


@pytest.mark.parametrize("count,total,expected", [
    (145, 839, (1, 6)), (91, 839, (1, 9)), (450, 1000, (4, 10)), (300, 1000, (3, 10)),
    (0, 839, None), (None, 839, None), (10, None, None), (900, 839, None),
])
def test_plain_fractions(count, total, expected):
    assert fraction(count, total) == expected


def test_iep_line_only_for_iep_families_and_never_a_percentage_or_ranking():
    es = render.render_matches(match(MARIA), MARIA)
    lines = [ln for ln in es.splitlines() if "IEP (toda la escuela" in ln]
    assert len(lines) == 3 and "1 de cada 4" in lines[0] and "1 de cada 6" in es
    no_iep = MARIA.model_copy(update={"iep": False})
    assert "toda la escuela" not in render.render_matches(match(no_iep), no_iep)
    en = render.render_matches(match(MARIA), MARIA.model_copy(update={"language": "en"}))
    for text in (es, en):
        facts_lines = [ln for ln in text.splitlines() if "IEP (" in ln]
        assert facts_lines and not any("%" in ln for ln in facts_lines)
        assert not re.search(r"\b(best|better|worse|worst|top|mejor|peor)\b", " ".join(facts_lines), re.I)


def test_detail_card_rows():
    m = next(x for x in match(MARIA) if x.code == HOLLENBECK)
    card = cards.school_detail(m, MARIA)
    rows = {r["label"]: r["value"] for r in card["summary_rows"]}
    assert rows["Estudiantes"] == "unos 840 en toda la escuela (2025-26)"
    assert rows["Con IEP"] == "unos 1 de cada 6" and rows["Aprenden inglés"] == "unos 1 de cada 9"
    assert not cards.validate("detail", card)


def test_flag_off(monkeypatch):
    monkeypatch.setenv("FACTS", "off")
    assert facts(HOLLENBECK) == {}
    assert "toda la escuela" not in render.render_matches(match(MARIA), MARIA)


def test_unknown_code_is_empty():
    assert facts("0000000") == {}


def test_specialist_agent_answers():
    out = json.loads(answer(FactsRequest(session_id="s", codes=[HOLLENBECK, "0000000"])).facts_json)
    assert out[HOLLENBECK]["iep"] == [1, 6] and out["0000000"] == {}


def test_builder_keeps_suppressed_cells_null_with_a_reason(monkeypatch, tmp_path):
    from scripts import build_school_facts as b

    schools_ = [{"code": "A", "cds_code": "19647331111111"}, {"code": "B", "cds_code": "19647332222222"},
                {"code": "C", "cds_code": "19647333333333"}]
    monkeypatch.setattr(b, "load_data", lambda n: schools_)
    head = "AcademicYear\tAggregateLevel\tCountyCode\tDistrictCode\tSchoolCode\tCountyName\tDistrictName\tSchoolName\tCharter\tReportingCategory\tTOTAL_ENR\n"
    body = "".join(f"2025-26\tS\t19\t64733\t{sc}\tLos Angeles\tLAUSD\t{name}\tNo\t{cat}\t{v}\n" for sc, name, cat, v in [
        ("1111111", "Alpha Middle", "TA", "600"), ("1111111", "Alpha Middle", "SG_EL", "100"),
        ("1111111", "Alpha Middle", "SG_DS", "*"),
        ("2222222", "Beta Middle", "TA", "300"), ("2222222", "Beta Middle", "SG_EL", "30"),
    ])
    (tmp_path / "cdenroll2526.txt").write_text(head + body, encoding="latin-1")
    ahead = ("Academic Year\tAggregate Level\tCounty Code\tDistrict Code\tSchool Code\tCounty Name\tDistrict Name\t"
             "School Name\tCharter School\tDASS\tReporting Category\tChronicAbsenteeismEligibleCumulativeEnrollment\t"
             "ChronicAbsenteeismCount\tChronicAbsenteeismRate\n")
    abody = "2024-25\tS\t19\t64733\t1111111\tLA\tLAUSD\tAlpha Middle\tNo\tNo\tTA\t650\t*\t*\n"
    (tmp_path / "chronicabsenteeism25.txt").write_text(ahead + abody, encoding="latin-1")
    out = b.build(tmp_path)["schools"]
    alpha, beta, missing = out["19647331111111"], out["19647332222222"], out["19647333333333"]
    assert alpha["enrollment"] == 600 and alpha["students_with_disabilities"] is None
    assert alpha["students_with_disabilities_null_reason"] == "suppressed"
    assert alpha["chronic_absenteeism_pct"] is None and alpha["chronic_absenteeism_pct_null_reason"] == "suppressed"
    assert beta["students_with_disabilities_null_reason"] == "not_reported"
    assert beta["chronic_absenteeism_pct_null_reason"] == "not_found"
    assert missing["enrollment"] is None and missing["enrollment_null_reason"] == "not_found"
