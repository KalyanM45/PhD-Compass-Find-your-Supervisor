# DECISIONS.md — Data Quality Challenges, Analysis & Trade-offs

> Required deliverable per PDF §8.4.
> For each challenge: **what it is · how it affects the pipeline · impact · our approach · remaining risk**.

---

## Format used throughout this document

Each entry follows this structure:

| Field | Meaning |
|-------|---------|
| **What it is** | Clear definition of the problem |
| **How it enters the pipeline** | Which stage is vulnerable and what breaks |
| **Impact** | Severity (High / Medium / Low) and why |
| **Our approach** | What we implemented, with file references |
| **Concrete example** | Real or realistic scenario from this system |
| **Remaining risk / gaps** | What is not yet covered and recommended fix |

---

---

# Part A — Challenges from PDF §6 (Required)

---

## A-1 · Same-name-different-person collisions (PDF §6.1)

**What it is**  
Common names — "Wei Wang", "Sharma", "Yang Shi", "Rong Zheng" — are shared by dozens or
hundreds of unrelated researchers. OpenAlex links papers to author IDs, but their disambiguation
algorithm is imperfect. A paper in machine learning authored by one "Wei Wang" can be indexed
under the same author record as a "Wei Wang" who is a civil engineer.

**How it enters the pipeline**  
Stage 3 fetches papers by keyword + country filter. The paper's authorship list contains an
OpenAlex author ID. Stage 4 uses that ID to build the candidate. If the ID belongs to the wrong
"Wei Wang", every enrichment (ORCID, concept fingerprint, seniority metrics) belongs to the
wrong person. The wrong-person entry then produces a why_match blurb referencing a paper they
didn't actually write — the most embarrassing output possible.

**Impact: HIGH**  
A student cold-emailing a chemistry professor about graph neural networks is a credibility-
destroying mistake that cannot be undone.

**Our approach**  
`subagents/author_disambiguator.py` — Stage 5 (GATE 1):

Fetches the OpenAlex `/authors/{id}` record and reads `x_concepts` — a ranked list of topics
covering the author's *entire body of work*, not just the one matching paper. Drop logic:

```
IF  x_concepts is non-empty          ← author is well-indexed
AND keyword overlap(x_concepts, student_area_keywords) == 0
THEN drop                            ← clear field mismatch; not the right person
ELSE keep                            ← either overlaps (good) or unverifiable (keep; not wrong)
```

Keyword matching is per-word substring: each word from the student's area and query_hint is
checked against each concept name.

**Concrete example**  
stu_001 area: "geometric deep learning". A chemistry professor "Wei Wang" co-authors a paper on
"molecular graphs". Her `x_concepts` = {Organic Chemistry 0.9, Catalysis 0.7, Polymer Science 0.6}.
None of these contain "deep", "learning", "geometric", "neural", "graph" (as a CS concept) →
zero overlap → dropped at Stage 5.

**Remaining risk / gaps**  
- Concept match is keyword substring, not semantic — a researcher publishing on "geometric
  morphometrics" (biology) shares the word "geometric" with "geometric deep learning" → false keep.
- OpenAlex `x_concepts` for newly hired PIs may be sparse → kept on "unverifiable" logic → potential
  false keep.
- No ORCID affiliation cross-check implemented (would confirm institution matches claimed country
  and field).
- No co-author triangulation (papers with shared co-authors from the right lab would confirm identity).

**Recommended fix**  
Add a secondary check: if `x_concepts` score for the most relevant concept < 0.3, flag as
low-confidence. Consider a soft score penalty rather than binary keep/drop.

---

## A-2 · Career-stage errors — grad students and postdocs mistaken for PIs (PDF §6.2)

**What it is**  
PhD students and postdocs appear as first authors of real, citable, recent papers in OpenAlex.
They have OpenAlex author IDs, ORCID records, and sometimes grants (personal fellowships: MSCA-PD,
NIH F31/F32, UKRI DTP studentships). None of them can supervise a PhD student. Treating any name
in an author list as a PI candidate produces a shortlist full of 24-year-old grad students.

