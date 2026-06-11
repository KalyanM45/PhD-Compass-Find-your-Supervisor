# PhD Shortlist Builder — Project Status & Review

> **How to use this file:**  
> Review each row. Add your comments in the **Review Comments** column.  
> Statuses: ✅ Done · ⚠️ Partial · ❌ Not Started · 🔄 In Progress

---

## 0. Current Phase Summary

| Phase | What's in it | Status | Next Action |
|-------|-------------|--------|-------------|
| **Phase 1 — Input & Profile** | Student profile schema, LLM enrichment (capability_profile / stated / revealed interests), 4-step OpenAlex topic selection, ISO country normalisation | ✅ **Finalized** | — |
| **Phase 2 — Paper Retrieval** | OpenAlex paper retrieval filtered by topic IDs + country + recency + citations, sorted by citations, article/preprint only | ✅ **Finalized** | — |
| **Phase 3 — Supervisor Extraction** | PI extraction from papers (last-author heuristic), author disambiguation (x_concepts fingerprint), country re-verification (recent works majority vote) | ⚠️ **Partial** | No ORCID affiliation cross-check · concept overlap is keyword substring not semantic · no co-author triangulation · no hard seniority drop gate |
| **Phase 4 — Evidence & Scoring** | Top-3 paper selection, CORDIS grant fetch, email resolution, match scoring, tier assignment, per-area balancing | ⚠️ **Partial** | Grant coverage EU-only (UKRI/NIH missing) · email hit rate 20–40% · keyword similarity not semantic |
| **Phase 5 — Output & Blurbs** | why_match blurb generation (LLM, grounded, parallel), Pydantic output schema, timestamped output files | ✅ Done | — |
| **Phase 6 — Observability** | LangGraph execution, Langfuse LLM tracing, date-keyed log files, disk cache | ✅ Done | — |

---

## Pending Fixes — Next Session

> Items found during full audit on 2026-06-10. Fix in order listed.

### Bug 1 — email_resolver broken (`_author_record` never populated) ❌

**Root cause:** `subagents/email_resolver.py` reads `candidate.get("_author_record", {}).get("homepage_url")` but `_author_record` is never stored in the candidate dict.  
**Where it breaks:** `author_disambiguator.disambiguate_candidate()` fetches the full OpenAlex author record (has `homepage_url`) but only copies `orcid`, `h_index`, `works_count`, `cited_by_count`, `i10_index`, `author_concepts` out — never `homepage_url`.  
**Impact:** Homepage scrape fallback in email_resolver is completely unreachable. Email hit rate stays at ORCID-only (~10–15% instead of 20–40%).

**Fix — 2 files:**

1. `subagents/author_disambiguator.py` — where enriched fields are written back to `cand` (the block that sets `cand["orcid"]`, `cand["h_index"]` etc.), add:
   ```python
   cand["homepage_url"] = author_record.get("homepage_url") or ""
   ```

2. `subagents/email_resolver.py` — change line that reads `_author_record`:
   ```python
   # BEFORE
   homepage = candidate.get("_author_record", {}).get("homepage_url")
   # AFTER
   homepage = candidate.get("homepage_url") or ""
   ```

---

### Bug 2 — `program_fetcher` passes `candidate.get("_author_record")` for homepage (same root cause) ❌

**Root cause:** Old `evidence_agent.py` used `candidate.get("_author_record", {}).get("homepage_url")` to pass homepage to program_fetcher. The new `program_fetcher` reads `candidate.get("homepage_url")` directly — correct — but `homepage_url` is only available after Bug 1 is fixed.  
**Impact:** `homepage` strategy in program_fetcher is already removed (sources now only FindAPhD + PhDScanner), so no action needed here once Bug 1 is fixed.  
**Status:** No separate fix needed — resolved by Bug 1 fix.

---

### Feature Gap 1 — No `citizenship` field on `StudentProfile` ⚠️

**File:** `shared/schema.py` — `StudentProfile` has `target_countries` but not the student's own citizenship/fee-status.  
**Impact:** FindAPhD and PhDScanner extract `eligible_citizenships` from ad text (e.g. `["UK_only"]`, `["EU", "International"]`) but nothing filters against student's passport. Ineligible positions can appear in output.

