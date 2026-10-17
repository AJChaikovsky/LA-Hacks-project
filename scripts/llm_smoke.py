"""Live check for ticket C0 (#18): does ASI:One answer, how fast, and does JSON mode work?

Usage: python scripts/llm_smoke.py
Needs ASI1_API_KEY in .env. Paste the summary it prints into ticket #18.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openai  # noqa: E402

from core import llm  # noqa: E402

PROMPT = "Mi hijo Diego está en quinto grado. Responde en una frase en español."


def timed_text(model: str) -> tuple[str | None, float]:
    start = time.monotonic()
    text = llm.complete_text(PROMPT, model=model)
    return text, time.monotonic() - start


def json_mode_supported() -> str:
    client = llm.get_client()
    try:
        resp = client.chat.completions.create(
            model=llm.default_model(),
            messages=[{"role": "user", "content": 'Return {"grade": 5} as JSON.'}],
            response_format={"type": "json_object"},
            temperature=0,
        )
    except openai.BadRequestError as exc:
        return f"NO (400: {exc})"
    except Exception as exc:  # noqa: BLE001
        return f"UNKNOWN ({type(exc).__name__}: {exc})"
    data = llm.extract_json_block(resp.choices[0].message.content)
    return "YES" if data == {"grade": 5} else f"ACCEPTED but reply was {resp.choices[0].message.content!r}"


def main() -> int:
    if not os.getenv("ASI1_API_KEY"):
        print("ASI1_API_KEY is not set. Add it to .env (see .env.example).")
        return 1
    ok = True
    for model in ("asi1", "asi1-mini"):
        text, secs = timed_text(model)
        ok &= text is not None
        print(f"{model:10s} {secs:5.1f}s  {text!r}")
    mode = json_mode_supported()
    print(f"JSON mode (response_format=json_object): {mode}")
    print(f"Suggested .env: ASI1_JSON_MODE={'on' if mode == 'YES' else 'off'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