**How it enters the pipeline**  
Stage 4 extracts all authors at target-country institutions. A first-year PhD student who is first
author on a Nature-indexed paper is extracted as a candidate. At Stage 5, their `x_concepts`
aligns with the student's area (it's their field too) → disambiguation passes. At Stage 7, their
first-author paper becomes evidence. At Stage 9, a compelling why_match blurb is written.
The final output recommends a PhD student to another PhD student.

**Impact: HIGH**  
Not only useless — actively harmful. The student may email the grad student asking to join their
"lab", creating an awkward situation for both parties.

**Our approach**  
Two complementary signals:

*Signal 1 — last-author heuristic* (`agents/candidate_agent.py`, Stage 4):  
In most STEM fields, the PI appears last. We set `likely_pi = True` for last-author position and
for any author in ≤2-author papers. This flag feeds into scoring.

*Signal 2 — seniority composite score* (`agents/scoring_agent.py`, Stage 8):  
```
seniority_score = 0.50 × min(1, h_index / 60)
               + 0.25 × min(1, cited_by_count / 10,000)
               + 0.15 × min(1, works_count / 200)
               + 0.10 × min(1, i10_index / 100)
```
A PhD student (h=1, 8 citations, 2 papers) → seniority ≈ 0.008  
A postdoc (h=5, 300 citations, 15 papers) → seniority ≈ 0.058  
An associate professor (h=25, 2500 citations, 80 papers) → seniority ≈ 0.42  
A full professor (h=50, 8000 citations, 180 papers) → seniority ≈ 0.86  

Junior researchers score so low they fall below the per-area quota cut in Stage 8.

**Concrete example**  
A 3rd-year PhD student: h_index=2, cited_by_count=45, works_count=4, i10_index=0.  
seniority = 0.50×(2/60) + 0.25×(45/10000) + 0.15×(4/200) + 0.10×0  
          = 0.017 + 0.001 + 0.003 + 0 = 0.021  
Combined match_score with perfect topic overlap and recent paper: ≈ 0.50×1 + 0.2×0.9 + 0.2×0.2 + 0.1×0.021 ≈ 0.72  
Still makes "reach" tier! This is the known gap — seniority weight (10%) is too small to reliably
demote very topic-relevant junior researchers.

**Remaining risk / gaps**  
- Seniority is a signal, not a gate. A highly-cited postdoc (h=8) in a hot area scores high enough
  to survive. No hard drop on low seniority.
- Last-author heuristic fails for alphabetical-authorship fields (math, economics, CS theory).
  Documented in `run_metadata.deferred_limitations`.
- Personal fellowship grants (MSCA-PD, UKRI studentships) are not filtered — if CORDIS returns one
  attached to a postdoc, it passes through as "evidence".
- No check on author's listed role on ORCID (e.g., "PhD Candidate" vs "Principal Investigator").

**Recommended fix**  
Add a hard gate after Stage 5: drop if `works_count < 8 AND h_index < 4`. Separately, add
grant-type filtering in Stage 7: discard grants whose `fundingScheme` matches known trainee award
codes (MSCA-PF, IF, etc.).

---

## A-3 · Wrong-domain leakage from keyword overlap (PDF §6.3)

**What it is**  
A keyword that is correct in one domain has a completely different meaning in another. PDF §6.3
examples: "biodegradable plastic cartridges" (ammunition, not biomaterials), "trauma-informed"
(Roman literary history, not clinical psychology), "DNA barcoding" (single-cell Hi-C chromatin,
not plant phylogenetics). The pipeline uses keyword search — surface text match does not equal
domain match.

**How it enters the pipeline**  
Stage 3 (retrieval) uses a search query against OpenAlex full-text. If a paper in a wrong domain
contains the student's keywords in its abstract, it is fetched. The PI of that paper then enters
the candidate pool. If the PI's x_concepts happen to share a superficial keyword with the student's
area, they pass Stage 5 disambiguation too. Their paper then becomes evidence in Stage 7.

Two specific attack vectors:
1. **Paper level** — a wrong-domain paper matches the query text.
2. **Grant level** — a CORDIS grant with a misleading title is attached as evidence via Stage 7.

