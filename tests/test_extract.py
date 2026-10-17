"""C1 (#19): profile extraction. All tests run with the LLM forced to fail unless marked live."""
import json
import os
from pathlib import Path

import pytest

from core import extract as ex
from core import llm
from core.types import FamilyProfile

FIXTURES = Path(__file__).parent / "fixtures"
MARIA = FamilyProfile.model_validate(json.loads((FIXTURES / "maria_profile.json").read_text(encoding="utf-8")))
MATCHES = ["1804703", "1817902", "1804702"]

TURN1 = ("Hola, soy Maria. Vivo en Boyle Heights. Mi hijo Diego está en quinto grado y el próximo año entra "
         "a sexto. Tiene un IEP y le encanta el arte, dibuja todo el día. Yo trabajo en el centro y solo "
         "tengo mi celular. ¿Qué escuelas me recomienda?")
TURN2 = "¿Qué posibilidades tiene de entrar?"
TURN3 = "Voy a poner las tres, y una más por si acaso: Markham, aunque queda lejísimos y no sé si lo aceptaría."
TURN4 = "Está bien, solo las tres. ¿Qué necesito para terminar?"


@pytest.fixture(autouse=True)
def llm_down(monkeypatch, request):
    """Simulate a dead LLM: complete_json always returns its fallback."""
    if "live" not in request.keywords:
        monkeypatch.setattr(llm, "complete_json", lambda prompt, schema, fallback, **kw: fallback)


# 10 phrasings: 5 Spanish, 3 English, 2 mixed. (text, language, expects Downtown work area)
PHRASINGS = [
    (TURN1, "es", True),
    ("Buenas tardes. Mi hija Sofia cursa 5to grado en Boyle Heights, el año que entra pasa a 6to. "
     "Tiene PEI y le gusta mucho dibujar y pintar. Trabajo en downtown.", "es", True),
    ("Necesito ayuda con la aplicación de Choices. Vivimos en Boyle Heights, mi hijo va en quinto "
     "grado, tiene educación especial (IEP) y lo que más le gusta es el arte.", "es", False),
    ("Hola! somos de Boyle Heights. Diego esta en 5o grado, tiene IEP, le encanta el arte. "
     "Trabajo en el centro de Los Angeles.", "es", True),
    ("Mi hijo de quinto grado tiene un IEP. Le encanta pintar. Vivo en Boyle Heights y trabajo "
     "en el centro, solo uso el teléfono.", "es", True),
    ("Hi, I'm Maria. We live in Boyle Heights. My son Diego is in 5th grade and has an IEP. "
     "He loves art and drawing. I work downtown and only have my phone.", "en", True),
    ("My daughter is a fifth grader in Boyle Heights going into 6th grade next year. She has an IEP "
     "and loves to paint.", "en", False),
    ("Looking at magnets for my son, 5th grade, Boyle Heights. He has an IEP and is really into art. "
     "I work in DTLA.", "en", True),
    ("Hola, my son Diego está en quinto grado, we live in Boyle Heights. Tiene IEP and he loves arte. "
     "Trabajo en el centro.", None, True),
    ("Hi! Mi hijo is in 5th grade, vivimos en Boyle Heights, he has an IEP y le encanta dibujar. "
     "I work downtown.", None, True),
]


@pytest.mark.parametrize("text,lang,downtown", PHRASINGS)
def test_phrasings_produce_maria_profile(text, lang, downtown):
    p = ex.extract(text)
    assert (p.current_grade, p.applying_grade) == (5, 6)
    assert p.iep is True
    assert "arts" in p.interests
    assert p.home_neighborhood == "Boyle Heights"
    if lang:
        assert p.language == lang
    if downtown:
        assert p.parent_work_area == "Downtown"


def test_golden_turn1_equals_fixture_with_llm_down():
    assert ex.extract(TURN1) == MARIA


def test_later_turns_keep_the_profile():
    p = ex.extract(TURN2, prior=MARIA)
    assert p == MARIA
    p = ex.extract(TURN4, prior=MARIA)
    assert p == MARIA


def test_turn3_flags_markham_as_doubtful():
    p = ex.extract(TURN3, prior=MARIA)
    assert p.would_not_accept == ["1823703"]
    assert p.language == "es" and p.current_grade == 5


