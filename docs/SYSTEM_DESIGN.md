# PhD Explorer — System Design

## Goal
Given a student profile JSON → produce a ranked shortlist of PhD supervisors with evidence and personalised match blurbs.

---

## Architecture

- **Runtime**: Python 3.11, LangGraph linear DAG, ThreadPoolExecutor for parallelism
- **LLM**: Groq API via `langchain-groq` (`ChatGroq`), key-rotation pool
- **External APIs**: OpenAlex (papers + authors), ORCID (employment), CORDIS (grants), web scraping (email, PhD programs)
- **Observability**: Langfuse tracing, structured file logging (daily dirs, pipeline.log + errors.log)
- **Caching**: Disk-based JSON cache for OpenAlex responses (24h TTL)

---

## Pipeline (6 LangGraph nodes, linear)

### 1. `enrich_profile` — ProfileAgent
- LLM call #1: extracts `capability_profile`, `stated_interests`, `revealed_interests`, `openalex_concepts`, `normalised_countries` from student JSON
- 4 LLM calls: hierarchical topic selector (domain → field → subfield → topic) against 4,516 OpenAlex topics
- Output: enriched profile dict + ISO country codes + query plan per research area

### 2. `retrieve_papers` — RetrievalAgent
- Expands each area's `query_hint` with synonym variants (dict lookup, no LLM)
- Parallel OpenAlex `/works` fetch per area (ThreadPoolExecutor)
- Uses `search` param + country + year + topic_ids filters
- Output: `papers_by_area` dict

### 3. `build_candidates` — CandidateAgent
- **Stage 4**: extracts unique PI candidates from paper authorships (last-author heuristic)
- **Stage 5**: disambiguates via OpenAlex author record
  - Keyword overlap ≥ 2 → keep (no LLM)
  - Keyword overlap 0–1 → LLM relevance check (conditional LLM call)
  - Checks institution type (drops company/funder/archive)
  - Checks bibliometric gate (works_count, h_index)
  - ORCID employment check (confirmed faculty overrides metrics)
- **Stage 6**: country hard-filter against student's target countries
- Output: `area_candidates` dict

### 4. `attach_evidence` — EvidenceAgent
- Per candidate (parallel):
  - Papers from OpenAlex author record
  - Grants from CORDIS API
  - Email via web scraping (institution homepage)
  - PhD programs via FindAPhD / PhD Scanner
  - Use-case matching (keyword, no LLM) → annotates papers with `relevance_note`
- Output: enriched `area_candidates`

### 5. `score_and_balance` — ScoringAgent
- Scores each candidate: recency (0.2) + topic similarity (0.5) + evidence strength (0.2) + seniority (0.1)
- Tiers: reach (≥0.75), target (≥0.50), safety (<0.50)
- Balances across areas so no area exceeds 50% of total
- Output: flat `final_candidates` list (target 80)

### 6. `generate_why_match` — WhyMatchAgent
- LLM call per candidate (parallel, bounded by `llm_parallelism_limit`)
- Uses `capability_profile` + candidate's papers + `relevance_note` annotations
- Output: `why_match` string attached to each candidate

---

## LLM Call Budget (per run)

| Stage | Calls | Type |
|---|---|---|
| enrich_profile | 5 (fixed) | structured JSON |
| build_candidates | 0–N (conditional) | yes/no relevance |
| generate_why_match | ~50–80 | free-text blurb |
| **Total** | **~85–145** | |

---

## Key Design Decisions

- **`call_structured` over `with_structured_output`**: handles thinking tokens (`<think>`) + markdown fences from any model
- **Key rotation**: one key active at a time; rotate on 429, let `invoke_with_retry` sleep
- **`search` param not `search.title_and_abstract`**: broader recall, topic filter handles precision
- **Countries via LLM normalisation**: raw profile values (e.g. "India") → ISO codes ("IN") before any API call
- **Disambiguation is conditional**: keyword-clear matches skip LLM entirely (saves ~60–80% of disambiguation calls)
- **`min_final` in config not schema**: lets dev runs produce <50 results without hard crash

---

## Data Flow

```
stu_001.json
    |
    v
[enrich_profile] --LLM--> enrichment + countries + query_plan
    |
    v
[retrieve_papers] --OpenAlex--> papers_by_area
    |
    v
[build_candidates] --OpenAlex+LLM--> area_candidates (filtered)
    |
    v
[attach_evidence] --ORCID/CORDIS/web--> area_candidates (enriched)
    |
    v
[score_and_balance] --arithmetic--> final_candidates (ranked)
    |
    v
[generate_why_match] --LLM--> final_candidates (with blurbs)
    |
    v
stu_001_<timestamp>.json (Shortlist schema)
```

---

## File Layout

```
agents/          — one file per LangGraph node
subagents/       — one file per external call / sub-task
shared/          — clients, cache, schemas, prompts loader, logging
graph/           — LangGraph state + pipeline wiring
prompts/         — all LLM prompt templates (.txt)
data/inputs/     — student profile JSONs
data/outputs/    — shortlist JSONs
data/openalex_data/ — static hierarchy (domains/fields/subfields/topics)
```