**Impact: MEDIUM-HIGH**  
A student interested in biomaterials receiving a recommendation for a military ammunition
researcher is not just useless — it signals a fundamentally broken system to the user.

**Our approach**  

*Gate 1 — grounded LLM query terms* (Stage 1, `agents/profile_agent.py`):  
Instead of free-text keywords, we ask the LLM to output concrete OpenAlex concept terms
(`"equivariant graph neural network molecular"` rather than `"AI"`). Highly specific queries
produce far less semantic bleed than broad ones.

*Gate 2 — author career fingerprint* (Stage 5, `subagents/author_disambiguator.py`):  
Even if a wrong-domain paper matches a query keyword, the author's career x_concepts must
overlap with the student's area. A military researcher who wrote one paper using "neural"
will have x_concepts in Defence Science, Ballistics, Materials Engineering → zero overlap
with "deep learning" → dropped.

*Gate 3 — query synonym table scope* (`subagents/query_expander.py`):  
Synonyms are added only for known canonical terms, not arbitrary expansions. This limits how
broadly queries can drift.

**Concrete example**  
Student area: "geometric deep learning". A researcher in architectural geometry publishes a paper
on "geometric learning from point clouds" (computer graphics, not ML). Their x_concepts include:
Computer Graphics (0.8), 3D Modelling (0.7), Computational Geometry (0.6).  
Keywords to check: {"deep", "learning", "geometric", "neural", "equivariant"}.  
"geometric" matches "Computational Geometry" → overlap = 1 → Stage 5 keeps this candidate.  
This is a false keep — a real leak. The concept match is too coarse.

**Remaining risk / gaps**  
- Concept overlap is a substring match — "geometric" in "Computational Geometry" falsely overlaps
  with "geometric deep learning". Needs semantic similarity (embeddings) rather than string match.
- Grant-level domain check not implemented. A CORDIS grant with a misleading title passes
  through Stage 7 unchecked.
- The synonym table is static and small (6 entries) — novel terminology in emerging fields
  produces unexpected matches.

**Recommended fix**  
Add a binary LLM gate on each fetched grant: "Is this grant about {student_area}? Reply only
yes or no." Discard "no" grants. For the author fingerprint, replace substring match with
cosine similarity over SPECTER embeddings.

---

## A-4 · Eligibility filters in free-text PhD vacancy ads (PDF §6.4)

**What it is**  
Many PhD positions have citizenship or fee-status restrictions buried in plain text: "UK residents
only", "home fees only", "EU nationals eligible", "must have right to work in Germany". An Indian
student applying to a "UK home fees only" position wastes their outreach and risks damaging their
reputation with that PI for future applications.

**How it enters the pipeline**  
Currently: it does not enter the pipeline at all, because vacancy scraping is not implemented.
`linked_programs` in every output record is `[]`. This means §6.4 is not a live contamination
risk today, but the moment vacancy scraping is added (Phase 2), it becomes the highest-severity
risk: an automated system sending targeted cold-emails to ineligible positions at scale.

**Impact: HIGH (latent)**  
Today: no impact (no vacancy data). Post Phase 2: systematic mis-targeting of ineligible positions
at scale is the worst possible user experience for the product.

**Our approach**  
Currently deferred. Mitigation plan documented:

Phase 1 (input schema): Add `citizenship` and `fee_status` fields to `StudentProfile`
(e.g., `"citizenship": "IN"`, `"fee_status": "overseas"`).

Phase 2 (scraping): Add per-PI vacancy scraper calling findaphd.com and institution pages.

Phase 3 (extraction): LLM prompt to extract eligibility from ad text:
```
Given this PhD ad text, extract:
- citizenship_required: list of ISO country codes, or "any"
- fee_status: "home" | "EU" | "overseas" | "any"
Return JSON only.
```

Phase 4 (filter): Before adding to `linked_programs`, compare extracted eligibility against
student's `citizenship` and `fee_status`. Drop ineligible positions entirely (never surface them).

**Concrete example**  
Student: Indian citizen, overseas fee status. Ad says "UK/EU nationals only (home fees)".
Extraction: `{"citizenship_required": ["GB", "EU"], "fee_status": "home"}`.
Student's citizenship "IN" is not in `["GB", "EU"]` → position is suppressed.