def test_parse_school_list_turn3_and_turn4():
    assert ex.parse_school_list(TURN3, MATCHES) == (MATCHES + ["1823703"], ["1823703"])
    assert ex.parse_school_list(TURN4, MATCHES) == (MATCHES, [])


def test_parse_school_list_named_schools_and_themes():
    choices, doubtful = ex.parse_school_list(
        "Voy a poner Hollenbeck primero, luego Bel Vedere media, pero Markham no sé si la aceptaría", [])
    assert choices == ["1817902", "1804702", "1823703"]
    assert doubtful == ["1823703"]
    # a bare school name resolves to the program that was matched
    assert ex.parse_school_list("I'll list Bel Vedere", ["1804702"])[0] == ["1804702"]


@pytest.mark.parametrize("text,expected", [
    ("Mi hijo no tiene IEP", False),
    ("He doesn't have an IEP", False),
    ("Tiene un PEI", True),
    ("He gets special education services", True),
    ("Le gusta el arte", None),
])
def test_iep(text, expected):
    assert ex.find_iep(ex.norm(text)) is expected


@pytest.mark.parametrize("text,current,applying", [
    ("está en quinto grado", 5, 6),
    ("my son is in 5th grade", 5, 6),
    ("going into 6th grade", 5, 6),
    ("entra a sexto el próximo año", 5, 6),
    ("tiene 3 hermanos y le gusta el arte", None, None),
    ("is in second grade", 2, 3),
])
def test_grades(text, current, applying):
    assert ex.find_grades(ex.norm(text)) == (current, applying)


def test_flags_and_names():
    p = ex.extract("Mi hija Ana está aprendiendo inglés, es GATE, y estuvo en lista de espera 2 años.")
    assert p.child_name == "Ana" and p.english_learner and p.gifted and p.waitlist_years == 2


def test_llm_layer_merges_but_prepass_wins(monkeypatch):
    def fake(prompt, schema, fallback, **kw):
        return schema(interests=["arte", "ciencia"], home_neighborhood="Somewhere Else", iep_notes="speech therapy",
                      current_grade=4)
    monkeypatch.setattr(llm, "complete_json", fake)
    p = ex.extract(TURN1)
    assert p.home_neighborhood == "Boyle Heights"  # pre-pass wins
    assert p.current_grade == 5                    # pre-pass grade pair wins
    assert p.interests == ["arts", "stem"]         # 'arte'/'ciencia' normalized and unioned
    assert p.iep_notes == "speech therapy"         # LLM-only field kept


def test_llm_filler_iep_note_dropped(monkeypatch):
    monkeypatch.setattr(llm, "complete_json", lambda p, schema, f, **kw: schema(iep_notes="Has an IEP"))
    assert ex.extract(TURN1).iep_notes is None


def test_load_places_reads_b1_neighborhoods():
    places = ex.load_places()
    assert {"landmarks", "neighborhoods", "source_url"}.isdisjoint(places)
    assert "boyle hts" in places["Boyle Heights"]  # alias that only exists in data/places.json


def test_extract_llm_off_switch(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: calls.append(1))
    monkeypatch.setenv("EXTRACT_LLM", "off")
    ex.extract(TURN1)
    assert calls == []


@pytest.mark.live
@pytest.mark.skipif(not os.getenv("RUN_LIVE"), reason="set RUN_LIVE=1 to call ASI:One")
def test_live_turn1_matches_fixture():
    assert ex.extract(TURN1) == MARIA


@pytest.mark.parametrize("text,keeps_markham", [
    ("I'll list all three, plus Markham just in case", True),           # 5943cae regression
    ("Voy a poner las tres, solo para estar segura, y Markham", True),
    ("I'm only adding Markham to the three", True),
    ("Voy a poner las tres y Markham, solo por si acaso", True),
    ("Está bien, solo las tres.", False),
    ("Ok, only the three.", False),
    ("Just those three please", False),
    ("Las tres nada más", False),
    ("Solo con las tres, gracias", False),
])
def test_only_the_three_must_be_attached(text, keeps_markham):
    choices, _ = ex.parse_school_list(text + (" Markham" if not keeps_markham else ""), MATCHES)
    assert ("1823703" in choices) is keeps_markham
