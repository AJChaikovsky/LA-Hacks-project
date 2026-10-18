# Devpost draft — Choices Counselor

> Draft for C7 (#25). Every number below is from the repo as of Sat Oct 17, 8 PM; re-check the
> ones marked ⟳ after the 8 AM freeze. Fill in the agent addresses from the README (A6).

**Tagline:** A Spanish-first school-choice counselor inside ASI:One that helps LAUSD families get into magnet
schools before the Nov 6 deadline, and stops the mistakes that cost them their spot.

**Track:** Fetch.ai — ASI:One Agent Challenge.

---

## Inspiration

LAUSD's Choices application for 2027-28 is open now, and the on-time deadline is **November 6, 2026**. Paper
applications were eliminated this year. The process has hundreds of programs, a magnet "points" system, and rules
that quietly backfire: if you're not selected, your child is waitlisted **only at your first choice**; declining an
offer, or not answering it, **erases every waiting-list point** you've earned; and you can list **only three**
magnets. Families who are English learners, low-income or have a child with an IEP are the least likely to know
these rules. Research on school choice (Hastings & Weinstein) found that giving families simple information about
their options changes the choices they make.

So we built the counselor a parent like Maria would want: she writes in Spanish, on her phone, the way she talks.

## What it does

Inside one ASI:One conversation, a parent describes their child in Spanish or English. The counselor:

1. **Matches 3 programs** from 49 real grade 6-8 Choices programs (38 magnets, 11 Spanish dual-language),
   explaining each pick ("su programa STEAM incluye arte") and saying honestly when there's no arts-only magnet nearby.
2. **Estimates magnet points** from LAUSD's official rules, including the official PHBAO/overcrowded list for
   697 resident schools, and shows **real competitiveness** from the official Choices pages (e.g. "unas 1,3
   solicitudes por cada lugar"). It never shows a percentage.
3. **Catches costly mistakes** when the parent lists schools: more than 3 magnets, a school she might not accept,
   declines that wipe points, first-choice-only waitlists, plus **ranking advice** ("keep your favorite first";
   "this one had fewer applications than seats last year").
4. **Answers rule questions from the official brochure with a page citation** ("¿Pierdo mis puntos si rechazo un
   lugar?" → the answer + *folleto oficial, página 18*). If the brochure doesn't say, it gives the hotline instead
   of guessing.
5. **Prepares the packet**: a document checklist, every deadline with days left and **"add to calendar" links**,
   the 9 official steps to apply on a phone, and a drafted email to the magnet coordinator in the parent's
   language with an English copy. **It never submits anything** — the parent does.

All of it runs through **ASI:One Interactive Cards**: a school carousel, a detail card with "add to my list", an
intake form when information is missing, and a review card for the final packet.

## How we built it

**Principle: math and rules are code; understanding and writing are the LLM.** Every number the agent says comes
from deterministic Python reading cited data, so it can't hallucinate a deadline or a point value.

- **Fetch.ai uAgents + Agent Chat Protocol**, discoverable and usable in ASI:One.
- **Multi-agent:** `choices-counselor` (orchestrator) hands off to `choices-points` and `choices-paperwork` over
  the mailbox; each reply shows which specialist answered ("Consulté: Agente de Puntos"). If a specialist doesn't
  answer in time, the counselor computes the same result locally, so the conversation never breaks.
- **Payment Protocol:** `choices-caseload` sells batch reports to partner organizations' agents at 0.1 FET per
  family (testnet), with on-chain verification before delivery. Families always use it free.
- **ASI:One LLM (`asi1-mini`)** for understanding free text, routing intents, writing the coordinator email and
  phrasing brochure answers — each behind validation and a deterministic fallback.
- **Brochure Q&A:** 402 passages from the official 2027-28 brochures (English and Spanish) and the Choices FAQ,
  searched with BM25 (pure Python); an answer is rejected unless it cites a retrieved passage and every number in it
  appears in that passage.
- **Always-on host** with health checks (`/health`, `/health/deep`) and a closed-lid test from a phone.
- **Data:** the 2027-28 Choices brochure, the official Choices program pages (seat numbers, saved as evidence),
  LAUSD's Norm Category/Overcrowded list, the official 2027-28 user guide (phone steps), explore.lausd.org, and
  OpenStreetMap for optional exact-address distance (the address is never stored or logged).

## Proof it works ⟳

- **32/32** judge-style personas handled sensibly (grades K-8, every region, Spanish/English/Spanglish, typos,
  off-topic, missing grade, rule questions), **with the LLM on and with it off**.
- The demo conversation passes in Spanish and English on the agent's own code, **p95 ≈ 1.2 s per turn**.
- **10/10** live rule questions answered with the right fact and a page citation.
- **519 automated tests**; every Interactive Card is validated against the ASI:One schema on every run.
- ⟳ Live 10/10 against the hosted agent: _paste the result from `replay_golden.py --live --runs 10`._

## Challenges

- **Trust over fluency.** The LLM wanted to write "mamá de Diego" from the name María; it guessed a grade from an
  age; it once answered an English question in Spanish. Each became a rule enforced in code (we don't pass the
  parent's name to the model, we only accept grades the parent states, and we reject wrong-language answers).
- **Rules that are easy to get wrong.** Points apply to magnets only; the limit is 3 magnets (not 3 programs); the
  waitlist is first-choice only. We re-derived every rule from the official brochure and corrected our own brief.
- **Spanish that reads like a person.** Plain Spanish with "usted", decimal commas, translated places ("el centro de
  Los Ángeles"), official school names.

## Accomplishments we're proud of

A parent can go from "mi hijo está en quinto y le encanta el arte" to a checked list, a cited answer to "what if I
don't respond?", calendar reminders and a ready email, in one conversation on a phone, in Spanish.

## What we learned

Agents earn trust when they're honest about limits: saying "no hay programas para cuarto grado en mi lista" and
pointing to the hotline beats a confident wrong answer.

## What's next

More programs and grades (the data pipeline already reads the brochure), Metro transit times and school context from
California Department of Education open data, all 9 brochure languages, and partnerships with parent centers and
nonprofits through the paid caseload agent.

## Built with

Python · Fetch.ai uAgents · Agent Chat Protocol · ASI:One · ASI:One Interactive Cards · Fetch.ai Payment Protocol ·
Agentverse · asi1-mini · Pydantic · OpenStreetMap Nominatim · pypdf (build time)

## Links

- GitHub: https://github.com/AJChaikovsky/LA-Hacks-project
- Agent in ASI:One: `choices-counselor` — _address from README (A6)_
- Video (2-3 min): _link_ · Fetch video (3-5 min): _link_