**Remaining risk / gaps**  
- "EU nationals" after Brexit is ambiguous and inconsistently stated in UK ads.
- Some eligibility clauses reference funding body rules, not the institution's own rules —
  a Leverhulme Trust grant may say "UK/EU only" but this applies to the student stipend funding,
  not the PI's ability to supervise.
- Natural language in ads is inconsistent ("eligible for home fees", "must be settled status",
  "UKRI-funded studentship open to UK nationals") — coverage of LLM extraction will be <100%.

---

---

# Part B — Additional Challenges We Identified

---

## B-1 · LLM enrichment failure modes

**What it is**  
Stage 1 sends the student's full profile to Groq and expects structured JSON back. Three distinct
failure modes exist: (a) LLM returns non-JSON (markdown fence, apology, truncated output),
(b) LLM returns valid JSON but with hallucinated OpenAlex concept names that match no real papers,
(c) LLM misinterprets the student's area (e.g., confuses "drug delivery" with "drug discovery",
or maps a niche topic to an overly broad parent concept).

**How it enters the pipeline**  
Failure (a): Handled by the JSON parse try/except in `agents/profile_agent.py`. Fallback uses
raw `profile.skills` and `profile.research_interests` — crude but safe.  
Failure (b): The hallucinated concept name produces a query that returns zero or wrong papers
from OpenAlex. Silent failure — the area may return 0 results and be omitted from coverage.  
Failure (c): All downstream stages run on the wrong framing. "Drug discovery" → "pharmaceutical
synthesis" maps the student to wet-lab chemists instead of computational ML researchers.

**Impact: HIGH for (b) and (c), LOW for (a)**  
Failure (b) silently drops an area from coverage. Failure (c) produces a subtly wrong shortlist
that looks plausible but contains systematically wrong PIs — the hardest contamination to detect.

**Our approach**  
- JSON fallback (`agents/profile_agent.py`, lines 51–69): on parse failure, falls back to raw
  profile data. Logged as a warning.
- The prompt is highly structured with numbered fields and `Return only valid JSON, no markdown
  fences` instruction.
- `normalised_countries` in the LLM output is cross-checked against the deterministic ISO override
  table as a secondary normalisation.

**Concrete example**  
If the LLM returns `"openalex_concepts": [{"name": "Graph Learning", "query_hint": "graph learning"}]`
instead of the more specific "equivariant neural network molecular", the OpenAlex query returns
papers from network science, social graph analysis, and biology — all wrong domains for stu_001.

**Remaining risk / gaps**  
- No validation that LLM-returned concept names exist in OpenAlex.
- No retry on LLM failure with a simpler prompt.
- Failure (c) is undetectable without a post-hoc domain audit of the retrieved papers.

**Recommended fix**  
After Stage 3 retrieval, count papers returned per area. If any area returns < 5 papers, log a
warning and optionally re-run Stage 1 for that area with a broader query hint.

---

## B-2 · OpenAlex indexing gaps and author record quality

**What it is**  
OpenAlex indexes approximately 250 million works but coverage is uneven. Researchers at smaller
institutions, non-English-language publishers, or in humanities/social sciences are systematically
under-indexed. Additionally, an author may have published under multiple name variants
(with/without middle initial, with/without accent marks, name change after marriage) — creating
split author records in OpenAlex, each with incomplete x_concepts.

**How it enters the pipeline**  
A PI who published in German journals with an umlaut in their name (Müller vs Mueller) may have
two OpenAlex author records — neither with sufficient x_concepts to pass disambiguation. They are
silently dropped at Stage 5. A genuine, highly relevant PI is missed.

**Impact: MEDIUM**  
Reduces recall without contaminating precision. The shortlist is shorter or thinner in coverage,
but no wrong PIs are added. This is the acceptable failure mode.

**Our approach**  
`subagents/author_disambiguator.py`: If `x_concepts` is empty, the candidate is kept (not
dropped). This handles the case where a PI is under-indexed — we cannot verify them, but we
also cannot rule them out.

