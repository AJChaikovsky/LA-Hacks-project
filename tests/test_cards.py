"""C4 (#22): card payloads match the ASI:One schema; clicks route to the right reply."""
import json
from datetime import date

import pytest
from uagents_core.contrib.protocols.chat import MetadataContent, TextContent

from agents import cards
from core import intent, stubs
from core.types import SessionState

MARIA = stubs.extract("")
EN = MARIA.model_copy(update={"language": "en"})
MATCHES = stubs.match(MARIA)
CODES = [m.code for m in MATCHES]
ESTIMATES = stubs.estimate(MARIA, CODES)
PLAN = stubs.build_plan(MARIA, CODES, "es", date(2026, 10, 17))
STATE = SessionState(stage="plan", profile=MARIA, matches=MATCHES, estimates=ESTIMATES, plan=PLAN)


def all_cards(profile):
    return [
        ("carousel", cards.matches_carousel(MATCHES, profile, ESTIMATES)),
        ("detail", cards.school_detail(MATCHES[0], profile, ESTIMATES[0])),
        ("review", cards.packet_review(PLAN, profile, CODES)),
    ]


@pytest.mark.parametrize("profile", [MARIA, EN], ids=["es", "en"])
def test_payloads_validate(profile):
    for kind, payload in all_cards(profile):
        assert cards.validate(kind, payload) == [], (kind, payload)


def test_card_message_envelope():
    msg = cards.card_message("Encontré 3 escuelas", "carousel", cards.matches_carousel(MATCHES, MARIA, ESTIMATES))
    text, meta = msg.content
    assert isinstance(text, TextContent) and isinstance(meta, MetadataContent)
    md = meta.metadata
    assert md["card_protocol_version"] == "1"
    assert md["requires_card_interaction"] == "true"
    assert md["card_kind"] == "carousel"
    assert isinstance(md["card_payload"], str)
    assert all(isinstance(v, str) for v in md.values())
    payload = json.loads(md["card_payload"])
    assert "items" in payload  # top level, no wrapper envelope
    assert len(md["card_payload"].encode()) < 64 * 1024


def test_carousel_content():
    payload = cards.matches_carousel(MATCHES, MARIA, ESTIMATES)
    assert payload["title"] == "Escuelas para Diego"
    first = payload["items"][0]
    assert first["id"] == "1804703"
    assert first["title"] == "Belvedere MS · STEAM"
    assert first["secondary_text"] == "STEAM · probablemente 4 de 23"
    assert first["primary_cta"] == {"label": "Ver detalles", "selection": {"action": "details", "code": "1804703"}}


def test_detail_rows_and_ctas():
    payload = cards.school_detail(MATCHES[0], MARIA, ESTIMATES[0])
    rows = {r["label"]: r["value"] for r in payload["summary_rows"]}
    assert rows["Teléfono"] == "(323) 266-5400"
    assert rows["Autobús"] == "Pregunte a la escuela"  # transport_note is null in data/schools.json
    assert payload["ctas"][0]["selection"] == {"action": "add", "code": "1804703"}


def test_review_rows():
    payload = cards.packet_review(PLAN, MARIA, CODES)
    rows = {r["label"]: r["value"] for r in payload["summary_rows"]}
    assert rows["Fecha límite"] == "6 de noviembre de 2026"
    assert rows["Escuelas (en orden)"].startswith("1. Belvedere MS · STEAM")


@pytest.mark.parametrize("kind,payload,error", [
    ("slideshow", {"title": "x"}, "unknown card_kind"),
    ("carousel", {"title": "x", "items": []}, "items must be a non-empty list"),
    ("review", {"title": "x", "summary_rows": [{"label": "a", "value": "b"}], "approve_cta": {"label": "ok"}},
     "selection must be an object"),
    ("detail", {"title": "", "summary_rows": [{"label": "a", "value": "b"}]}, "title"),
    ("carousel", {"title": "x", "items": [{"id": "1", "title": "t" * 70000,
                                           "primary_cta": {"label": "a", "selection": {}}}]}, "64 KB"),
])
def test_validate_catches_bad_payloads(kind, payload, error):
    assert any(error in e for e in cards.validate(kind, payload))


def test_invalid_payload_or_cards_off_sends_text_only(monkeypatch):
    assert len(cards.card_message("hola", "carousel", {"title": "x", "items": []}).content) == 1
    monkeypatch.setenv("CARDS", "off")
    assert len(cards.card_message("hola", "carousel", cards.matches_carousel(MATCHES, MARIA)).content) == 1


