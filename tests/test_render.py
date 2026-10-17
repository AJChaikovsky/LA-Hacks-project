"""C3 (#21): every fixture renders in es and en, with the demo's must-have and must-never strings."""
import re
from datetime import date

import pytest

from core import render, stubs
from core.types import (
    CHECKLIST_CODES,
    DEADLINE_KEYS,
    INTERESTS,
    REASON_CODES,
    STEP_CODES,
    WARNING_CODES,
    GuardrailWarning,
    PointsEstimate,
    PointsLine,
)

MARIA = stubs.extract("")
EN = MARIA.model_copy(update={"language": "en"})
MATCHES = stubs.match(MARIA)
CODES = [m.code for m in MATCHES]
ESTIMATES = stubs.estimate(MARIA, CODES)
WARNINGS = stubs.check_list(MARIA, CODES)
PLAN = stubs.build_plan(MARIA, CODES, "es", date(2026, 10, 17))
DISCLAIMER_ES = "Esto es un estimado. Confirme en choices.lausd.net o llame al (213) 241-4177."


@pytest.mark.parametrize("lang", ["es", "en"])
def test_i18n_covers_every_frozen_code(lang):
    s = render.strings(lang)
    assert set(REASON_CODES) <= s["reasons"].keys()
    assert set(WARNING_CODES) <= s["warnings"].keys()
    assert set(CHECKLIST_CODES) <= s["checklist"].keys()
    assert set(STEP_CODES) <= s["steps"].keys()
    assert set(DEADLINE_KEYS) <= s["deadlines"].keys()
    assert set(INTERESTS) <= s["interests"].keys()
    assert {"phbao", "overcrowded", "waitlist", "sibling", "matriculation"} <= s["points"]["questions"].keys()


def all_replies(profile):
    return [
        render.render_matches(MATCHES, profile),
        render.render_points(ESTIMATES, profile),
        render.render_warnings(WARNINGS, profile),
        render.render_plan(PLAN, profile),
    ]


@pytest.mark.parametrize("profile", [MARIA, EN])
def test_no_percentages_no_submit_claims_no_leftover_placeholders(profile):
    for reply in all_replies(profile):
        assert "%" not in reply
        assert "garantizado" not in reply.lower() and "guaranteed" not in reply.lower()
        assert "enviamos su solicitud" not in reply and "we submitted" not in reply.lower()
        assert not re.search(r"\{[a-z_]+\}", reply), reply


def test_matches_es():
    out = render.render_matches(MATCHES, MARIA)
    assert out.startswith("Encontré 3 escuelas para Diego")
    assert "Belvedere MS · STEAM" in out and "Hollenbeck" in out
    assert out.count("cualquier magnet") == 1  # general reason shown once, not per school
    assert out.rstrip().endswith("**¿Quiere saber qué posibilidades tiene de entrar?**")
    for block in out.split("\n\n")[1:4]:
        assert len(block.splitlines()) <= 6


def test_points_es_assumed_unknown_and_disclaimer():
    out = render.render_points(ESTIMATES, MARIA)
    assert "probablemente 4 de 23 puntos" in out
    assert "¿Sabe si la escuela de su zona está sobrepoblada?" in out
    assert out.rstrip().endswith(DISCLAIMER_ES)


def test_points_per_school_ratio_and_not_applicable():
    lines = [PointsLine(factor="phbao", points=4, status="confirmed")]
    ests = [
        PointsEstimate(code="1804703", applicable=True, total=4, lines=lines, competitiveness="high", apps_per_opening=3.24),
        PointsEstimate(code="1817902", applicable=True, total=8, competitiveness="moderate",
                       lines=lines + [PointsLine(factor="overcrowded", points=4, status="confirmed")]),
        PointsEstimate(code="1838701TWS", applicable=False),
    ]
    out = render.render_points(ests, MARIA)
    assert "Belvedere MS · STEAM: 4 de 23 puntos." in out
    assert "Hollenbeck MS · STEM y Medicina: 8 de 23 puntos." in out
    assert "unas 3.2 solicitudes por cada lugar (competencia alta)" in out
    assert "Stevenson MS · Programa bilingüe en español: no usa puntos" in out
    assert "probablemente" not in out.split("Qué tan")[0]


def test_warnings_es_order_and_names():
    out = render.render_warnings(WARNINGS, MARIA)
    bullets = [l for l in out.splitlines() if l.startswith("- ")]
    assert bullets[0].startswith("- **Solo puede poner 3 escuelas magnet.** Markham MS · Artes")
    assert "Stevenson MS · Programa bilingüe en español" in out
    assert "primera opción (Belvedere MS · STEAM)" in out


def test_warnings_empty_list():
    assert render.render_warnings([], MARIA).startswith("Su lista se ve bien.")


def test_plan_es():
    out = render.render_plan(PLAN, MARIA)
    assert "**6 de noviembre de 2026: fecha límite a tiempo (faltan 20 días)**" in out
    assert "1. Abra apply.lausd.net en su celular." in out
    assert "Asunto: Preguntas sobre el programa STEAM de Belvedere MS" in out
    assert "Usted envía la solicitud; nosotros no enviamos nada." in out
    assert out.rstrip().endswith("**Termine su solicitud antes del 6 de noviembre de 2026.**")


def test_plan_en_dates():
    out = render.render_plan(PLAN, EN)
    assert "**November 6, 2026: on-time deadline (20 days left)**" in out
    assert "You submit the application; we never submit anything." in out


def test_unknown_language_falls_back_to_english():
    assert render.strings("fr") == render.strings("en")


def test_unknown_program_code_uses_match_name():
    assert render.program_name("9999999", "es") == "9999999"
    assert render.program_name(CODES[0], "es", MATCHES) == "Belvedere MS · STEAM"


def test_warning_with_every_code_renders():
    for code in WARNING_CODES:
        w = GuardrailWarning(code=code, severity="info", params={"codes": ["1823703"], "suggest": "1838701TWS"})
        assert render.render_warnings([w], MARIA).strip()


# --- official names and translated places (regressions) ---

def test_names_come_from_school_data():
    for code, record in render.schools().items():
        for lang in ("es", "en"):
            assert render.program_name(code, lang).startswith(record["name"] + " · ")


@pytest.mark.parametrize("lang", ["es", "en"])
def test_every_school_in_data_has_a_program_label(lang):
    assert set(render.schools()) <= render.strings(lang)["program_labels"].keys()


@pytest.mark.parametrize("profile", [MARIA, EN], ids=["es", "en"])
def test_no_unofficial_spelling_in_any_output(profile):
    for reply in all_replies(profile):
        assert "Bel Vedere" not in reply


def test_spanish_reply_translates_places():
    from core.email_draft import draft_email

    downtown = MARIA.model_copy(update={"home_neighborhood": "Downtown", "parent_work_area": "Downtown"})
    matches = [m.model_copy(update={"reason_codes": ["near_home", "near_work"]}) for m in MATCHES]
    out = render.render_matches(matches, downtown) + draft_email(downtown, CODES)[1].split("\n\n---")[0]
    assert "Downtown" not in out
    assert "Queda cerca de su trabajo en el centro de Los Ángeles." in out
    assert "¿Hay transporte escolar desde el centro de Los Ángeles?" in out
    assert render.place("Boyle Heights", "es") == "Boyle Heights"
    assert render.place("Downtown", "en") == "downtown Los Angeles"
