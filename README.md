# PhD Explorer

PhD Explorer is an AI pipeline that takes a student profile as input and produces a ranked shortlist of PhD supervisors across target countries. You give it the student's background, skills, research interests, and target intake — and it finds the right PIs, verifies them, scores them, and writes a personalised match explanation for each one.

---

## What it does

We run the student profile through a 7-step LangGraph pipeline:

- We expand the student's research interests into OpenAlex topic IDs and query the OpenAlex API for the most-cited recent papers in those areas.
- We extract all authors from those papers, apply the last-author PI heuristic, and run a 5-layer disambiguation to keep only real faculty supervisors in the student's target countries.
- We attach supporting evidence — top papers, active EU grants from CORDIS, linked doctoral programs from FindAPhD and PhDScanner.
- We filter out PhD positions where the student is not eligible based on their citizenship (using keyword matching + LLM fallback).
- We score every PI on topic match, recency, evidence strength, and seniority, then tier them into reach / target / safety.
- We generate a personalised one-sentence why-match blurb for each PI using the LLM.
- We write the final ranked shortlist to a timestamped JSON file.

Full details in [docs/Wokflow Overview.md](docs/Wokflow%20Overview.md).

---

## Tech Stack

- LangGraph — pipeline orchestration (linear DAG)
- Groq (Gemma 2 9B) via langchain-groq — LLM for query expansion, disambiguation, eligibility checks, and why-match blurbs
- OpenAlex API — paper and author data (free, no key required)
- CORDIS API — EU grant data
- FindAPhD.com + PhDScanner.com — live PhD position listings
- Pydantic v2 — input and output schema validation
- Tenacity — retry with exponential backoff on all API calls
- SHA-256 disk cache with 24h TTL — avoids re-fetching OpenAlex data on every run

---

## Project Structure

```
PhD Explorer/
├── run.py                    # entrypoint — run this
├── config/
│   └── config.yaml           # all tunable parameters (quotas, scoring weights, timeouts, LLM model)
├── data/
│   ├── inputs/               # student profile JSON files go here
│   └── outputs/              # pipeline writes timestamped shortlist JSON files here
├── src/
│   ├── graph/                # LangGraph pipeline wiring
│   ├── nodes/                # one file per pipeline node (profile, retrieval, candidates, evidence, scoring, why_match)
│   ├── orchestrator.py       # assembles the final Shortlist object and writes output
│   └── utils/                # shared helpers — schema, LLM utils, scrapers, cache, email resolver
└── docs/
    ├── Wokflow Overview.md   # step-by-step pipeline explanation
    ├── schema.md             # input and output field reference
    └── Decisions.md          # edge cases — what we handle, what we partially handle, what we don't
```

---

## Setup

**1. Install dependencies**

```
pip install -r requirements.txt
```

**2. Set environment variables**

Create a `.env` file in the project root:

```
GROQ_API_KEY=your_groq_api_key_here

# Optional — for LangFuse tracing
LANGFUSE_SECRET_KEY=...
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_HOST=https://cloud.langfuse.com
```

You can get a free Groq API key at console.groq.com.

**3. Prepare the student profile**

Put the student JSON in `data/inputs/`. See [docs/schema.md](docs/schema.md) for all the fields. The minimum required fields are student_id, skills, research_interests, target_countries, and target_intake.

---

## Running

```
python run.py --profile data/inputs/stu_001.json --out data/outputs/stu_001.json
```

The output file gets a timestamp appended automatically, so it won't overwrite previous runs:

```
data/outputs/stu_001_2026-06-10_20-18-31.json
```

Optional flags:
- `--config` — path to a custom config file (default: `config/config.yaml`)
- `--log-dir` — directory for log files (default: `logs/`)
- `--verbose` — set console log level to DEBUG

---

## Output

The pipeline writes a JSON file with:
- A ranked list of PI recommendations — each with supervisor details, matched papers, active grants, linked PhD programs, a match score, a tier (reach / target / safety), and a personalised why-match sentence.
- A coverage summary showing how many recommendations we got per research area.
- Run metadata — total count, wall clock time, email hit rate, and known limitations for this run.

Full field reference in [docs/schema.md](docs/schema.md).

---

## Configuration

Everything is tunable in `config/config.yaml`:

- `openalex.max_works_per_area` — how many papers to fetch per research area (default 300)
- `openalex.recency_years` — how far back to look for papers (default 5 years)
- `quotas.total_target` — how many PI candidates to aim for before dedup (default 80)
- `quotas.min_final` — warn if fewer than this many recommendations are produced
- `scoring.*_weight` — weights for topic similarity, recency, evidence strength, seniority
- `tiering.reach_threshold` / `target_threshold` — score cutoffs for tier assignment
- `cache.ttl_seconds` — how long to cache OpenAlex responses (default 86400 = 24h)
- `supervision.min_works_count` / `min_h_index` — bibliometric thresholds to drop non-PIs
- `llm.model` — which Groq model to use

---

## Known Limitations

We track what works, what's partially working, and what we haven't solved yet in [docs/Decisions.md](docs/Decisions.md).