@pytest.mark.parametrize("text,expected", [
    ('{"action": "details", "code": "1817902"}', {"action": "details", "code": "1817902"}),
    ("Ver detalles: Hollenbeck MS · STEM y Medicina", {"action": "details", "code": "1817902"}),
    ("I'd like to view details for Belvedere MS · STEAM", {"action": "details", "code": "1804703"}),
    ("Agregar a mi lista Bel Vedere STEAM", {"action": "add", "code": "1804703"}),
    ("Ver el correo completo", {"action": "approve_packet"}),
    ("Change something", {"action": "reject_packet"}),
])
def test_clicks_json_and_prose(text, expected):
    sel = intent.parse_card_selection(text)
    assert {k: v for k, v in sel.items() if k != "raw_text"} == expected
    assert intent.classify(text, "matched") == "card_selection"


def test_golden_turn3_is_not_a_card_click():
    assert intent.parse_card_selection(
        "Voy a poner las tres, y una más por si acaso: Markham, aunque queda lejísimos y no sé si lo aceptaría.") is None


def test_respond_to_selection():
    r = cards.respond_to_selection({"action": "details", "code": "1817902"}, STATE)
    assert r.kind == "detail" and r.payload["title"] == "Hollenbeck MS · STEM y Medicina"
    r = cards.respond_to_selection({"action": "add", "code": "1817902"}, STATE)
    assert r.add_code == "1817902" and "Agregué" in r.narration
    r = cards.respond_to_selection({"action": "back"}, STATE)
    assert r.kind == "carousel"
    r = cards.respond_to_selection({"action": "approve_packet"}, STATE)
    assert r.show_email and "Belvedere" in r.narration
    assert cards.respond_to_selection({"action": "reject_packet"}, STATE).narration == "¿Qué quiere cambiar?"
    assert cards.respond_to_selection({"action": "dance"}, STATE) is None


def test_review_card_school_phone_row():
    plan = PLAN.model_copy(update={"coordinator_code": "1804703", "coordinator_phone": "(323) 266-5400"})
    payload = cards.packet_review(plan, MARIA, CODES)
    rows = {r["label"]: r["value"] for r in payload["summary_rows"]}
    assert rows["Teléfono de la escuela (pida al coordinador)"] == "(323) 266-5400"
    assert cards.validate("review", payload) == []


# --- C9 intake form ---

from core.types import FamilyProfile  # noqa: E402


def test_intake_form_asks_only_for_missing_fields():
    empty = cards.intake_form(FamilyProfile(language="es"))
    assert [f["name"] for f in empty["fields"]] == ["grade", "neighborhood", "interest", "iep", "address"]
    assert cards.validate("form", empty) == []
    grade = next(f for f in empty["fields"] if f["name"] == "grade")
    assert grade["options"][0] == {"value": "0", "label": "Kínder"} and grade["options"][5]["label"] == "5.º grado"
    assert "no la guardamos" in empty["fields"][-1]["label"]
    partial = cards.intake_form(MARIA.model_copy(update={"applying_grade": None, "current_grade": None}))
    assert [f["name"] for f in partial["fields"]] == ["grade", "address"]
    assert cards.intake_form(EN)["submit_cta"] == {"label": "Find schools", "selection": {"action": "intake_form"}}


@pytest.mark.parametrize("payload,error", [
    ({"title": "x", "fields": [], "submit_cta": {"label": "a", "selection": {}}}, "fields must be a non-empty list"),
    ({"title": "x", "fields": [{"name": "g", "label": "G", "kind": "select", "options": []}],
      "submit_cta": {"label": "a", "selection": {}}}, "select needs non-empty options"),
    ({"title": "x", "fields": [{"name": "g", "label": "G", "kind": "slider"}],
      "submit_cta": {"label": "a", "selection": {}}}, "kind must be one of"),
    ({"title": "x", "fields": [{"name": "g", "label": "G", "kind": "text"}]}, "submit_cta"),
])
def test_validate_form(payload, error):
    assert any(error in e for e in cards.validate("form", payload))


def test_parse_form_submission_flat_and_nested():
    updates, address = cards.parse_form_submission(
        {"action": "intake_form", "grade": "5", "neighborhood": "boyle hts", "interest": "arte", "iep": "true",
         "address": "312 N Record Ave"})
    assert updates == {"current_grade": 5, "applying_grade": 6, "home_neighborhood": "Boyle Heights",
                       "interests": ["arts"], "iep": True}
    assert address == "312 N Record Ave"
    updates, address = cards.parse_form_submission({"values": {"grade": "kinder?", "neighborhood": "", "iep": False}})
    assert updates == {"iep": False} and address is None