**Fix:**
1. `shared/schema.py` — add to `StudentProfile`:
   ```python
   citizenship: Optional[str] = None  # ISO-2 code, e.g. "IN", or "International"
   ```
2. `subagents/program_fetcher.py` — after collecting vacancies, filter:
   ```python
   if citizenship:
       vacancies = [v for v in vacancies if _is_eligible(v, citizenship)]
   ```
   Where `_is_eligible` returns True if `eligible_citizenships` is `["Unknown"]` or contains `"International"` or matches the student's code.
3. `data/inputs/*.json` — add `"citizenship": "IN"` (or relevant code) to student profile files.

---

### Feature Gap 2 — Grant coverage EU-only (CORDIS) ⚠️

**File:** `subagents/grant_fetcher.py`  
**Impact:** US and UK supervisors show 0 grants in evidence — reduces evidence quality for those regions.  
**Deferred:** UKRI Gateway to Research, NIH RePORTER, NSF Award Search, DFG GEPRIS not integrated.  
**No code change needed today** — already documented in §8 Known Deferred Limitations. Add when expanding to US/UK.

---

## 1. Pipeline Graph — Main Agent Nodes

| Step | Node | File | Status | What It Does | Code Notes | Workflow Notes | Review Comments |
|------|------|------|--------|-------------|------------|----------------|-----------------|
| 1 | `enrich_profile` | `agents/profile_agent.py` | ✅ Done | LLM extracts capability_profile (from thesis/projects/publications only — not skills list), stated/revealed interests, gap_flags, OpenAlex concepts, embedding_text, normalised ISO countries. Then runs 4-step topic selector. | `with_structured_output(ProfileEnrichment)` — Pydantic schema passed as response_format to Groq. No fallback on failure (raises). `_ISO_OVERRIDES` removed — LLM normalises countries. `select_topics()` runs 4 sequential LLM calls: domain→field→subfield→topics using `data/openalex_data/*.json`. | Two lanes: deterministic (ISO, recency floor) + LLM (enrichment + topic selection). Prompts in `prompts/profile_enrichment.txt` — deep field-by-field rules. `__main__` driver for isolated testing. | |
| 2 | `retrieve_papers` | `agents/retrieval_agent.py` | ✅ Done | Expands queries with synonyms then fans out parallel paper fetch per area. OpenAlex filtered by topic IDs + country + year + citations + article/preprint only, sorted by cited_by_count desc. | Uses `ThreadPoolExecutor`. `paper_fetcher` passes `topic_ids` from query plan into `search_works()` as `topics.id:T1\|T2` filter. `search.title_and_abstract` + `topics.id` combined for precision. | Query expansion: static synonym table. Topic IDs from Step 1 selector added to every area's filter — narrows to OpenAlex-classified papers only. Falls back to text-only if no topic IDs selected. | |
| 3 | `build_candidates` | `agents/candidate_agent.py` | ✅ Done | Extract PIs (Stage 4) → Disambiguate (Stage 5, parallel) → Country filter (Stage 6, parallel) | All three sub-stages in one node. Parallelism bounded by `parallelism_limit` config. | Last-author heuristic for PI detection. Disambiguation uses x_concepts fingerprint. Country re-verified from recent works. | |
| 4 | `attach_evidence` | `agents/evidence_agent.py` | ✅ Done | Selects top 3 papers, fetches CORDIS grants, resolves email, fetches linked programs (3-strategy) | Parallel per candidate. Evidence gate drops zero-evidence candidates (rare in practice). | Grant coverage only CORDIS (EU/CH). linked_programs now populated via lookup + FindAPhD + homepage scrape. | |
| 5 | `score_and_balance` | `agents/scoring_agent.py` | ✅ Done | Scores by topic_similarity + recency + evidence_strength + seniority. Assigns tier. Enforces per-area quota. | Keyword overlap proxy for topic similarity (no embeddings). Seniority uses h_index + cited_by_count + works_count + i10_index composite. | No area gets >50% of total. Global dedup by author ID. Final list sorted by match_score DESC. | |
| 6 | `generate_why_match` | `agents/why_match_agent.py` | ✅ Done | Generates one grounded sentence per candidate referencing specific paper/grant + student skill. Parallel LLM calls. | Bounded by `parallelism_limit`. Falls back to deterministic sentence on timeout/error — never generic. | LLM constrained to provided facts only — designed to prevent hallucination. 30s timeout per item. | |

