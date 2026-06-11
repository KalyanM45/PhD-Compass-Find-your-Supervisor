# PhD Shortlist Builder — Problem Statement & Build Spec

> Working reference document. Consolidates the assignment brief, the system design,
> the data sources, the JSON schemas, and a definition-of-done. Code against this.

---

## 1. Context

Ambitio helps international students apply to PhD programs abroad. A core product feature
is the **PhD Shortlist**: a personalised list of supervisors, programs, and funded
opportunities surfaced for each student, used downstream to draft personalised cold-emails
to professors.

For each student we must produce a shortlist of ~50–200 actionable supervisor + program
matches that a domain mentor would unhesitatingly approve as worth contacting.

**The challenge is not the LLM call. The challenge is the data:** surfacing the right
humans, with the right evidence, in the right context, with no embarrassing mismatches.

The single most important design decision flows from this: **retrieve verifiable evidence
first, derive people second.** Do not ask a model to "list 50 professors" and then hunt for
proof — that hallucinates names, emails, and papers. Instead, query a real paper database
for recent work in the student's topic within the target countries, and let the *authors of
those real papers* become the candidate pool.

---

## 2. Goal

Build a system that ingests a student profile and produces a ranked shortlist of PhD
supervisors + programs, each with grant/paper evidence and a personalised `why_match` a
student can reference when emailing the professor.

---

## 3. Input — Student Profile

A single JSON object. Treat free-text fields (`intro_call_summary`, `raw_resume_text`) as
the richest signal; the structured fields are scaffolding.

```jsonc
{
  "student_id": "stu_001",
  "education": [
    {
      "degree": "MSc Computer Science",
      "institution": "IIT Bombay",
      "country": "IN",
      "grade": "9.1 / 10 CGPA",
      "start_year": 2022,
      "end_year": 2024,
      "thesis_title": "Graph neural networks for molecular property prediction",
      "thesis_abstract": "..."
    }
  ],
  "skills": ["PyTorch", "PyTorch Geometric", "graph neural networks", "RDKit", "SLURM"],
  "projects": [
    {
      "title": "GNN for drug solubility prediction",
      "description": "Implemented a message-passing GNN ...",
      "tech": ["PyTorch Geometric", "RDKit"]
    }
  ],
  "publications": [
    {
      "title": "...",
      "venue": "...",
      "year": 2024,
      "doi": "10.xxxx/xxxxx",
      "role": "first author"
    }
  ],
  "research_interests": [
    "graph neural networks for molecular property prediction",
    "geometric deep learning",
    "AI for drug discovery"
  ],
  "target_countries": ["DE", "NL", "CH"],     // HARD CONSTRAINT, ISO 3166-1 alpha-2
  "target_intake": { "semester": "Fall", "year": 2026 },
  "intro_call_summary": "Student wants to move from cheminformatics toward ...",
  "raw_resume_text": "..."
}
```

**Notes**
- `target_countries` is a **hard constraint** (see Requirement 2). Normalise to ISO codes.
  OpenAlex uses `GB` (not `UK`).
- `research_interests` are *stated* interests — often aspirational. The resume/projects
  reveal *actual* training. These can diverge; capture both (see Stage 1).

---

## 4. Output — Shortlist

A single JSON object per student. **This schema is the contract — document it in the README
and validate every record against it (Pydantic recommended).**