`shared/clients.py` `OpenAlexClient`: The `User-Agent` header includes a `mailto` for the
polite pool, which gives access to more complete metadata than the anonymous tier.

**Concrete example**  
Prof. Müller publishes as "B. Müller" in German journals and "Barbara Mueller" in English ones.
OpenAlex creates two records. Each has sparse x_concepts (20 papers each, threshold not reached).
Both are kept at Stage 5 (unverifiable logic). This is correct behaviour: we surface the PI
despite indexing fragmentation.

**Remaining risk / gaps**  
- The same PI may appear twice in the output with different openalex_author_ids.
- No cross-record name similarity deduplication.

**Recommended fix**  
Post-Stage 6: run a name+institution deduplication pass. If two candidates share the same
last name + institution + country, keep only the one with higher seniority score.

---

## B-3 · Retrieval concentration bias — famous PIs dominate results

**What it is**  
OpenAlex ranks search results by relevance, which correlates with citation count. The most-cited
papers in any field appear first. Their authors are the most famous PIs in that area — typically
at elite institutions (MIT, ETH Zürich, Oxford). These PIs have large groups, are highly selective,
and represent "reach" tier at best for most students. Less famous but equally relevant PIs at solid
research universities are systematically under-fetched.

**How it enters the pipeline**  
Stage 3 fetches up to 300 papers per area but the first pages of OpenAlex results are
citation-sorted. Less-cited (but recency-strong) papers appear on later pages that may not be
fetched if the page limit is hit. Stage 8 scoring includes `seniority_score` with full weight
on h_index — this further promotes the already-overrepresented famous PIs.

**Impact: MEDIUM**  
The shortlist is skewed toward "reach" tier entries. Coverage exists (≥50 recs) but the tier
distribution is imbalanced — not enough "target" and "safety" recommendations for realistic
application planning.

**Our approach**  
- Per-area quota in Stage 8 (`balance_and_select`) caps any area at 50% of total, preventing
  one area from being filled entirely with one type of PI.
- `recency_score` in the match formula (weight 0.2) boosts PIs with very recent papers — these
  are often newer, less famous researchers who are actively building their group.
- `max_per_area_fraction = 0.5` ensures diversity across areas.

**Concrete example**  
For "geometric deep learning", Michael Bronstein, Stephan Günnemann, and Max Welling dominate
the first OpenAlex page. All three are full professors at elite institutions with h_index > 50.
They all receive `match_score > 0.85` → "reach" tier. A solid associate professor at TU Berlin
with h_index 18 and a 2024 paper appears on page 2 → still fetched → scores 0.61 → "target".
The per-area cap prevents the top 3 from filling the entire quota.

**Remaining risk / gaps**  
- If `max_works_per_area` is too small (< 100), later-page results are never fetched.
- No explicit tier-balancing (e.g., guarantee at least 15% safety, 30% target, 55% reach).

**Recommended fix**  
Add post-balancing tier distribution check: if <10 "target" or <5 "safety" entries exist,
fetch an extra page from OpenAlex specifically sorted by `publication_date` (recent-first)
rather than relevance (citation-first).

---

## B-4 · API rate limits and reliability

**What it is**  
The pipeline makes hundreds of API calls: one per area (OpenAlex works), one per candidate
(OpenAlex authors), one per candidate (OpenAlex recent works for country), one per candidate
(CORDIS grants), and one per PI (Groq LLM). These are made concurrently via ThreadPoolExecutor.
Concurrent calls against a single API can exceed its rate limit or trigger a temporary ban.

**How it enters the pipeline**  
OpenAlex polite pool: allows ~10 requests/second. With 10 concurrent threads each making author
lookups, burst rate can exceed this. CORDIS has no documented rate limit but will return HTTP 429
or silent failures. Groq has a tokens-per-minute limit that 10 concurrent why_match calls can hit.

**Impact: MEDIUM**  
Rate-limit errors produce empty results (candidates dropped silently) or failed why_match
blurbs (deterministic fallback used). The pipeline continues but with reduced coverage and
lower-quality blurbs.

