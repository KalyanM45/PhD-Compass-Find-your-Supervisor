# Feedback Loop Design — Outcome-Driven Shortlist Improvement

> Status: Design only — not implemented yet.
> Revisit after first real outcome batch is received.

---

## The Problem

After a shortlist runs, students email supervisors. Weeks later we get back a CSV:

```
student_id, supervisor_id, institution, area, sent_at, outcome
106419, A5031856973, UNSW, PTSD, 2026-07-12, ADMIT
106419, A5055000569, UNSW, PTSD, 2026-07-12, REJECT
106419, A5089892421, Ohio State, PTSD, 2026-07-12, WRONG_PERSON
106419, A5012345678, Oxford, PTSD, 2026-07-12, BOUNCE
```

The question: how do we use this to make the next shortlist better?

---

## Signal Classification

Not all outcomes carry the same type of information.

| Category | Outcomes | What it means |
|----------|----------|---------------|
| Match quality (positive) | ADMIT, INTERVIEW, POSITIVE_REPLY | This PI was a good fit |
| Match quality (negative) | REJECT, NO_REPLY, NOT_RECRUITING | Weak-to-strong negative fit signal |
| Data quality error | BOUNCE, WRONG_PERSON | We surfaced wrong person or wrong email |
| Timing noise | OUT_OF_OFFICE | Ignore — no learning signal |

**Important nuances:**
- REJECT ≠ wrong fit. Lab could be full, PI not recruiting this cycle. Treat as mild negative only.
- NO_REPLY is weak negative. PI could be traveling, email in spam. Don't over-penalise.
- WRONG_PERSON is a disambiguation system failure, not a match failure.
- BOUNCE is an email resolution failure, not a match failure.

---

## The 4 Optimisation Levers

### Lever 1 — Scoring Weight Adaptation

**Current formula (hardcoded):**
```
match_score = 0.5×topic_sim + 0.2×recency + 0.2×evidence + 0.1×seniority
```

**What we do:**
- At scoring time, save a feature snapshot per candidate: `{supervisor_id, topic_sim, recency, evidence, seniority}`
- When outcomes arrive, pair each (student_id, supervisor_id) with its snapshot
- Label: ADMIT/INTERVIEW/POSITIVE_REPLY → 1, REJECT/NO_REPLY/NOT_RECRUITING → 0
- Run logistic regression on (features, labels) to learn which features actually predicted success
- Write new weights to `data/feedback/learned_weights.json`
- `scoring_agent.py` reads this file next run (overrides config defaults)

**Safeguards:**
- Don't update until 30+ labelled outcomes — prevents overfitting to noise
- Blend learned with prior: `new = 0.7×learned + 0.3×prior` — prevents wild swings from one noisy batch
- Log the change so humans can audit it

**Example:** If all ADMITs came from supervisors with high recency but average topic_sim → recency weight increases from 0.2 to maybe 0.3. Next shortlist prioritises recent publishers more.

---

### Lever 2 — Supervisor Reputation Ledger

**What we do:**
- Per supervisor_id, accumulate signed outcome signals over time
- Signed values: ADMIT +3, INTERVIEW +2, POSITIVE_REPLY +1, NOT_RECRUITING -0.8, REJECT -0.5, NO_REPLY -0.3, WRONG_PERSON -2
- Apply time decay (~6 month half-life) so old behaviour doesn't permanently define a PI
- Compute reputation_score in range [-1, +1]
- Convert to a multiplier: e.g. score +0.5 → multiply final rank score by 1.25; score -0.5 → multiply by 0.75
- `scoring_agent.py` applies this multiplier on top of match_score

**Effect:** Same PI, same papers — but a PI with a strong positive history ranks higher. A PI with repeated NO_REPLYs across multiple students sinks in the list.

**Stored in:** `data/feedback/supervisor_ledger.json`

---

### Lever 3 — Disambiguation Threshold Tightening

**What we do:**
- Track WRONG_PERSON rate per run batch
- If WRONG_PERSON rate > 5% → raise the minimum keyword overlap score in `author_disambiguator.py`
- Currently: overlap ≥ 2 keywords = clear match, skip LLM. Overlap 0–1 = LLM decides.
- After high WRONG_PERSON rate: raise clear-match threshold to 3, or lower LLM acceptance rate

