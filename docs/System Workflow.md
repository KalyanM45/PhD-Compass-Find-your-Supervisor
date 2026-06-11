# PhD Shortlist Builder — Detailed System Workflow

This document traces every step the system takes from a student JSON file to a
ranked shortlist, including every external API call, every LLM prompt, and every
filtering decision.

---

## High-Level Flow

```
Student JSON
     │
     ▼
┌─────────────────────────────────────────────────────────┐
│  Stage 1  │  Parse profile + LLM enrichment             │
└─────────────────────────────────────────────────────────┘
     │  query_plan  +  student capability profile
     ▼
┌─────────────────────────────────────────────────────────┐
│  Stage 2  │  Expand queries with keyword synonyms        │
└─────────────────────────────────────────────────────────┘
     │  expanded query plan (with keyword_variants)
     ▼
┌─────────────────────────────────────────────────────────┐
│  Stage 3  │  Retrieve real papers from OpenAlex          │
└─────────────────────────────────────────────────────────┘
     │  {area: [papers]}  — typically 100–300 papers/area
     ▼
┌─────────────────────────────────────────────────────────┐
│  Stage 4  │  Extract PI candidates from authorships      │
└─────────────────────────────────────────────────────────┘
     │  {area: [candidate_dicts]}  — typically 50–200/area
     ▼
┌─────────────────────────────────────────────────────────┐
│  Stage 5  │  GATE 1 — Disambiguation (OpenAlex author)   │
└─────────────────────────────────────────────────────────┘
     │  filtered candidates (drop clear field mismatches)
     ▼
┌─────────────────────────────────────────────────────────┐
│  Stage 6  │  GATE 2 — Country hard filter               │
└─────────────────────────────────────────────────────────┘
     │  candidates confirmed in target countries
     ▼
┌─────────────────────────────────────────────────────────┐
│  Stage 7  │  Attach evidence (papers, grants, email)     │
└─────────────────────────────────────────────────────────┘
     │  candidates with evidence_papers, evidence_grants, email
     ▼
┌─────────────────────────────────────────────────────────┐
│  Stage 8  │  Score + tier + balance                      │
└─────────────────────────────────────────────────────────┘
     │  final ranked list (≥ 50 records)
     ▼
┌─────────────────────────────────────────────────────────┐
│  Stage 9  │  Generate why_match blurbs (LLM)             │
└─────────────────────────────────────────────────────────┘
     │
     ▼
  shortlist.json  (Pydantic-validated)
```

---

## Stage 1 — Parse & Enrich Profile

**File:** `shortlist/profile.py`
**Called from:** `run.py` line ~138

### 1a. Deterministic lane (no LLM)

The system reads the student JSON and:
- Normalises `target_countries` to ISO 3166-1 alpha-2 codes
  (`"UK"` → `"GB"`, `"USA"` → `"US"`)
- Computes a **recency floor** year = `target_intake.year − recency_years`
  (default 5). Papers older than this are excluded from retrieval.
  For a Fall 2026 student → floor = 2021.

These are exact, auditable, LLM-free decisions because they gate hard
constraints (country adherence, recency).

### 1b. Semantic lane — LLM call #1

**Purpose:** Extract a structured capability profile and ground research
interests to OpenAlex concept terms.

**Model:** `llama-3.3-70b-versatile` (Groq)

**Prompt sent:**

```
You are an expert at reading academic CVs and research profiles.

Given the student profile below, extract a structured JSON object with:
1. "capability_profile": list of specific methods/tools/datasets the student
   has hands-on experience with (concrete skills, not aspirations).
2. "stated_interests": the research areas the student explicitly says they
   want to pursue.
3. "revealed_interests": research areas implied by their actual work
   (projects, thesis, publications) — may differ from stated.
4. "gap_flags": list of strings describing mismatches between stated and
   revealed interests.
5. "openalex_concepts": list of objects {name, query_hint} grounding each
   research area to searchable OpenAlex concept terms. Include both stated
   and revealed interests.
6. "embedding_text": a 2-3 sentence dense paragraph combining thesis
   abstract + project descriptions — this will be embedded for similarity
   search.

Return only valid JSON, no markdown fences.

Student profile:
{ ...full student JSON... }
```

**Expected LLM response (JSON):**

