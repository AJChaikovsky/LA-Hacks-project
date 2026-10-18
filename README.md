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
| `choices-transit` | Direct LA Metro bus lines from home to each school (prebuilt GTFS index, no network) | _TBD (A4 pattern; `TRANSIT_SEED`, port 8003)_ |
| `choices-facts` | Whole-school context from CDE: enrollment, English learners, students with IEPs | _TBD (A4 pattern; `FACTS_SEED`, port 8004)_ |
| `choices-caseload` | Paid batch reports for partner agents; Payment Protocol (seller), testnet | `agent1qfn674sglphgr6ml02jdkag3ps4dvqrstxm2leqyxza8d56lz7e7qyzj9fx` (Agentverse registration pending) |

## Business model

**Families always use the counselor for free.** Organizations that help many families at once (nonprofits, school
parent centers, college-access counselors) can have their own agent send `choices-caseload` a batch of family
profiles and pay **0.1 FET per family** over the Fetch.ai Payment Protocol (`RequestPayment` -> `CommitPayment` ->
`CompletePayment`). The seller verifies the transfer on-chain before it sends the report: 3 matching programs,
estimated magnet points and the most important warning for each family, built from the same deterministic `core/`
code, with no percentages. Today this runs on the Fetch **testnet** only: `python scripts/caseload_demo.py`.

## Website

The same `choices-counselor` agent also answers on the web. Besides the ASI:One Chat Protocol, it serves
`POST /chat` and `GET /info` on its own HTTP port (`COUNSELOR_PORT`, default 8000; CORS open), so
[web/index.html](web/index.html) is a plain page with no build step. Every message runs through the same turn handler
as ASI:One, and the cards (school carousel, details, intake form, packet review) render as buttons and forms.

```bash
python agents/orchestrator.py                      # the counselor: ASI:One mailbox + the web endpoint
open web/index.html                                # or web/index.html?agent=https://your-host:8000
```

To share it, put the agent's port behind HTTPS (for example a tunnel or the always-on host) and serve `web/`
statically (GitHub Pages works). The page remembers the `?agent=` URL. ASI:One never depends on the website.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in ASI1_API_KEY and seeds
pytest
```

Requires Python 3.10 or newer (tested on 3.13).

## Always-on host (A5)

All three agents run co-located on one Mac (the counselor↔specialist hop is localhost `127.0.0.1`). To (re)start them — the three commands to run at 3 AM:

```bash
cd ~/LA-Hacks-project
./scripts/run_all.sh        # starts all 3 in tmux session "choices" (under caffeinate), then runs ping.py
python scripts/ping.py      # exit 0 = healthy · watch logs with: tmux attach -t choices
```

Keep the Mac on **AC power**. To keep it answering with the **lid closed**: `sudo pmset -a disablesleep 1`
(caffeinate alone does not survive a lid close). **After the event, re-enable sleep:** `sudo pmset -a disablesleep 0`.

## Data sources

LAUSD 2027-28 Choices brochure, choices.lausd.net guidelines and FAQ. `data/schools.json` covers **49 grade 6-8 Choices programs** (magnets and Spanish dual-language) across all four regions, each cited to its brochure page; seat numbers come from the official Choices program pages where we saved them (`docs/evidence/`). Points and odds are **estimates**; the official source is [choices.lausd.net](https://choices.lausd.net) or the Choices Support Line (213) 241-4177.

PHBAO and overcrowded status come from LAUSD's 2026-27 Norm Category/Overcrowded School List (for 2027-28), kept in `docs/evidence/`.

**Privacy:** if a parent shares a street address (optional, in the intake form), it is used once to look up coordinates through OpenStreetMap for distance and bus eligibility, then dropped. The address is never logged or stored; only coordinates rounded to about 100 m stay in the session. Bus eligibility is shown as "may qualify" (official rule: grades 6-12, more than 2 miles, max 90-minute ride).

**Metro bus lines** come from the official [LA Metro bus GTFS feed](https://gitlab.com/LACMTA/gtfs_bus) (feed valid 2026-06-07 to 2026-12-12; used under Metro's developer terms, https://developer.metro.net/). `scripts/build_transit.py` turns it into `data/transit_index.json` once, offline: direct lines only (no transfers), boarding on a weekday morning (6:30-8:15) near the neighborhood centre and getting off within 500 m of the school; the minutes are the median **scheduled** ride, rounded to 5. The agent says "about", never "will take", and doesn't use live arrivals. Lines 70 and 74 from Boyle Heights were checked by hand against Metro's published timetables (`docs/evidence/B13-*`). Turn it off with `TRANSIT=off`.

**School context** comes from the California Department of Education: [Census Day enrollment 2025-26](https://www3.cde.ca.gov/demo-downloads/census/cdenroll2526.txt) (total, English learners, students with disabilities; [file layout](https://www.cde.ca.gov/ds/ad/filesenrcensus.asp)) and [chronic absenteeism 2024-25](https://www3.cde.ca.gov/demo-downloads/attendance/chronicabsenteeism25.txt) ([layout](https://www.cde.ca.gov/ds/ad/filesabd.asp)). `scripts/build_school_facts.py` matches each school by its CDS code (`cds_code` in `data/schools.json`) and writes `data/school_facts.json`; cells CDE suppresses stay null with a reason. The numbers describe the **whole host school**, not only the magnet, and are shown as context in plain fractions ("about 1 in 6 students has an IEP"), never as a percentage, ranking or quality score. Hollenbeck (839 students) and Belvedere (718) were checked against [DataQuest](https://dq.cde.ca.gov/dataquest/). Turn it off with `FACTS=off`.

The competitiveness bands (5+ applications per opening = very high, 2+ = high, 1+ = moderate, under 1 = low, i.e. more seats than applications; in `config/competitiveness.json`) are **our heuristic, not LAUSD's**. The agent never states a probability.