---

## 2. Subagents — Atomic Operations

| Subagent | File | Called By | Status | What It Does | Code Notes | Workflow Notes | Review Comments |
|----------|------|-----------|--------|-------------|------------|----------------|-----------------|
| Query Expander | `subagents/query_expander.py` | `retrieval_agent` | ✅ Done | Adds keyword synonym variants to a single area's query_hint | Static `_SYNONYMS` dict. Deduplicates. Only first matching canonical key fires. | Covers GNN, geometric DL, drug discovery, NLP, CV, RL. Extend for new domains. | |
| Paper Fetcher | `subagents/paper_fetcher.py` | `retrieval_agent` | ✅ Done | Fetches OpenAlex works for one area using compound filter (topic + country + year + citations) | Wraps `OpenAlexClient.search_works`. Paginated via cursor. 24h disk cache. | Country hard-filtered at retrieval (Stage 3 guarantee). Min citations configurable. | |
| Author Disambiguator | `subagents/author_disambiguator.py` | `candidate_agent` | ⚠️ Partial | Fetches OpenAlex author record; drops candidate if x_concepts present but zero overlap with area keywords | Precision over recall: drop on clear mismatch, keep on empty concepts (unverifiable ≠ wrong). Enriches with ORCID, h_index, works_count. | **Gap**: No ORCID cross-check with affiliation. No co-author triangulation. Concept overlap is keyword substring match, not semantic. | |
| Country Verifier | `subagents/country_verifier.py` | `candidate_agent` | ✅ Done | Re-confirms candidate's current country from their last 20 recent works. Majority vote (>50%). | Keep on ambiguous/unverifiable (Stage 3 already enforced at retrieval). Drop only on positive wrong-country evidence. | Handles affiliation drift (paper country ≠ current country). | |
| Grant Fetcher | `subagents/grant_fetcher.py` | `evidence_agent` | ⚠️ Partial | Queries CORDIS for EU/CH grants by PI name. Returns up to 3 matching grants. | Covers DE, NL, CH, FR, BE, AT, SE, FI, DK, NO. Uses tenacity retry (2 attempts). | **Gap**: DFG GEPRIS (Germany), UKRI Gateway (UK), NIH RePORTER/NSF (US) not implemented. Grant-name collisions not handled. | |
| Email Resolver | `subagents/email_resolver.py` | `evidence_agent` | ⚠️ Partial | Tries ORCID public API then homepage scrape. Returns None if not found — never fabricates. | Regex-based extraction. Filters `.png`/`example`/`noreply` matches. | Hit rate typically 20–40%. ORCID rarely exposes emails publicly. Scrape is flaky/slow. | |
| Blurb Generator | `subagents/blurb_generator.py` | `why_match_agent` | ✅ Done | Generates one constrained why_match sentence per PI using their specific papers/grants + student capability. | Max 60 words. Must reference a specific paper/grant title. No generic phrases allowed by prompt. Deterministic fallback on error. | One Groq API call per candidate. Prompt designed to prevent hallucination. | |

---

## 3. Shared Utilities

