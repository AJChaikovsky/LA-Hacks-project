# Choices Counselor

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

A pocket school-choice counselor for LAUSD families, built on Fetch.ai uAgents and usable entirely inside ASI:One. A parent describes their child in Spanish or English. The agents match 3 Choices programs, estimate magnet points, catch costly listing mistakes, and prepare a ready-to-submit packet before the **Nov 6, 2026** on-time deadline. It never submits anything to LAUSD.

> Work in progress for LA Hacks 2026. The build plan is in [BUILD_PLAN.md](BUILD_PLAN.md).

## Agents

| Agent | Role | Address |
| --- | --- | --- |
| `choices-counselor` | Orchestrator; ASI:One Chat Protocol | _TBD (A1)_ |
| `choices-points` | Magnet points, guardrails | _TBD (A4)_ |
| `choices-paperwork` | Checklist, deadlines, steps, email | _TBD (A4)_ |

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in ASI1_API_KEY and seeds
pytest
```

Requires Python 3.10 or newer (tested on 3.13).

## Data sources

LAUSD 2027-28 Choices brochure, choices.lausd.net guidelines and FAQ. `data/schools.json` covers **49 grade 6-8 Choices programs** (magnets and Spanish dual-language) across all four regions, each cited to its brochure page; seat numbers come from the official Choices program pages where we saved them (`docs/evidence/`). Points and odds are **estimates**; the official source is [choices.lausd.net](https://choices.lausd.net) or the Choices Support Line (213) 241-4177.

PHBAO and overcrowded status come from LAUSD's 2026-27 Norm Category/Overcrowded School List (for 2027-28), kept in `docs/evidence/`.

The competitiveness bands (5+ applications per opening = very high, 2+ = high, 1+ = moderate, under 1 = low, i.e. more seats than applications; in `config/competitiveness.json`) are **our heuristic, not LAUSD's**. The agent never states a probability.