**Our approach**  
- `tenacity` retry decorator on `OpenAlexClient._get`: 3 attempts, exponential backoff
  (2s → 4s → 8s) — handles transient 429s and 5xx errors.
- `time.sleep(0.1)` between cursor pages within a single area fetch (polite pool courtesy).
- CORDIS grant fetcher: 2 retries with 2–8s backoff.
- `parallelism_limit` in config (default: 10) caps concurrent threads across all parallel stages.
- 24h SHA-256 disk cache: after first run, all OpenAlex calls hit disk — zero API calls on re-run.

**Concrete example**  
50 candidates in area "geometric deep learning" → 50 concurrent `get_author` calls at Stage 5.
With `parallelism_limit=10`, only 10 fire simultaneously. OpenAlex returns HTTP 429 on thread 3 →
tenacity waits 2s, retries → succeeds. Thread 3 candidate survives; pipeline continues normally.

**Remaining risk / gaps**  
- Groq tokens-per-minute limit is not explicitly managed — 10 concurrent 200-token calls at
  Stage 9 could exhaust the per-minute quota mid-pipeline.
- `time.sleep(0.1)` between pages applies only within a single-area fetch, not across all
  concurrent area fetches happening simultaneously.

**Recommended fix**  
Add a `RateLimiter` semaphore per API host (OpenAlex, CORDIS, Groq) with configurable max
requests/second. Wrap each client call behind the semaphore rather than relying solely on
per-thread retry.

---

## B-5 · Grant-name collisions (same-name problem applied to grants)

**What it is**  
CORDIS searches grants by PI name string. If a PI's name is common ("Zhang Wei", "Thomas Schmidt"),
the grant search returns grants from multiple researchers with the same name. Grant evidence from
the wrong "Thomas Schmidt" (a materials engineer) is attached to a computer scientist named
"Thomas Schmidt".

**How it enters the pipeline**  
Stage 7 (`subagents/grant_fetcher.py`) sends the PI's name as a free-text query to CORDIS. CORDIS
returns top 5 projects matching that string. If the first result belongs to a different person
with the same name, it is attached as evidence with no verification.

**Impact: MEDIUM**  
Produces a false evidence chain: the why_match blurb references a grant the PI did not hold.
This is not as dangerous as wrong-person contamination but actively misleads the student about
the PI's current funding.

**Our approach**  
Currently: CORDIS results are taken as-is. No cross-reference against the PI's institution or
ORCID. This is a known limitation, documented in `run_metadata.deferred_limitations`.

**Concrete example**  
PI: "Thomas Müller" at TU Munich (machine learning). CORDIS query returns:
1. "Thomas Müller" at University of Freiburg — environmental science grant.
2. "Thomas Müller" at TU Munich — computational biology grant (different department).
Both are attached as evidence. Neither is the ML researcher's grant.

**Remaining risk / gaps**  
- No institution cross-check on CORDIS results.
- No field/domain cross-check on grant title vs PI's known x_concepts.

**Recommended fix**  
After fetching CORDIS results, apply a lightweight institution match: discard grants where the
CORDIS project partner institution does not fuzzy-match the PI's OpenAlex institution name.
Also apply the domain gate described in A-3: LLM binary check on grant relevance.

---

## B-6 · Recency floor as a flawed "still active" proxy

**What it is**  
We use `publication_year >= target_intake.year - 5` as a proxy for "PI is currently active and
can take a student for Fall 2026." This is an approximation. A PI can have published in 2024 but
retired. A PI can have their last paper in 2019 but just opened a new lab after industry.
A PI who is on sabbatical may have no 2024 papers but is actively recruiting.

**How it enters the pipeline**  
Stage 3 filters at the paper level: only papers after `recency_floor` are fetched. Stage 8
`recency_score` further penalises PIs whose most recent paper is older. A PI who published last
in 2021 scores `recency = max(0, 1 - (2026-2021)/10) = 0.5` — reduced but not zero.

**Impact: LOW-MEDIUM**  
We may miss some recently-returned PIs, and we may include some recently-retired ones. The
seniority score partially compensates (retired PIs may have declining recent citation counts),
but it is an imperfect signal.