| Module | File | Status | Purpose | Review Comments |
|--------|------|--------|---------|-----------------|
| Schema | `shared/schema.py` | ✅ Done | Pydantic v2 models for StudentProfile + Shortlist. Hard validation rules (≥50 recs, country in target). | |
| LLM Schemas | `shared/llm_schemas.py` | ✅ Done | Pydantic schemas for LLM outputs (`ProfileEnrichment`, `OpenAlexConcept`). Passed as `response_format` to Groq via `with_structured_output()`. | |
| Prompts Loader | `shared/prompts.py` | ✅ Done | Loads `.txt` prompt files from `prompts/` directory by name. | |
| Cache | `shared/cache.py` | ✅ Done | SHA-256 keyed disk cache (24h TTL). Ensures reproducibility — same input hits zero APIs on re-run. | |
| Groq Client Pool | `shared/clients.py` | ✅ Done | Multi-key round-robin pool. Rotates to next key instantly on 429. Parses Groq `retry-after` exactly ("try again in 650ms"). `invoke_with_retry()` with 5 attempts. Langfuse `CallbackHandler` attached at pool creation. | |
| OpenAlex Client | `shared/clients.py` | ✅ Done | `search_works()` uses `search.title_and_abstract` + `topics.id` filter + `sort=cited_by_count:desc` + `type:article\|preprint`. Cursor-based pagination. Tenacity retry (3 attempts). | |
| Topic Selector | `subagents/topic_selector.py` | ✅ Done | Loads `data/openalex_data/*.json` (4 flat files). 4-step LLM chain narrowing ~4500 topics to relevant set. Each step only shows children of selected parent. Returns topic ID list for OpenAlex filter. | |
| Logging Config | `shared/log_config.py` | ✅ Done | Date-based log directories (`logs/YYYY-MM-DD/pipeline.log` + `errors.log`). Silences noisy 3rd-party loggers. | |

---

## 4. Graph Execution Engine

| Module | File | Status | Purpose | Review Comments |
|--------|------|--------|---------|-----------------|
| Pipeline Graph | `graph/pipeline_graph.py` | ✅ Done | Real LangGraph `StateGraph` with `TypedDict` state. `START → enrich_profile → retrieve_papers → build_candidates → attach_evidence → score_and_balance → generate_why_match → END`. Compiled with `.compile()`. | |
| State & Context | `graph/state.py` | ✅ Done | `PipelineState(TypedDict, total=False)` flows through nodes. `PipelineContext` carries shared resources (LLM pool, OpenAlex client, config). | |
| Orchestrator | `agents/orchestrator.py` | ✅ Done | Builds graph via `build_pipeline(context)`, calls `.invoke(initial_state)`. Assembles Pydantic `Shortlist` from final state. | |

---

## 5. Data Quality Challenges — PDF §6

### 5.1  Same-name-different-person collisions  (PDF §6.1) — Priority

| | |
|---|---|
| **PDF concern** | "Yang Shi", "Wei Wang", "Sharma" — same name, different researcher. A great paper by one "Wei Wang" may be wrongly attached to a same-named PI in a different field. Catching the mistake from a paper title alone is not enough. |
| **Status** | ⚠️ Partial |
| **Where handled** | `subagents/author_disambiguator.py` (Stage 5) |
| **How** | Fetches author's full OpenAlex record → extracts `x_concepts` (career-wide topic fingerprint) → drops candidate if concepts are present but share zero keyword overlap with the student's research area |
| **What works** | Catches clear cross-field collisions (e.g., a chemistry "Wei Wang" surfaced for a CS student) |
| **What's missing** | ❌ ORCID affiliation cross-check not implemented (only stored if found) · ❌ Co-author triangulation not done · ❌ Concept overlap is substring match, not semantic similarity — a researcher who uses "neural" but not "graph" would still fail the check even if they're relevant |
| **Review Comments** | |

---

### 5.2  Career-stage errors — PhD students / postdocs mistaken for PIs  (PDF §6.2)