```json
{
  "capability_profile": [
    "message-passing GNNs in PyTorch Geometric",
    "molecular graph featurisation with RDKit",
    "equivariant architectures (DimeNet++, SE(3))",
    "MoleculeNet benchmarking",
    "SLURM HPC job management"
  ],
  "stated_interests": [
    "graph neural networks for molecular property prediction",
    "geometric deep learning",
    "AI for drug discovery"
  ],
  "revealed_interests": [
    "message-passing GNNs",
    "equivariant neural networks",
    "cheminformatics"
  ],
  "gap_flags": [
    "Student states interest in broad AI for drug discovery but actual work is narrower: GNN-based property prediction"
  ],
  "openalex_concepts": [
    { "name": "graph neural networks for molecular property prediction",
      "query_hint": "graph neural network molecular property prediction" },
    { "name": "geometric deep learning",
      "query_hint": "equivariant neural network geometric deep learning" },
    { "name": "AI for drug discovery",
      "query_hint": "machine learning drug discovery cheminformatics" }
  ],
  "embedding_text": "We develop a message-passing GNN trained on molecular graphs to predict aqueous solubility, lipophilicity, and binding affinity using PyTorch Geometric and RDKit. Extended DimeNet++ with SE(3)-equivariant message passing to predict binding free energies on PDBbind."
}
```

**On JSON parse failure:** falls back to raw `profile.skills` and
`profile.research_interests` — no crash.

### 1c. Build query plan

For each `openalex_concepts` entry the system creates one plan item:

```python
{
  "area":          "geometric deep learning",
  "query_hint":    "equivariant neural network geometric deep learning",
  "countries":     ["DE", "NL", "CH"],
  "recency_floor": 2021,
  "target_count":  26   # total_target(80) / n_areas(3), rounded
}
```

---

## Stage 2 — Query Expansion

**File:** `shortlist/queries.py`

Adds `keyword_variants` to each plan item by checking a hard-coded synonym
table. Example:

```
"graph neural networks" → ["GNN", "message passing neural network", "MPNN"]
"geometric deep learning" → ["equivariant neural network", "geometric ML"]
```

The query plan item becomes:

```python
{
  "area":             "geometric deep learning",
  "query_hint":       "equivariant neural network geometric deep learning",
  "keyword_variants": ["equivariant neural network geometric deep learning",
                       "equivariant neural network", "geometric ML"],
  ...
}
```

> The first variant is always the original `query_hint`. Duplicates are removed.

---

## Stage 3 — Evidence-First Retrieval

**File:** `shortlist/retrieve.py`
**External API:** OpenAlex Works endpoint (`https://api.openalex.org/works`)

For each area in the query plan the system makes one or more paginated GET
requests. **No LLM involved.**

### Request structure

```
GET https://api.openalex.org/works
  ?search=equivariant+neural+network+geometric+deep+learning
  &filter=institutions.country_code:DE|NL|CH,
          publication_year:>2020,
          cited_by_count:>-1
  &per-page=200
  &cursor=*
  &select=id,doi,title,publication_year,cited_by_count,
          authorships,primary_location,concepts,topics
  &mailto=mhemakalyan@gmail.com        ← polite-pool header (not secret)
```

**Filter breakdown:**

| Filter part | Purpose |
|-------------|---------|
| `institutions.country_code:DE\|NL\|CH` | Hard country constraint — only papers where at least one author is at a DE/NL/CH institution |
| `publication_year:>2020` | Recency floor (target_intake 2026 − 5 years) |
| `cited_by_count:>-1` | No minimum (set to 0 in config); all papers included |

**Pagination:** uses OpenAlex cursor-based pagination. Fetches up to
`max_works_per_area` (300) papers per area, across at most 2 pages of 200.

**Caching:** every request is SHA-256 keyed on `(endpoint, params)` and
stored in `.cache/*.json` for 24 h. Re-running the pipeline hits the disk
cache and makes zero API calls.

**Result:** typically 100–300 papers per area, each containing full
authorship data (author ID, author name, institution, country).

---

## Stage 4 — Extract PI Candidates

**File:** `shortlist/pis.py`
**No API calls. No LLM.**

Iterates every paper's `authorships` array. For each author:

1. Checks the author has an OpenAlex author ID.
2. Checks the author's institution `country_code` is in `target_countries`.
3. If so, adds them to the candidate pool for this area.

**PI likelihood heuristic:**

- If the paper has ≤ 2 authors → all authors marked `likely_pi = True`.
- If the paper has > 2 authors → only the last author is marked
  `likely_pi = True`; all others are still kept but scored lower.

**Deduplication:** same `openalex_author_id` appearing across multiple
papers → one candidate entry, multiple papers merged into their `papers` list.

**Output per candidate:**

