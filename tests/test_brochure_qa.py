"""C12 (#46): rule questions are answered from the official brochure with a page citation."""
import os

import pytest

from core import brochure_qa as qa
from core import intent, llm

QUESTIONS = [  # (question, lang, a word the right answer must contain when the LLM is on)
    ("¿Qué pasa si no contesto la oferta?", "es", "rechazo"),
    ("What happens if I don't respond to the offer?", "en", "decline"),
    ("¿Cuántas escuelas magnet puedo poner?", "es", "tres"),
    ("How many magnet programs can I apply to?", "en", "three"),
    ("¿Puedo aplicar tarde?", "es", "diciembre"),
    ("Can I apply late?", "en", "December"),
    ("Tengo gemelos, ¿cómo aplico?", "es", "gemel"),
    ("Is there a school bus?", "en", "ransportation"),
    ("¿Pierdo mis puntos de lista de espera si rechazo un lugar?", "es", "puntos"),
    ("Can I change my application after I submit it?", "en", "November 6"),
]


@pytest.fixture
def llm_down(monkeypatch):
    monkeypatch.setattr(llm, "complete_json", lambda prompt, schema, fallback, **kw: fallback)


def test_index_has_both_brochures_and_the_faq():
    ps = qa.passages()
    assert len(ps) > 300
    assert {p.lang for p in ps} == {"en", "es"}
    assert any(p.page is None and "faq" in p.source_url for p in ps)
    page = next(p for p in ps if p.page)
    assert page.source_url.endswith(f"#page={page.page}")


@pytest.mark.parametrize("question,lang,_", QUESTIONS)
def test_rule_questions_route_to_the_brochure_agent(question, lang, _):
    assert intent.classify(question, "matched") == "ask_rules"


@pytest.mark.parametrize("question,lang,_", QUESTIONS)
def test_found_with_citation_when_llm_is_down(llm_down, question, lang, _):
    result = qa.answer(question, lang)
    assert result.found and not result.used_llm and result.passage is not None
    out = qa.render_answer(result, lang)
    assert ("Fuente:" if lang == "es" else "Source:") in out and "](https://" in out


@pytest.mark.parametrize("question,lang", [("¿Cuál es la capital de Francia?", "es"), ("What is the best pizza in LA?", "en")])
def test_off_topic_gets_the_hotline_not_a_guess(llm_down, question, lang):
    result = qa.answer(question, lang)
    assert not result.found
    assert "(213) 241-4177" in qa.render_answer(result, lang)


def test_retrieval_puts_the_right_passage_in_the_top_five():
    def top(q, lang):
        return " ".join(p.text for _, p in qa.search(q, lang)).lower()
    assert "no response is considered a decline" in top("What happens if I don't respond to the offer?", "en") \
        or "considered a decline" in top("What happens if I don't respond to the offer?", "en")
    assert "up to three magnet programs" in top("How many magnet programs can I apply to?", "en")
    assert "tres programas magnet" in top("¿Cuántas escuelas magnet puedo poner?", "es")


def fake(monkeypatch, answer, passage_id):
    monkeypatch.setattr(llm, "complete_json", lambda p, schema, f, **kw: schema(answer=answer, passage_id=passage_id))


def _first_hit(q, lang):
    return qa.search(q, lang)[0][1]


def test_llm_answer_with_bracketed_id_is_accepted(monkeypatch):
    q = "How many magnet programs can I apply to?"
    hit = _first_hit(q, "en")
    fake(monkeypatch, "You can apply to up to three magnet programs.", f"[{hit.id}]")
    result = qa.answer(q, "en")
    assert result.used_llm and result.passage.id == hit.id
    assert qa.render_answer(result, "en").startswith("You can apply to up to three magnet programs.")


@pytest.mark.parametrize("answer", [
    "You can apply to up to 7 magnet programs.",              # number not in the passage
    "Puede solicitar hasta tres programas magnet diferentes.",  # wrong language for an English question
    "",                                                         # model declined
])
def test_bad_llm_answers_fall_back_to_the_quote(monkeypatch, answer):
    q = "How many magnet programs can I apply to?"
    fake(monkeypatch, answer, _first_hit(q, "en").id)
    result = qa.answer(q, "en")
    assert result.found and not result.used_llm
    assert qa.render_answer(result, "en").startswith("The closest passage I found")


def test_unknown_passage_id_falls_back(monkeypatch):
    fake(monkeypatch, "You can apply to up to three magnet programs.", "made-up-id")
    assert not qa.answer("How many magnet programs can I apply to?", "en").used_llm


@pytest.mark.skipif(not os.getenv("RUN_LIVE"), reason="set RUN_LIVE=1 to call ASI:One")
@pytest.mark.parametrize("question,lang,fact", QUESTIONS)
def test_live_answers_state_the_fact(question, lang, fact):
    result = qa.answer(question, lang)
    assert result.used_llm and fact.lower() in result.text.lower(), result.text