| | |
|---|---|
| **PDF concern** | PhD students and fresh postdocs appear in author databases with first-author papers but cannot supervise. Personal fellowships (NIH F31/F32, UKRI studentships, MSCA postdoc grants) list the awardee — usually a junior researcher. |
| **Status** | ⚠️ Partial |
| **Where handled** | `agents/candidate_agent.py` (Stage 4) · `agents/scoring_agent.py` (Stage 8) |
| **How** | Stage 4: last-author heuristic (senior authors appear last in most STEM fields). Stage 8: `seniority_score` composite (h_index 50% weight + cited_by_count 25% + works_count 15% + i10_index 10%) — junior researchers score near zero and sink to the bottom of the ranked list. |
| **What works** | Grad students rarely have h_index > 5 or works_count > 20; they will score very low and be cut at balancing. Last-author heuristic correctly promotes senior authors. |
| **What's missing** | ❌ No hard drop on low seniority — they're scored down, not removed · ❌ Fellowship/grant type not checked (MSCA-PD, NIH F31/F32, UKRI DTP are trainee awards, not PI grants) · ❌ Alphabetical-authorship fields (math/econ/CS theory) break last-author assumption — documented in `deferred_limitations` |
| **Recommended fix** | Add `works_count < 10 AND h_index < 3` as a hard drop gate after Stage 5 |
| **Review Comments** | |

---

### 5.3  Wrong-domain leakage from keyword overlap  (PDF §6.3)

| | |
|---|---|
| **PDF concern** | A grant titled "biodegradable plastic cartridges" leaks into biomaterials. "DNA barcoding" leaks into plant biology but is actually single-cell Hi-C chromatin work. Keyword overlap is not the same as domain match. |
| **Status** | ⚠️ Partial |
| **Where handled** | `subagents/author_disambiguator.py` (Stage 5) — at author level, not grant level |
| **How** | Author-level concept fingerprint check catches most wrong-domain *authors*. The student's area keywords must appear in the author's career x_concepts — this filters out researchers whose one matching paper is an outlier. |
| **What works** | Stops wrong-domain authors from being surfaced even if one of their papers matches a keyword. |
| **What's missing** | ❌ No grant-level domain check — a CORDIS grant with misleading title (§6.3 examples) can still be attached as evidence · ❌ No discipline classifier (humanities vs STEM vs medical) · ❌ No region/ecosystem disambiguation for geographic terms · ❌ Keyword synonym table is static and small — novel terms in the student's field won't match |
| **Recommended fix** | Run a second LLM check on attached grants: "Is this grant about {area}? Answer yes/no." Discard grants that fail. |
| **Review Comments** | |

---

### 5.4  Eligibility filters in free-text PhD ads  (PDF §6.4)

| | |
|---|---|
| **PDF concern** | Many PhD vacancies say "UK only", "home fees", "EU residents only". Surfacing an ineligible position to an Indian student is worse than not surfacing it. |
| **Status** | ❌ Not Started |
| **Where handled** | Nowhere — `linked_programs` is always `[]` in current output |
| **Why** | PhD vacancy scraping is not implemented. The `linked_programs` schema field exists and is wired, but no scraper runs. |
| **What's missing** | ❌ Per-institution PhD vacancy scraper · ❌ Eligibility extraction from ad text (citizenship, fee status, residency) · ❌ Student's citizenship / fee-status is not in the input schema |
| **Recommended fix** | Phase 1: Scrape `findaphd.com` / institution pages per PI. Phase 2: LLM-extract eligibility keywords. Phase 3: Compare against student's citizenship field (add to input schema). |
| **Review Comments** | |

---

## 6. Grading Dimensions — PDF §7

| Dimension | How Graded | Our Implementation | Status | Review Comments |
|-----------|-----------|-------------------|--------|-----------------|
| Mentor-eye audit | Domain mentor rates top 30 as bullseye/solid/stretch/wrong. Target: ≥60% bullseye+solid. | Stage 5 disambiguation + concept fingerprint reduces wrong picks. Stage 9 grounded blurbs help evaluators assess fit. | ⚠️ Depends on retrieval quality | |
| Contamination count | Wrong-domain / wrong-person / non-PI entries. Past systems: 5–20%. | Last-author heuristic + concept fingerprint + country gate together lower contamination. Seniority score sinks non-PIs. | ⚠️ Partial mitigation | |
| Coverage | ≥50 recs spread across stated areas | Per-area quota (Stage 1) + balancing (Stage 8) + `min_final=50` schema guard (fails loudly if breached) | ✅ Enforced | |
| Country adherence | 100% in target countries. Hard fail if breached. | Deterministic ISO normalisation + retrieval filter + Stage 6 re-verification + Pydantic schema guard | ✅ Enforced | |
| Latency | Wall-clock < 15 min per shortlist | Parallel retrieval (per area) + parallel disambiguation + parallel evidence + parallel why_match + 24h disk cache | ✅ Addressed | |
| Process quality | `DECISIONS.md` — trade-offs seen, chosen, justified | → See `DECISIONS.md` | ⚠️ Written — needs review | |