**Effect:** Fewer candidates pass disambiguation, but they're the right people. Precision improves at the cost of some recall.

**Stored in:** `data/feedback/disambig_config.json` (read by author_disambiguator as override)

---

### Lever 4 — Tier Threshold Recalibration

**What we do:**
- After each outcome batch, compute: what fraction of "reach" supervisors resulted in ADMIT/INTERVIEW?
- Compare against "target" and "safety" fractions
- If reach and target admit at the same rate → the threshold is meaningless, tighten it
- Adjust reach_threshold and target_threshold in config accordingly

**Example:**
```
reach  (score ≥ 0.75): 12% ADMIT rate
target (score ≥ 0.50): 11% ADMIT rate   ← barely different, threshold is wrong
safety (score < 0.50):  3% ADMIT rate
```
→ Raise reach threshold from 0.75 to 0.82 until reach genuinely outperforms target.

**Stored in:** Updated `config.yaml` tiering section, with history logged to `data/feedback/calibration_history.json`

---

## Data Quality Fixes (Immediate, Deterministic)

These don't require statistical inference — just act on them directly:

| Signal | Action |
|--------|--------|
| BOUNCE | Add email to `email_blacklist.json`. Next run: email_resolver skips known-bad addresses, re-resolves from scratch. |
| WRONG_PERSON | Add supervisor_id to `wrong_person_flags.json`. Next run: candidate_agent skips flagged author IDs entirely. |

---

## Where Each Fix is Applied in the Pipeline

| What changes | File that reads it | When |
|---|---|---|
| `learned_weights.json` | `scoring_agent.py` | Every run — overrides config weights if file exists |
| `supervisor_ledger.json` | `scoring_agent.py` | Every run — multiplier applied to match_score |
| `disambig_config.json` | `author_disambiguator.py` | Every run — tighter overlap threshold if WRONG_PERSON rate high |
| `wrong_person_flags.json` | `candidate_agent.py` | Every run — flagged author IDs dropped at Stage 5 |
| `email_blacklist.json` | `email_resolver.py` | Every run — known-bad emails skipped |
| `calibration_history.json` | `config.yaml` (manual review) | Human reviews, updates config if thresholds need adjustment |

**Key principle:** The pipeline code itself doesn't change. It just reads updated data files that the feedback system wrote. Clean separation between learning and execution.

---

## Proposed File Structure

```
feedback/
  ingest.py              — parse CSV, classify signal types, return typed OutcomeRecord list
  ledger.py              — supervisor reputation store with time decay
  weight_tuner.py        — feature snapshot writer + logistic regression weight adaptation
  report.py              — human-readable summary of what changed and why

run_feedback.py          — CLI: python run_feedback.py --outcomes data/feedback/outcomes.csv

data/feedback/
  supervisor_ledger.json       — reputation scores per supervisor_id
  learned_weights.json         — updated scoring weights
  email_blacklist.json         — bounced emails never used again
  wrong_person_flags.json      — misidentified author IDs skipped in disambiguation
  disambig_config.json         — override for disambiguation thresholds
  calibration_history.json     — tier accuracy over time
  snapshots/{student_id}.jsonl — feature vectors at scoring time (for weight training)
  reports/YYYY-MM-DD.md        — human audit trail per feedback run
```

---

## What Improves Run-Over-Run

| After | What's better |
|-------|--------------|
| Batch 1 (10–20 outcomes) | Reputation scores nudge rankings. Bounced emails re-resolved. WRONG_PERSON supervisors blocked. |
| Batch 2 (30–50 outcomes) | Scoring weights begin adapting — features that predict ADMIT get higher weight. |
| Batch 3+ (50+ outcomes) | Tier thresholds recalibrate. Disambiguation tightens if WRONG_PERSON rate is high. System is genuinely personalised to what works for this student population. |

---

## What We Deliberately Don't Do

- Don't treat NO_REPLY as strong negative — too noisy
- Don't permanently ban a supervisor after one REJECT — labs fill up
- Don't retrain weights on fewer than 30 outcomes — would overfit
- Don't fully replace prior weights with learned weights — blend to stay stable
- Don't auto-update tier thresholds — human reviews calibration report first
