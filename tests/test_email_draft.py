"""C5 (#23): coordinator email asks the 3 questions, never mentions points/odds, falls back to the template."""
import os

import pytest

from core import email_draft as em
from core import llm, stubs

MARIA = stubs.extract("")
EN = MARIA.model_copy(update={"language": "en"})
CODES = ["1804703", "1817902", "1804702"]


@pytest.fixture
def llm_on(monkeypatch, tmp_path):
    monkeypatch.setenv("EMAIL_LLM", "on")
    monkeypatch.setattr(em, "CACHE", tmp_path / "email_cache.json")


def fake_llm(monkeypatch, **draft):
    calls = []

    def fake(prompt, schema, fallback, **kw):
        calls.append(prompt)
        return schema(**draft)

    monkeypatch.setattr(llm, "complete_json", fake)
    return calls


def test_template_es_has_three_questions_and_english_copy():
    subject, body = em.draft_email(MARIA, CODES)
    assert subject == "Preguntas sobre el programa STEAM de Bel Vedere MS (estudiante con IEP)"
    spanish, english = body.split("\n\n---\nEnglish:\n")
    assert spanish.startswith("Estimado/a coordinador/a del programa magnet de Bel Vedere MS:")
    assert "Diego, que entra a sexto grado el próximo año y tiene un IEP" in spanish
    assert "¿Hay transporte escolar desde Boyle Heights?" in spanish
    assert spanish.rstrip().endswith("Maria")
    assert "Dear magnet coordinator at Bel Vedere MS" in english and "6th grade" in english
    assert em.problems(spanish, MARIA) == [] and em.problems(english, MARIA) == []


def test_template_en_has_no_copy():
    subject, body = em.draft_email(EN, CODES)
    assert subject.startswith("Questions about the STEAM program at Bel Vedere MS")
    assert "English:" not in body and "1. What special education supports" in body


def test_iep_notes_are_named_and_no_iep_skips_that_question():
    with_notes = MARIA.model_copy(update={"iep_notes": "speech therapy"})
    assert "IEP (speech therapy)?" in em.draft_email(with_notes, CODES)[1]
    no_iep = MARIA.model_copy(update={"iep": False})
    body = em.draft_email(no_iep, CODES)[1]
    assert "IEP" not in body and em.problems(body.split("\n\n---")[0], no_iep) == []


def test_missing_parent_name_uses_placeholder():
    body = em.draft_email(MARIA.model_copy(update={"parent_name": None}), CODES)[1]
    assert "[Su nombre]" in body


def test_good_llm_draft_is_used(monkeypatch, llm_on):
    body = ("Hola, soy Maria. Diego tiene un IEP.\n1. ¿Qué apoyos de educación especial ofrecen?\n"
            "2. ¿Cuándo hay recorridos?\n3. ¿Hay transporte desde Boyle Heights?\nGracias, Maria")
    body_en = ("Hi, I'm Maria. Diego has an IEP.\n1. What special education supports do you offer?\n"
               "2. When are tours?\n3. Is there transportation from Boyle Heights?\nThanks, Maria")
    calls = fake_llm(monkeypatch, subject="Preguntas sobre STEAM", body=body, body_en=body_en)
    subject, out = em.draft_email(MARIA, CODES)
    assert subject == "Preguntas sobre STEAM" and out.startswith(body) and body_en in out
    assert '"school": "Bel Vedere MS"' in calls[0] and '"has_iep": true' in calls[0]


@pytest.mark.parametrize("bad", [
    "Diego tiene un IEP. ¿Qué apoyos hay? ¿Recorridos? ¿Transporte? Con 4 puntos tiene buenas posibilidades.",
    "¿Qué apoyos hay para su IEP? ¿Hay transporte?",               # no tour question
    "IEP recorrido transporte " + "palabra " * 150,                # too long
    "",
])
def test_bad_llm_drafts_fall_back_to_template(monkeypatch, llm_on, bad):
    fake_llm(monkeypatch, subject="x", body=bad, body_en="IEP tours transportation")
    subject, _ = em.draft_email(MARIA, CODES)
    assert subject == em.template(MARIA, CODES[0], "es")[0]


def test_demo_mode_caches_first_good_draft(monkeypatch, llm_on):
    monkeypatch.setenv("DEMO_MODE", "true")
    good = "IEP? 1. ¿Apoyos de educación especial? 2. ¿Recorridos? 3. ¿Transporte?"
    calls = fake_llm(monkeypatch, subject="S1", body=good, body_en="IEP? special education? tours? transportation?")
    first = em.draft_email(MARIA, CODES)
    fake_llm(monkeypatch, subject="S2", body=good, body_en="IEP? special education? tours? transportation?")
    assert em.draft_email(MARIA, CODES) == first and first[0] == "S1"
    assert len(calls) == 1


def test_llm_down_returns_template(monkeypatch, llm_on):
    monkeypatch.setattr(llm, "complete_json", lambda p, schema, fallback, **kw: fallback)
    assert em.draft_email(MARIA, CODES)[0] == em.template(MARIA, CODES[0], "es")[0]


def test_stub_plan_email_follows_language():
    from datetime import date
    assert stubs.build_plan(EN, CODES, "en", date(2026, 10, 17)).email_subject.startswith("Questions about")
    assert stubs.build_plan(MARIA, CODES, "es", date(2026, 10, 17)).email_subject.startswith("Preguntas")


@pytest.mark.skipif(not os.getenv("RUN_LIVE"), reason="set RUN_LIVE=1 to call ASI:One")
def test_live_draft_passes_rules(monkeypatch, tmp_path):
    monkeypatch.setenv("EMAIL_LLM", "on")
    monkeypatch.setattr(em, "CACHE", tmp_path / "c.json")
    subject, body = em.draft_email(MARIA, CODES)
    assert subject and em.problems(body.split("\n\n---")[0], MARIA) == []


def test_draft_assuming_parent_role_falls_back(monkeypatch, llm_on):
    good = "IEP? 1. ¿Apoyos de educación especial? 2. ¿Recorridos? 3. ¿Transporte?"
    fake_llm(monkeypatch, subject="S", body="Soy María, mamá de Diego. " + good,
             body_en="IEP? special education? tours? transportation?")
    assert em.draft_email(MARIA, CODES)[0] == em.template(MARIA, CODES[0], "es")[0]