---

## 7. Required Deliverables — PDF §8

| # | Deliverable | File | Status | Notes | Review Comments |
|---|------------|------|--------|-------|-----------------|
| 1 | README.md | `README.md` | ⚠️ Partial | Exists but needs: data sources table, explicit trade-offs section, how-to-run example, known limitations | |
| 2 | End-to-end run | `run.py` | ✅ Done | `python run.py --profile data/inputs/stu_001.json --out data/outputs/stu_001.json` | |
| 3 | Sample output | `data/outputs/stu_001.json` | ✅ Exists | Generated from real pipeline run | |
| 4 | DECISIONS.md | `DECISIONS.md` | ✅ Done | 15 challenges covered (4 PDF + 8 additional + 3 deferred) with impact/approach/gaps per entry | |
| 5 | schema.md | `schema.md` | ✅ Done | Output JSON schema fully documented | |
| B | Bonus: Feedback loop | Not started | ❌ Not Started | Outcome CSV ingestion + shortlist improvement system | |

---

## 8. Known Deferred Limitations (documented in run_metadata)

| Limitation | Reason Deferred | Impact | Recommended Next Step |
|-----------|----------------|--------|----------------------|
| Alphabetical authorship (math/econ/CS theory) breaks last-author PI heuristic | Requires field-level classification of paper venue | Medium — these fields have fewer students in our target demo | Classify venue/journal by field; switch to first-author for math/econ |
| `linked_programs` always empty | PhD vacancy scraping not built | High for §6.4 eligibility filtering | Scrape findaphd.com + institution dept pages |
| Grant coverage limited to CORDIS (EU) | DFG/UKRI/NIH APIs not integrated | Medium — affects US/UK candidates | Add DFG GEPRIS, UKRI Gateway to Research, NIH RePORTER |
| Email hit rate ~20–40% | ORCID rarely exposes emails; homepage scrape is fragile | Low — null email is safe; hit rate reported in output | Build institutional email pattern inference (first.last@institution.edu) |
| No grant-level domain sanity check | LLM cost per grant not budgeted | Medium — §6.3 wrong-domain grants can appear in evidence | Add binary LLM gate: "Is this grant in domain {area}?" |
| No hard drop for career-stage (only scoring down) | Threshold not calibrated | Medium — may surface postdocs with high h_index | Add `works_count < 10 AND h_index < 3` hard gate in Stage 5 |
| No eligibility extraction from PhD ad text | §6.4 scraping not built | High for non-EU students | Phase 2 feature |

---

## 9. Code Health Checks

| Check | Status | Detail |
|-------|--------|--------|
| `normalise_countries` ImportError (was in `run.py`) | ✅ Fixed | Now a proper exported function in `agents/profile_agent.py` with ISO override table |
| Unused `model`/`max_tokens` params in `enrich_profile` | ✅ Fixed | Parameters removed; LLM client carries config |
| Country normalisation only `.upper()` (UK≠GB) | ✅ Fixed | `_ISO_OVERRIDES` dict maps UK→GB, USA→US etc. |
| Smoke tests (`test_schema.py`) | ✅ Passing | All 5 pass including `normalise_countries(["UK","de","NL"]) == ["GB","DE","NL"]` |
| Logging | ✅ Done | Date-based `logs/YYYY-MM-DD/pipeline.log` + `errors.log` |
| Old `src/` module | ✅ Removed | All code now in `agents/`, `subagents/`, `graph/`, `shared/` |
| Circular imports | ✅ Clean | `shared/schema.py` validator uses only `.upper()` — full ISO mapping in profile_agent |
| Thread safety | ✅ Adequate | Each parallel task works on its own dict copy; no shared mutable state |
