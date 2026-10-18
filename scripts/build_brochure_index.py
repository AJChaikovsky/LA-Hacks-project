"""Build data/brochure_passages.json from the official 2027-28 Choices brochures (EN + SP) and FAQ.

Owner: Dev C, ticket C12 (#46). One-time, offline at runtime: the agent only reads the JSON.

    pip install pypdf certifi    # build-time only; not in requirements.txt
    python scripts/build_brochure_index.py

Keeps prose pages (rules, process, programs info) and skips the school listing tables. Each
passage carries its language, page and a source URL that opens that page.
"""
from __future__ import annotations

import html
import io
import json
import re
import ssl
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "brochure_passages.json"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) choices-counselor-index/1.0"}
BROCHURES = {
    "en": "https://choices.lausd.net/Brochure/Brochure_EN.pdf?27",
    "es": "https://choices.lausd.net/Brochure/Brochure_SP.pdf?27",
}
FAQ_URL = "https://choices.lausd.net/Home/faq"
MIN_WORDS, MAX_WORDS = 40, 110
PROGRAM_CODE = re.compile(r"\b1\d{6}")


def _ssl_context():
    try:  # python.org builds on macOS ship without system CAs; certifi's bundle fixes that
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=60, context=_ssl_context()) as r:
        return r.read()


def clean(text: str) -> str:
    text = text.replace(" ", " ")
    text = re.sub(r"(?m)^\s*n\s+", "• ", text)          # pypdf renders bullets as a lone 'n'
    text = re.sub(r"\b([A-ZÁÉÍÓÚ]) ([A-ZÁÉÍÓÚ]{2,})", r"\1\2", text)  # 'P AGES' -> 'PAGES'
    return re.sub(r"[ \t]+", " ", text).strip()


def chunks(text: str) -> list[str]:
    """Group sentences/bullets into ~40-110 word passages."""
    pieces = [p.strip() for p in re.split(r"(?<=[.?!])\s+|\n+|•", text) if p.strip()]
    out, cur = [], []
    for piece in pieces:
        cur.append(piece)
        if len(" ".join(cur).split()) >= MAX_WORDS * 0.8:
            out.append(" ".join(cur))
            cur = []
    if cur:
        if out and len(" ".join(cur).split()) < MIN_WORDS:
            out[-1] += " " + " ".join(cur)
        else:
            out.append(" ".join(cur))
    return out


def brochure_passages(lang: str, url: str) -> list[dict]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(fetch(url)))
    passages = []
    for number, page in enumerate(reader.pages, 1):
        text = clean(page.extract_text() or "")
        if len(PROGRAM_CODE.findall(text)) > 2 or len(text.split()) < 30:
            continue  # school listing tables or near-empty pages
        for i, chunk in enumerate(chunks(text)):
            passages.append({"id": f"{lang}-p{number}-{i}", "lang": lang, "page": number, "text": chunk,
                             "source_url": f"{url.split('?')[0]}#page={number}"})
    return passages


def faq_passages() -> list[dict]:
    raw = fetch(FAQ_URL).decode("utf-8", "ignore")
    raw = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", "", raw)
    lines = [html.unescape(l).strip() for l in re.sub(r"<[^>]+>", "\n", raw).split("\n")]
    lines = [l for l in lines if l]
    passages, question, answer = [], None, []
    for line in lines:
        if line.endswith("?") and len(line.split()) >= 4:
            if question and answer:
                passages.append((question, " ".join(answer)))
            question, answer = line, []
        elif question:
            answer.append(line)
    if question and answer:
        passages.append((question, " ".join(answer)))
    return [{"id": f"faq-{i}", "lang": "en", "page": None, "text": f"{q} {a}"[:900], "source_url": FAQ_URL}
            for i, (q, a) in enumerate(passages)]


def main() -> int:
    passages = []
    for lang, url in BROCHURES.items():
        passages += brochure_passages(lang, url)
    passages += faq_passages()
    OUT.write_text(json.dumps({"source": "LAUSD Choices 2027-28 brochure (EN, SP) and choices.lausd.net FAQ",
                               "passages": passages}, ensure_ascii=False, indent=1), encoding="utf-8")
    by = {}
    for p in passages:
        by[p["lang"] + ("-faq" if p["page"] is None else "")] = by.get(p["lang"] + ("-faq" if p["page"] is None else ""), 0) + 1
    print(f"wrote {len(passages)} passages to {OUT.relative_to(ROOT)}: {by}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