```jsonc
{
  "student_id": "stu_001",
  "generated_at": "2026-06-09T12:00:00Z",
  "target_countries": ["DE", "NL", "CH"],
  "target_intake": { "semester": "Fall", "year": 2026 },
  "recommendations": [
    {
      "supervisor": {
        "name": "Jane Müller",
        "openalex_author_id": "https://openalex.org/A1234567890",
        "orcid": "0000-0002-1825-0097",          // null if not found
        "institution": "ETH Zürich",
        "country": "CH",                          // MUST be in target_countries
        "contact_email": "jane.mueller@ethz.ch",  // null if not confidently found
        "research_focus": "Geometric deep learning for molecular systems"
      },
      "research_area": "geometric deep learning",  // which student area this maps to
      "evidence": {
        "papers": [
          {
            "title": "Equivariant message passing for molecular property prediction",
            "year": 2024,
            "doi": "10.xxxx/xxxxx",
            "url": "https://doi.org/10.xxxx/xxxxx",
            "openalex_id": "https://openalex.org/W...",
            "relevance_note": "Same equivariant GNN family as the student's thesis"
          }
        ],
        "grants": [
          {
            "title": "SNSF: Equivariant deep learning for chemistry",
            "funder": "Swiss National Science Foundation",
            "id": "...",
            "url": "https://...",
            "years": "2023–2027"
          }
        ]
      },
      "why_match": "Your MSc thesis applied message-passing GNNs to molecular property prediction; Prof. Müller's 2024 paper extends this with equivariant message passing for the same task, and her SNSF grant funds exactly this direction.",
      "match_score": 0.82,                         // 0–1, for ranking/audit
      "tier": "reach",                             // reach | target | safety | null
      "linked_programs": [
        {
          "name": "ETH Zürich Doctoral Program in Computer Science",
          "url": "https://...",
          "open_positions": [
            { "title": "PhD in ML for chemistry", "url": "https://...", "deadline": "2026-01-15" }
          ]
        }
      ]
    }
  ],
  "coverage_summary": {
    "graph neural networks for molecular property prediction": 28,
    "geometric deep learning": 22,
    "AI for drug discovery": 18
  },
  "run_metadata": {
    "total_recommendations": 68,
    "wall_clock_seconds": 540,
    "email_hit_rate": 0.61,
    "deferred_limitations": ["alphabetical-authorship fields not handled"]
  }
}
```

**Hard schema rules (validate, fail loudly):**
- `recommendations.length >= 50`
- Every `supervisor.country` ∈ `target_countries`
- Every record has at least one entry in `evidence.papers` **or** `evidence.grants`, each with a working `url`
- `why_match` references a *specific* paper/grant, not generic praise
- `contact_email` is `null` rather than fabricated when not confidently resolved

---

## 5. Requirements

| # | Requirement | How we satisfy it |
|---|-------------|-------------------|
| 1 | **Coverage** — ≥50 actionable recs spread across stated areas | Per-area quota set in Stage 1; retrieve per area; balance in Stage 8 |
| 2 | **Country adherence** — 100% within target countries (hard) | Deterministic ISO normalisation; filter at retrieval (Stage 3) + drop-on-doubt re-check (Stage 6) |
| 3 | **Evidence** — every supervisor has verifiable paper(s)/grant(s) with links | Evidence-first retrieval guarantees this by construction |
| 4 | **Personalisation** — `why_match` references specific PI work mapped to the student | Grounded LLM call fed the specific paper + student capability (Stage 9) |
| 5 | **Machine-readable output** — consistent, documented JSON schema | Pydantic schema (Section 4), documented in README |
| 6 | **Reproducibility** — same input → single-command end-to-end run | `python run.py --profile X --out Y`; cache layer keyed by query hash |
| 7 | **Latency** — < 15 min per shortlist on one laptop / VM | Bulk cached API calls; parallelise email scraping + `why_match` with timeouts |

---

## 6. Data Sources

| Source | Use | Notes |
|--------|-----|-------|
| **OpenAlex** | Backbone: works, authors (with IDs), institutions (with `country_code`), concepts/topics | Free, no API key. Country filter lives here. Use the polite pool (set a `mailto`). |
| **ORCID** | Stable per-human identifier; affiliations; works | Strongest disambiguation signal when present. |
| **Semantic Scholar** | Supplementary paper/author data; SPECTER embeddings | Free API; good for topical similarity. |
| **Crossref** | DOI resolution / metadata fallback | — |
| **CORDIS** | EU Horizon grants | For EU target countries. |
| **DFG GEPRIS** | German research grants | Germany. |
| **UKRI Gateway to Research** | UK grants | UK. |
| **NIH RePORTER / NSF** | US grants | US. |
| University directory pages | Contact email resolution | Scrape last; flaky; null on failure. |

---

## 7. System Design — Pipeline

```
1. Parse + enrich profile        → query plan + student fingerprint
2. Expand queries (per area)     → grounded concept IDs + keywords + per-area quota
3. Evidence-first retrieval      → real papers: topic + country + recent (OpenAlex)
4. Extract PIs from authors      → last-author / seniority heuristics, dedup
5. GATE 1 — disambiguation       → ORCID + topic-fingerprint match (solves §8.1)
6. GATE 2 — country hard filter  → drop if current affiliation uncertain
7. Attach evidence + contact     → papers, grants, email (null if uncertain)
8. Score + tier + balance        → enforce per-area quota; reach/target/safety
9. why_match (grounded) + validate → write shortlist.json + README
```

### Stage 1 — Parse & enrich (two lanes, never mixed)