**Our approach**  
- 5-year recency window is configurable (`recency_years` in `config.yaml`) — a tighter window
  catches more stale PIs but reduces recall.
- `recency_score` in Stage 8 is continuous (not binary), so a PI with papers from 2022–2023 is
  still represented, just with a lower recency component.

**Concrete example**  
PI took a 3-year industry leave (2020–2023) and returned to academia in 2023. Their most recent
paper is from 2024. Recency score = max(0, 1 - 2/10) = 0.8 — correctly high.
A PI who retired in 2023 but has 2023 papers: recency = 0.7, still high. Seniority ≈ 0.9 (full
career). They may surface. No way to detect retirement from paper metadata alone.

**Remaining risk / gaps**  
- No faculty webpage status check ("accepting students" text).
- No lab website activity signal (last blog post, recent student roster).

**Recommended fix**  
Phase 2: Scrape the PI's lab homepage (already fetched for email resolution) and check for
"currently accepting PhD students" or similar phrases. Use LLM extraction.

---

## B-7 · Institutional ambiguity and dual affiliations

**What it is**  
Some PIs hold joint appointments at institutions in different countries (ETH Zürich + MPI Germany,
MIT + KAUST Saudi Arabia). A paper may list both affiliations. The Stage 3 retrieval filter
(`institutions.country_code:DE|NL|CH`) passes papers where any author's institution is in a
target country — even if the paper's first author is at a US institution.

Additionally, some institutions are in unexpected countries: MIT Abu Dhabi is in UAE, not US.
KAUST is in Saudi Arabia. African Institute for Mathematical Sciences (AIMS) campuses are spread
across five countries. OpenAlex may assign the parent-institution country rather than the
branch country.

**How it enters the pipeline**  
A US-based PI who holds a joint appointment at TU Munich appears in Stage 3 results (their DE
affiliation passes the filter). At Stage 4, their US-based authorship is extracted too —
`country = "US"` — which fails the country check at Stage 4. But if the paper lists their DE
affiliation for that specific paper, `country = "DE"` → they enter the candidate pool correctly.

Stage 6 then fetches their last 20 works. If most recent works are from their US post, majority
vote confirms "US" → correctly dropped.

**Impact: LOW-MEDIUM**  
Stage 6 catches most dual-affiliation false positives. The remaining risk is a PI who recently
moved from DE to US but has mostly old DE papers.

**Our approach**  
Stage 6 (`subagents/country_verifier.py`): majority vote over recent works (last 20 papers,
publications since `recency_floor`). If >50% of author appearances in those works show a target
country, they are confirmed. A recently-moved PI's recent works will show US → correctly dropped.

**Remaining risk / gaps**  
- The 20-paper sample is small. A PI who publishes infrequently (2 papers in 5 years) may not
  give a reliable majority.
- Joint-appointment PIs who legitimately operate in both countries (running labs in DE and US)
  are dropped when they should be kept for DE-targeting students.

---

## B-8 · Thread safety in parallel execution

**What it is**  
Stages 5, 6, 7, and 9 fan out per-candidate tasks using `ThreadPoolExecutor`. Each task receives
a reference to the candidate dict. If any task mutates a shared data structure (e.g., a list
being iterated by the orchestrator) or if the same candidate dict is accessed concurrently by
two threads, silent data corruption can occur.

**How it enters the pipeline**  
Python's GIL protects basic dict reads/writes at the bytecode level, but compound operations
(read-modify-write sequences, sorting, extending lists) are not atomic. If two threads write
to the same candidate dict simultaneously (e.g., both setting `cand["orcid"]`), the last write
wins — which may be the wrong value.

**Impact: LOW in practice**  
Current architecture: each task receives a separate dict (we do `dict(candidate)` in disambiguator
and country verifier — shallow copies). Writes go to the copy, not the original. The risk is low
but not zero.

**Our approach**  
`subagents/author_disambiguator.py`: `cand = dict(candidate)` creates a shallow copy before
mutation → original dict is not touched by the thread.  
`subagents/country_verifier.py`: same pattern — `cand = dict(candidate)`.  
All futures are collected via `as_completed` and results aggregated sequentially by the calling
agent — no shared mutable structure during the concurrent execution window.