```python
{
  "openalex_author_id": "https://openalex.org/A2208157607",
  "name":               "Stephan Günnemann",
  "institution":        "Technical University of Munich",
  "country":            "DE",
  "likely_pi":          True,
  "papers":             [ { "title": "...", "year": 2023, "doi": "...", "url": "..." }, ... ],
  "area":               "geometric deep learning"
}
```

---

## Stage 5 — GATE 1: Disambiguation

**File:** `shortlist/disambiguate.py`
**External API:** OpenAlex Authors endpoint (`/authors/{id}`)
**No LLM.**

For each candidate, fetches the full author record:

```
GET https://api.openalex.org/authors/A2208157607
```

The record contains `x_concepts` — a list of research topics covering the
author's entire body of work, each with a relevance `score`.

**Drop logic (precision over recall):**

```
IF  x_concepts is non-empty
AND overlap between concept names and area keywords == 0
THEN drop  ← clear field mismatch (e.g. a chemist named the same as a CS prof)

IF  x_concepts is empty  →  keep  (unverifiable, not wrong)
IF  overlap >= 1         →  keep
```

**Also enriches:**
- `orcid` from author record (if present)
- `works_count` (used in Stage 8 seniority scoring)
- `author_concepts` list (used in Stage 8 topic similarity)

---

## Stage 6 — GATE 2: Country Hard Filter

**File:** `shortlist/country_filter.py`
**External API:** OpenAlex Works endpoint (per-author recent works query)
**No LLM.**

Fetches each candidate's recent works to confirm their **current primary**
affiliation country:

```
GET https://api.openalex.org/works
  ?filter=authorships.author.id:A2208157607,publication_year:>2020
  &per-page=20
  &select=authorships
```

Counts institution country codes across all authorships where this author
appears. The country with > 50% of appearances is "confirmed current country".

**Drop logic:**

```
IF confirmed_country is NOT in target_countries  →  drop
IF country unverifiable (sparse works / API fail)  →  KEEP
  (Stage 3 already enforced country at retrieval; default is trust)
```

---

## Stage 7 — Attach Evidence + Contact

**File:** `shortlist/evidence.py`
**External APIs:** CORDIS (EU grants), ORCID, institution pages (scrape)
**No LLM.**

### 7a. Paper selection
Picks the top 1–3 papers from the candidate's `papers` list, sorted by
recency (year DESC) then citation count (DESC). These become
`evidence.papers` in the output.

### 7b. Grant lookup (CORDIS)
For candidates in EU/CH countries (DE, NL, CH, FR, BE, AT, …):

```
GET https://cordis.europa.eu/api/search/api/search
  ?q="Stephan Günnemann"
  &num=5
  &fl=id,title,startDate,endDate,fundingScheme
```

Returns up to 3 matching EU Horizon grants as `evidence.grants`.

### 7c. Email resolution
Tried in order, stops at first success:

1. **ORCID public API** — `GET https://pub.orcid.org/v3.0/{orcid}/emails`
   (rarely exposes emails but worth trying)
2. **Scrape** — if author record has a `homepage_url`, fetch the page and
   regex-search for email addresses

If neither works → `contact_email = null` (never fabricated).

### 7d. Evidence gate
Candidates with zero papers AND zero grants → dropped (schema requires ≥ 1).
In practice this never fires because Stage 3 guarantees at least one paper.

---

## Stage 8 — Score, Tier, Balance

**File:** `shortlist/score.py`
**No API calls. No LLM.**

### match_score formula

```
match_score = 0.5 × topic_similarity
            + 0.2 × recency_score
            + 0.2 × evidence_strength
            + 0.1 × seniority_score
```

| Component | How computed |
|-----------|-------------|
| `topic_similarity` | Keyword overlap: area + query_hint words vs author's `x_concepts` names. Capped at 1.0 |
| `recency_score` | `max(0, 1 − age/10)` where age = current_year − best_paper_year |
| `evidence_strength` | `min(1, n_papers × 0.2 + total_citations/50 × 0.8)` |
| `seniority_score` | `min(1, works_count / 100)` — proxy for career stage |

### Tier assignment

| match_score | tier |
|-------------|------|
| ≥ 0.75 | `reach` |
| ≥ 0.50 | `target` |
| < 0.50 | `safety` |

### Balancing

Within each area, candidates are sorted by `match_score` DESC and capped at
`min(area_target_count, 50% of total_target)` so no single topic dominates.
Global deduplication removes the same author appearing in multiple areas
(only highest-scoring instance kept).

---