- **Deterministic lane (no LLM):** `target_countries` → ISO codes; `target_intake` →
  recency floor (≈ last 5 years, the proxy for "PI still active and able to take a student
  for this intake"). These gate hard constraints, so they must be exact and auditable.
- **Semantic lane (LLM, structured output):**
  - Extract a **capability profile** — specific methods/tools/datasets from resume,
    projects, thesis. ("Implemented a message-passing GNN in PyTorch Geometric" >> "interested in AI".)
  - Reconcile **stated vs revealed** interests — aspiration vs actual training; flag gaps
    (drives tiering). The intro-call summary often holds the truest signal.
  - **Ground interests to the OpenAlex concept taxonomy** — map terms to real concept IDs,
    not free-text keywords, to stop query drift.
- **Outputs:** a *query plan* (per area: concept IDs, keyword variants, ISO countries,
  recency floor, target count) and a *student fingerprint* (capability profile + an
  embedding from thesis abstract + project descriptions, reused in Stages 5 and 8).

### Stage 2 — Query expansion with per-area quota
Allocate a target count to each area up front (e.g. 4 areas, target 80 → ~20 each). Retrieve
*per area* so a popular topic doesn't starve a niche one. This is what makes Requirement 1
hold.

### Stage 3 — Evidence-first retrieval (the core)
OpenAlex Works endpoint, per area, compound filter:
`concept/topic` + `institutions.country_code ∈ targets` + `publication_year >= floor` +
minimum citation floor. Pull a few hundred works per area. Evidence, country, and relevance
are satisfied here by construction.

### Stage 4 — Extract PIs (not students/postdocs)
Heuristics: last-author position, stable institutional affiliation, career length, works
count. Deduplicate authors across papers.
*Known limitation:* alphabetical-authorship fields (math/econ) break the last-author
heuristic — document this.

### Stage 5 — GATE 1: Disambiguation (the §8.1 problem)
Never trust a name string. A candidate survives only if multiple independent signals cohere:
OpenAlex author ID + ORCID cross-check, a **topic fingerprint built from the author's entire
body of work** (not just the one matching paper), co-authors, and affiliation history. If the
matching paper is a lone outlier in an otherwise unrelated career → collision → drop.
When signals conflict, drop the candidate. (A missing PI costs nothing; a wrong one costs
credibility.)

### Stage 6 — GATE 2: Country hard filter
Re-confirm each survivor's *current primary* affiliation country from recent works + ORCID
employment. Filter on the *person's* current country, not a paper's affiliation list (a
US-based PI may co-author with a German lab). **Ambiguous → drop.**

### Stage 7 — Attach evidence + contact
Select each PI's 1–3 most relevant works (concept overlap / embedding similarity) with DOI
links. Query the country-appropriate grant DB by PI name + institution (apply the same
disambiguation caution). Resolve email via ORCID → directory scrape; `null` if not confident.

### Stage 8 — Score, tier, balance
`match_score` from topic similarity (student fingerprint vs PI fingerprint), recency,
evidence strength, seniority. Assign `tier` by comparing PI/lab prestige against student
profile strength. Enforce the per-area quotas so the final list is balanced.

### Stage 9 — why_match (grounded) + validate
Feed the LLM the *specific* PI paper(s) + the student's matching capability, constrained to
use only the provided facts → produces specific, non-hallucinated blurbs. Validate every
record against the Section 4 schema. Write `shortlist.json` + README.

---

## 8. Data-Quality Challenges to Address

Graded on which you noticed, the trade-offs you chose, and how you justified them — not on
solving all of them.

### 8.1 Same-name-different-person collisions (priority)
"Wei Wang", "Yu Meng", "Ying Ma", "Sharma" etc. are shared by many researchers. A great
paper by one "Wei Wang" may be wrongly attached to a same-named PI in a different field —
producing a mortifying cold email. **Catching this from a paper title alone is not enough;
verify the human matches the student's research area** via ORCID + whole-career topic
fingerprint + affiliation + co-author triangulation (Stage 5).

### 8.2 Other failure modes worth noting (pick what time allows)
- **Affiliation drift / dual affiliations** — paper country ≠ person's current country
  (handled in Stage 6).
- **Students/postdocs mistaken for PIs** — Stage 4 seniority heuristics; document the
  alphabetical-authorship gap.
- **Stale researchers** — recency floor in Stages 1/3.
- **Email fabrication** — never invent; `null` and report hit-rate.
- **Topic over-breadth** — vague interests → generic profs; mitigated by grounding +
  capability-driven decomposition in Stage 1.
- **Coverage skew** — one area dominating; per-area quotas in Stages 2/8.
- **Grant name collisions** — same disambiguation problem in grant DBs.

---

## 9. Constraint Handling Cheat-Sheet

- **100% country (R2):** deterministic ISO normalisation → retrieval filter → drop-on-doubt
  re-check. When uncertain, exclude.
- **Reproducibility (R6):** pin dependencies; cache every external response keyed by a hash
  of (endpoint + params); single entrypoint `run.py`.
- **Latency < 15 min (R7):** bulk + cached retrieval is fast. The slow/flaky parts — email
  scraping and 50–200 `why_match` calls — run in parallel with hard per-item timeouts; on
  timeout, skip/`null` rather than block the run.

---

## 10. Suggested Tech Stack

- **Python 3.11+**
- `httpx` (async) or `requests` for APIs; `tenacity` for retries/backoff
- `pydantic` v2 for schema validation (input + output)
- `sentence-transformers` (or SPECTER) for embeddings; `numpy` for cosine similarity
- An LLM client (Anthropic) for Stage 1 extraction (structured output) + Stage 9 `why_match`
- `diskcache` or a simple JSON/SQLite cache keyed by query hash
- `selectolax`/`beautifulsoup4` for directory scraping (last resort)
- `asyncio` + bounded semaphore for parallelism with timeouts

---

## 11. Suggested Project Structure

```
phd_shortlist/
├── run.py                  # single entrypoint
├── README.md               # documents schema, design, trade-offs, how to run
├── requirements.txt        # pinned
├── config.yaml             # API mailto, thresholds, quotas, timeouts
├── shortlist/
│   ├── profile.py          # Stage 1: parse + enrich (deterministic + LLM lanes)
│   ├── queries.py          # Stage 2: query plan + per-area quota
│   ├── retrieve.py         # Stage 3: OpenAlex evidence-first retrieval
│   ├── pis.py              # Stage 4: PI extraction
│   ├── disambiguate.py     # Stage 5: GATE 1
│   ├── country_filter.py   # Stage 6: GATE 2
│   ├── evidence.py         # Stage 7: papers, grants, email
│   ├── score.py            # Stage 8: scoring, tiering, balancing
│   ├── why_match.py        # Stage 9: grounded blurb
│   ├── schema.py           # Pydantic models (input + output)
│   └── cache.py            # query-hash cache
└── data/
    ├── inputs/             # sample student profiles
    └── outputs/            # shortlist.json
```

Run: `python run.py --profile data/inputs/stu_001.json --out data/outputs/stu_001.json`

---

## 12. Definition of Done

- [ ] `python run.py --profile <in> --out <out>` runs end-to-end with one command.
- [ ] Output validates against the Section 4 Pydantic schema (CI-style assert).
- [ ] ≥ 50 recommendations, spread across all stated areas (see `coverage_summary`).
- [ ] 100% of `supervisor.country` values ∈ `target_countries` (assert; fail the run if not).
- [ ] Every record carries a paper or grant with a working `url`.
- [ ] `why_match` references a specific named paper/grant for every record (spot-check sample).
- [ ] Disambiguation (Stage 5) implemented and described; a collision test case passes.
- [ ] Wall-clock < 15 min on one machine (record in `run_metadata`).
- [ ] README documents the schema, the design, and — explicitly — **what was handled vs
      consciously deferred and why**.

---

## 13. Trade-offs to State Explicitly in the README

1. **Country/intake handled deterministically** (no LLM) for hard-constraint safety;
   interests handled by constrained LLM output **grounded to OpenAlex concept IDs** to
   prevent query drift.
2. **Evidence-first retrieval** chosen over name-first generation to eliminate hallucinated
   supervisors and guarantee Requirement 3.
3. **Disambiguation via ORCID + whole-career topic fingerprint**; the alphabetical-authorship
   case (math/econ last-author) is *noticed but deferred*.
4. **Drop-on-doubt** for country and identity: precision over recall, because a wrong
   recommendation is far more costly than a missing one.
5. **Email is best-effort** — report the achieved hit-rate; never fabricate.
6. **Latency managed** via caching + bounded-parallel timeouts on the flaky stages.

---

## 14. Open Questions / Things to Confirm

- Do "linked PhD programs / open positions" need to be live-scraped per institution, or is a
  department-level program URL acceptable for v1?
- Is `tier` (reach/target/safety) required, or "if relevant"? (Brief says optional.)
- Expected distribution across reach/target/safety, if any.
- Acceptable email hit-rate floor, if any.