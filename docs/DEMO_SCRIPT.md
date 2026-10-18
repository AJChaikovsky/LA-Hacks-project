# Demo script and slides — Choices Counselor

> For C7 (#25). Two cuts: the **live judging demo / Devpost video (3:00)** and the **Fetch video (≈4:30, must be
> 3-5 min)**. Speakers: **C = Rishi** drives ASI:One, **A = ochodolo** narrates architecture, **B = AJ** takes
> rules questions. Rehearse 3 times with a timer.

## Setup (10 minutes before)

- [ ] Host Mac on AC power, `python scripts/ping.py` exits 0, phone hotspot ready as backup Wi-Fi
- [ ] Fresh ASI:One chat, browser at phone width (~420 px), zoom 125%
- [ ] Second window: `tmux attach -t choices` (the handoff log lines are visible)
- [ ] Messages below pasted in a notes file to copy from (type turn 1 live only if confident)
- [ ] Backup video open in a tab, paused at 0:00; switching to it takes under 10 s
- [ ] Pre-demo reset: stop the agent, delete `agent1q*_data.json`, restart (README, A3 note)

**Use the exact wording below.** The 3rd school depends on "trabajo en el centro".

## Live demo / Devpost video — 3:00

| Time | Who | Screen | Say |
| --- | --- | --- | --- |
| 0:00-0:25 | C | Slide 1 | "LAUSD's school-choice deadline is November 6 — 20 days after this hackathon. No more paper applications. Hundreds of programs, a points system, and rules that quietly cost families their spot. Meet Maria: Boyle Heights, works downtown, only a phone, prefers Spanish. Her son Diego is in 5th grade, has an IEP, and loves art." |
| 0:25-0:55 | C | Turn 1 → carousel | "She writes the way she talks." *(send turn 1)* "Three real programs, each with a reason — and it's honest: there's no arts-only magnet on the Eastside, so it explains why STEAM fits. Tap a school…" *(tap Belvedere STEAM → detail card)* |
| 0:55-1:20 | C | Turn 2 → points | *(send turn 2)* "Points from LAUSD's official rules and the official PHBAO list — 'probablemente 4 de 23'. And real competition from the official pages: about 1.3 applications per seat. Never a percentage. Notice the line 'Consulté: Agente de Puntos' — a separate agent did that math." |
| 1:20-1:55 | C | Turn 3 → warnings | "This is the moment that matters." *(send turn 3)* "She adds a fourth school 'just in case'. The counselor stops her: only 3 magnets count; only list schools you'd accept, because declining erases your points; order matters. Then strategy: keep your favorite first, and here's a safer choice with fewer applications than seats." |
| 1:55-2:15 | C | Rule question | *(send)* "¿Qué pasa si no contesto la oferta?" → "Answered from the official brochure, with the page. If the brochure doesn't say, it gives the hotline instead of guessing." |
| 2:15-2:35 | C | Turn 4 → review card | *(send turn 4)* "A checklist, every deadline with calendar reminders, the official phone steps, and a ready email to the coordinator — in Spanish with an English copy. It never submits for her." |
| 2:35-3:00 | A | Slide 2 | "Under the hood: three Fetch agents — the counselor orchestrates, Points and Paperwork are specialists, with a local fallback if one goes down. Math and rules are code; the LLM only understands and writes. 32 of 32 test personas, in Spanish and English, with the LLM on or off. Free for families; organizations pay per family through the Payment Protocol." |

### Messages (copy exactly)

1. `Hola, soy Maria. Vivo en Boyle Heights. Mi hijo Diego está en quinto grado y el próximo año entra a sexto. Tiene un IEP y le encanta el arte, dibuja todo el día. Yo trabajo en el centro y solo tengo mi celular. ¿Qué escuelas me recomienda?`
2. `¿Qué posibilidades tiene de entrar?`
3. `Voy a poner las tres, y una más por si acaso: Markham, aunque queda lejísimos y no sé si lo aceptaría.`
4. `¿Qué pasa si no contesto la oferta?`
5. `Está bien, solo las tres. ¿Qué necesito para terminar?`

(@mention `choices-counselor` on each message in ASI:One.)

## Fetch video — ≈4:30 (3-5 min required)

Same as above, plus, after 2:35:

| Time | Who | Screen | Say |
| --- | --- | --- | --- |
| 2:35-3:10 | A | tmux logs | "Each specialist is its own agent on Agentverse. Here are the handoffs." *(point at the handoff lines)* "Now I stop the Points agent…" *(stop it, re-ask turn 2)* "…and the counselor answers anyway with its local fallback; the 'Consulté' line disappears so we never claim a handoff that didn't happen." |
| 3:10-3:40 | B | Terminal | `python scripts/caseload_demo.py` — "A nonprofit's agent sends a batch of families; our caseload agent requests 0.1 FET per family over the Payment Protocol, verifies the testnet transfer, then delivers the report. Families never pay." |
| 3:40-4:05 | C | English chat | One English turn 1 to show it's fully bilingual, plus the intake form when the grade is missing. |
| 4:05-4:30 | A | Slide 2 | Architecture + proof numbers + "built during LA Hacks" + the Submission Agent registration. |

## Slides

**Slide 1 — The problem** (title: "20 days. Hundreds of programs. One wrong click.")
- Nov 6, 2026 on-time deadline · online only this year
- 3 rules that cost families: only 3 magnets · waitlisted at 1st choice only · declining (or not answering) erases points
- Maria: Boyle Heights · phone only · Spanish · son with an IEP who loves art

**Slide 2 — How it works** (title: "Math and rules are code. Language is the LLM.")
- Diagram: ASI:One → `choices-counselor` → `choices-points` / `choices-paperwork` (+ local fallback) · `choices-caseload` (Payment Protocol)
- Data: official 2027-28 brochure (EN/SP) · Choices seat pages · PHBAO/overcrowded list · official user guide
- Proof: 32/32 personas (LLM on/off) · 10/10 cited rule answers · p95 ≈ 1.2 s/turn · 519 tests ⟳

## If something breaks on stage

- Agent silent > 10 s: "Let me show you the run from this morning" → play the backup video.
- Wrong or odd reply: don't debug live — move to the next turn; the script still lands.
- Judge asks a rules question: B answers, citing the brochure page (points are on p. 18).