def test_form_submit_reply_and_intake_message(monkeypatch):
    r = cards.respond_to_selection({"action": "intake_form", "grade": "5"}, SessionState())
    assert r.profile_updates == {"current_grade": 5, "applying_grade": 6} and r.address is None
    msg = cards.intake_message(FamilyProfile(language="es"))
    assert msg.content[1].metadata["card_kind"] == "form"
    monkeypatch.setenv("CARDS", "off")
    assert len(cards.intake_message(FamilyProfile(language="es")).content) == 1


# --- UI enrichment: badges, ranking form, one-tap list fixes (through the real agent turn) ---

def _flow(*texts):
    from datetime import date as _d

    from agents.orchestrator import handle_turn
    state, out = SessionState(), []
    for t in texts:
        state, parts, intent = handle_turn(state, t, _d(2026, 10, 17))
        out.append((intent, parts))
    return state, out


GOLDEN_1 = ("Hola, soy Maria. Vivo en Boyle Heights. Mi hijo Diego está en quinto grado y el próximo año entra a sexto. "
            "Tiene un IEP y le encanta el arte, dibuja todo el día. Yo trabajo en el centro y solo tengo mi celular. "
            "¿Qué escuelas me recomienda?")
GOLDEN_3 = "Voy a poner las tres, y una más por si acaso: Markham, aunque queda lejísimos y no sé si lo aceptaría."


def test_carousel_badges():
    payload = cards.matches_carousel(MATCHES, MARIA)
    badges = {i["id"]: [b["label"] for b in i.get("badges", [])] for i in payload["items"]}
    assert badges["1804703"] == ["Arte"]
    assert badges["1817902"] == ["Cerca de casa"]
    assert badges["1804702"] == ["Arte", "Menos solicitudes que lugares"]  # official seat data: 32 apps, 48 seats
    assert cards.validate("carousel", payload) == []
    bad = {"title": "x", "items": [{"id": "1", "title": "t", "badges": [{"label": "x", "variant": "neon"}],
                                    "primary_cta": {"label": "a", "selection": {}}}]}
    assert any("badges" in e for e in cards.validate("carousel", bad))


def test_odds_turn_offers_the_ranking_form():
    state, out = _flow(GOLDEN_1, "¿Qué posibilidades tiene de entrar?")
    part = out[-1][1][0]
    assert part.kind == "form" and part.payload["title"] == "Ordene sus escuelas"
    assert [f["name"] for f in part.payload["fields"]] == ["first", "second", "third"]
    assert cards.validate("form", part.payload) == []


def test_list_card_removes_markham_and_rechecks():
    state, out = _flow(GOLDEN_1, "¿Qué posibilidades tiene de entrar?", GOLDEN_3)
    intent, parts = out[-1]
    card = parts[0].payload
    assert parts[0].kind == "detail" and cards.validate("detail", card) == []
    assert {"label": "No cuenta (máximo 3 magnet)", "value": "Markham MS · Artes"} in card["summary_rows"]
    remove = next(c for c in card["ctas"] if c["selection"].get("action") == "remove")
    assert remove["label"] == "Quitar Markham MS · Artes" and remove.get("primary")

    from datetime import date as _d

    from agents.orchestrator import handle_turn
    state, parts, intent = handle_turn(state, json.dumps(remove["selection"]), _d(2026, 10, 17))
    assert intent == "card_selection" and state.profile.intended_choices == ["1804703", "1817902", "1804702"]
    assert parts[0].narration.startswith("Quité Markham MS · Artes de su lista.")
    assert "Solo puede poner 3" not in parts[0].narration          # the limit warning is gone
    ok = next(c for c in parts[0].payload["ctas"] if c["selection"] == {"action": "list_ok"})
    state, parts, _ = handle_turn(state, json.dumps(ok["selection"]), _d(2026, 10, 17))
    assert state.stage == "plan" and parts[0].kind == "review"


def test_rank_form_submission_sets_the_order():
    from datetime import date as _d

    from agents.orchestrator import handle_turn
    state, _ = _flow(GOLDEN_1, "¿Qué posibilidades tiene de entrar?")
    submit = {"action": "rank_list", "first": "1817902", "second": "1804703", "third": "none"}
    state, parts, intent = handle_turn(state, json.dumps(submit), _d(2026, 10, 17))
    assert state.stage == "listing" and state.profile.intended_choices == ["1817902", "1804703"]
    assert state.profile.language == "es" and parts[0].kind == "detail"
    assert cards.parse_rank_submission({"values": {"first": "", "second": "none"}}) == []