**Remaining risk / gaps**  
- Shallow copy: if `candidate["papers"]` is mutated (`.append`), the original list is modified
  in place because a shallow copy shares nested objects.
- `deduplicate_across_areas` in Stage 4 mutates `global_seen[aid]["papers"]` — this runs
  before the parallel stages, so it's safe, but the pattern is fragile.

**Recommended fix**  
Use `copy.deepcopy(candidate)` instead of `dict(candidate)` in all subagents that mutate the
candidate. This eliminates the shared-reference risk entirely at a small memory cost.

---

---

# Part C — Challenges Not Addressed (Deferred)

| Challenge | Why Not Addressed | Impact | Phase |
|-----------|------------------|--------|-------|
| §6.4 — PhD vacancy eligibility filtering | Requires scraper + LLM extraction + citizenship field in input schema. Full feature, not a fix. | High (when vacancy scraping is added) | Phase 2 |
| DFG GEPRIS (Germany) / UKRI Gateway (UK) / NIH RePORTER (US) grants | Each requires a separate API client. CORDIS covers EU/CH target in stu_001. | Medium | Phase 2 |
| Alphabetical-authorship field detection | Requires venue/journal classifier. Documented in `deferred_limitations`. | Medium for math/econ | Phase 2 |
| ORCID affiliation cross-check | Extra API call per candidate; ORCID employment history could confirm field + country. | Medium | Phase 2 |
| Co-author triangulation for disambiguation | Confirming shared co-authors with known PIs in the student's area would sharpen §A-1. | Medium | Phase 2 |
| Semantic concept matching (embeddings) | Replace substring keyword overlap with SPECTER cosine similarity. Fixes §A-3 false keeps. | High for precision | Phase 2 |
| Institutional email pattern inference | "first.last@institution.edu" pattern when ORCID + scrape fail. Improves email hit rate. | Low | Phase 2 |
| Feedback loop / outcome stream (PDF §10) | Bonus feature. Outcome CSV → reward signal → query reweighting. | N/A (improvement system) | Phase 3 |
| Grant-type filter (trainee awards) | Requires CORDIS `fundingScheme` classification. | Medium | Phase 2 |
| Retired / sabbatical PI detection | Lab website activity signal not yet scraped. | Low-Medium | Phase 3 |

---

# Summary — Challenge Coverage Matrix

| Challenge | In Scope (PDF) | Status | Our Layers |
|-----------|---------------|--------|-----------|
| Same-name collisions (§6.1) | ✅ Required | ⚠️ Partial | x_concepts fingerprint at Stage 5 |
| Career-stage errors (§6.2) | ✅ Required | ⚠️ Partial | Last-author heuristic + seniority score |
| Wrong-domain leakage (§6.3) | ✅ Required | ⚠️ Partial | Grounded LLM queries + concept fingerprint |
| Eligibility filters (§6.4) | ✅ Required | ❌ Deferred | Documented with Phase 2 plan |
| LLM enrichment failures | Identified by us | ⚠️ Partial | JSON fallback; no retry |
| OpenAlex indexing gaps | Identified by us | ✅ Handled | Keep-on-unverifiable logic |
| Retrieval concentration bias | Identified by us | ⚠️ Partial | Per-area quota + recency score |
| API rate limits | Identified by us | ✅ Handled | Tenacity retry + disk cache + parallelism cap |
| Grant-name collisions | Identified by us | ❌ Deferred | No institution cross-check yet |
| Recency as "active" proxy | Identified by us | ⚠️ Partial | Configurable window; no webpage scrape |
| Dual affiliations | Identified by us | ✅ Handled | Stage 6 majority-vote current country |
| Thread safety | Identified by us | ✅ Handled | Shallow copy per thread; aggregation is sequential |
| Country adherence | ✅ Requirement | ✅ Done | 4 independent layers + Pydantic guard |
| Email fabrication | ✅ Requirement | ✅ Done | Always null, never guessed |
| Latency | ✅ Requirement | ✅ Done | Parallel stages + 24h cache |