## Stage 9 — Generate why_match Blurbs

**File:** `shortlist/why_match.py`
**LLM call #2 … #N+1** (one call per final candidate, run in parallel)

This is the only other place the LLM is used. One call per recommendation,
up to `parallelism_limit` (10) concurrent threads.

### Prompt sent (per candidate)

```
You are helping a student write a targeted cold-email to a professor.

Write ONE sentence (max 60 words) explaining why THIS student is a strong
match for THIS professor. You MUST reference:
  - A specific paper title or grant name from the evidence below.
  - A specific skill or project from the student profile below.

Do NOT use generic phrases like "your work aligns with mine" or "I am passionate".
Do NOT invent details not present in the inputs.

Student capability profile:
message-passing GNNs in PyTorch Geometric, molecular graph featurisation
with RDKit, equivariant architectures (DimeNet++, SE(3))

Professor evidence:
Papers: "Equivariant message passing for molecular property prediction" (2024);
        "SE(3)-equivariant graph neural networks" (2023)
Grants: "DFG: Geometric deep learning for molecular systems" (German Research Foundation)

Return only the single sentence. No preamble, no quotes.
```

**Expected LLM response:**

```
Your MSc thesis implemented SE(3)-equivariant message passing in
PyTorch Geometric for molecular property prediction — exactly the
method behind Prof. Günnemann's 2024 paper and his active DFG grant
on geometric deep learning for molecular systems.
```

**Timeout:** 30 s per call. On timeout or error, a deterministic fallback
sentence is used (references the top paper title directly — never generic).

**Parallelism:** up to 10 concurrent Groq calls via `ThreadPoolExecutor`.

---

## Output Validation

After Stage 9, every record is built into a `Recommendation` Pydantic model
and the whole list into a `Shortlist`. Pydantic enforces:

| Rule | Error if violated |
|------|------------------|
| `len(recommendations) >= 50` | `ValidationError` — run fails loudly |
| `supervisor.country ∈ target_countries` | `ValidationError` — run fails loudly |
| `Evidence` has ≥ 1 paper or grant | `ValidationError` on construction |
| `contact_email` is `str \| null` | Pydantic type check |

---

## LLM Call Summary

| Call | Stage | Model | When | Input size | Output |
|------|-------|-------|------|-----------|--------|
| Profile enrichment | 1b | `llama-3.3-70b-versatile` | Once per run | Full student JSON (~500–2000 tokens) | JSON with capability profile + concept groundings |
| why_match blurbs | 9 | `llama-3.3-70b-versatile` | Once per final candidate (50–200×, parallel) | ~200 tokens each (PI papers + student skills) | One sentence per call |

**All other stages are deterministic** — no LLM, only REST API calls and
local computation.

---

## External API Call Summary

| API | Stage | Purpose | Auth |
|-----|-------|---------|------|
| OpenAlex `/works` | 3 | Paper retrieval by topic+country | None (mailto header) |
| OpenAlex `/authors/{id}` | 5 | Author concept fingerprint | None |
| OpenAlex `/works` (per-author) | 6 | Current country verification | None |
| CORDIS search API | 7 | EU grant lookup | None |
| ORCID public API | 7 | Email resolution | None |
| Institution homepage scrape | 7 | Email resolution fallback | None |
| Groq API | 1, 9 | LLM inference | `GROQ_API_KEY` |

---

## Data Flow Diagram (Token-level)

```
stu_001.json
  └─► Stage 1 LLM call  ──► capability_profile, openalex_concepts
        │
        └─► query_plan: [{area, query_hint, countries, recency_floor, target_count}]
                │
                └─► Stage 3 OpenAlex /works (per area)
                      │
                      └─► papers_by_area: {area: [paper_dict, ...]}
                            │
                            └─► Stage 4 (local)
                                  │
                                  └─► area_candidates: {area: [candidate_dict, ...]}
                                        │
                                        ├─► Stage 5 OpenAlex /authors (per candidate)
                                        │     └─► filtered area_candidates
                                        │
                                        ├─► Stage 6 OpenAlex /works (per candidate)
                                        │     └─► country-verified candidates
                                        │
                                        ├─► Stage 7 CORDIS + ORCID + scrape
                                        │     └─► candidates with evidence + email
                                        │
                                        ├─► Stage 8 (local scoring)
                                        │     └─► final_candidates (≥50, ranked)
                                        │
                                        └─► Stage 9 Groq (parallel, per candidate)
                                              └─► why_match sentences
                                                    │
                                                    └─► shortlist.json
```
