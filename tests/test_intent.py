"""C2 (#20): intent router. The LLM is forced off unless a test injects a fake."""
import json

import pytest

from core import intent, llm
from core.types import INTENTS, STAGES
from tests.test_extract import PHRASINGS, TURN1, TURN2, TURN3, TURN4


@pytest.fixture(autouse=True)
def llm_down(monkeypatch):
    monkeypatch.setattr(llm, "complete_json", lambda prompt, schema, fallback, **kw: fallback)


# 30 utterances: (text, stage, expected)
TABLE = [
    # the 4 golden turns
    (TURN1, "intake", "profile_info"),
    (TURN2, "matched", "ask_odds"),
    (TURN3, "odds", "set_list"),
    (TURN4, "listing", "ask_plan"),
    # ask_odds
    ("¿Cuántos puntos tiene Diego?", "matched", "ask_odds"),
    ("What are his chances of getting in?", "matched", "ask_odds"),
    ("Is Bel Vedere very competitive?", "odds", "ask_odds"),
    ("¿Qué tan difícil es entrar a Hollenbeck?", "matched", "ask_odds"),
    # set_list
    ("I'll list Hollenbeck first and Bel Vedere second", "odds", "set_list"),
    ("Pongo Bel Vedere como primera opción", "odds", "set_list"),
    ("Vamos con las tres", "odds", "set_list"),
    ("Add Markham to my list just in case", "listing", "set_list"),
    # ask_plan
    ("¿Qué documentos necesito?", "listing", "ask_plan"),
    ("What do I need to finish the application?", "listing", "ask_plan"),
    ("¿Cuál es la fecha límite?", "odds", "ask_plan"),
    ("Can you write the email to the coordinator?", "plan", "ask_plan"),
    ("¿Cómo aplico desde mi celular? Dame los pasos", "listing", "ask_plan"),
    # profile_info (facts, any stage)
    ("También está aprendiendo inglés", "matched", "profile_info"),
    ("Oh, he's actually in 4th grade", "matched", "profile_info"),
    ("No tiene IEP, me equivoqué", "matched", "profile_info"),
    ("We moved to El Sereno last month", "odds", "profile_info"),
    # reset / help
    ("Quiero empezar de nuevo", "plan", "reset"),
    ("start over please", "odds", "reset"),
    ("¿Qué puedes hacer?", "intake", "help"),
    ("Help", "matched", "help"),
    # affirmations follow the stage
    ("sí", "matched", "ask_odds"),
    ("ok", "listing", "ask_plan"),
    ("Sí, por favor", "odds", "set_list"),
    # card clicks
    ('{"code": "1804703", "action": "details"}', "matched", "card_selection"),
    ("Ver detalles", "matched", "card_selection"),
]


def test_table_has_30_utterances():
    assert len(TABLE) == 30


@pytest.mark.parametrize("text,stage,expected", TABLE)
def test_table(text, stage, expected):
    assert intent.classify(text, stage) == expected


@pytest.mark.parametrize("text,_lang,_dt", PHRASINGS)
def test_every_profile_phrasing_is_profile_info(text, _lang, _dt):
    assert intent.classify(text, "intake") == "profile_info"


def test_card_selection_parsing():
    assert intent.parse_card_selection('{"code": "1804703"}') == {"code": "1804703"}
    assert intent.parse_card_selection("I selected 1838701TWS")["code"] == "1838701TWS"
    assert intent.parse_card_selection("Agregar a mi lista") == {"raw_text": "Agregar a mi lista"}
    assert intent.parse_card_selection(TURN3) is None
    assert intent.parse_card_selection("[1, 2]") is None


def test_llm_fallback_used_only_when_rules_miss(monkeypatch):
    seen = []

    def fake(prompt, schema, fallback, **kw):
        seen.append(prompt)
        return schema(intent="off_topic")

    monkeypatch.setattr(llm, "complete_json", fake)
    assert intent.classify("¿Va a llover mañana?", "matched") == "off_topic"
    assert intent.classify(TURN2, "matched") == "ask_odds"
    assert len(seen) == 1 and "Stage: matched" in seen[0]


@pytest.mark.parametrize("stage,expected", [("intake", "profile_info"), ("matched", "help"), ("plan", "help")])
def test_llm_down_defaults(stage, expected):
    assert intent.classify("hmm, no estoy segura", stage) == expected


def test_always_returns_a_valid_intent():
    for stage in STAGES:
        for text in ["", "???", "asdf", json.dumps([1, 2])]:
            assert intent.classify(text, stage) in INTENTS


@pytest.mark.parametrize("lang", ["es", "en", "fr"])
def test_scope_reply(lang):
    for stage in STAGES:
        reply = intent.scope_reply(stage, lang)
        assert reply.count(".") + reply.count("?") >= 2
    assert "Choices" in intent.scope_reply("matched", "es")
    assert "posibilidades" in intent.scope_reply("matched", "es")
