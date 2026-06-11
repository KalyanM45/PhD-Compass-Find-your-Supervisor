## Multi Agentic Workflow Overview

Step-1: User Input
- The user provides an initial input or query to the system.
- The Input Schema Refer [Input Schema](schema.md) which contains the following:
        - Student ID
        - Education, Skills, Projects, Publications
        - Research Interests
        - UseCases [Additionally Added]
        - Target Countries
        - Target Intake
        - Intro Call Summary
        - Raw Resume Text
        - Citizenship

Step-2: OpenAlex Concepts Finalization
- Based on the Profile Summary, We will finalize the following parameters for OpenAlex Search
    - Domain
    - Sub-Domain
    - Topic
    - Sub Topic
- We also normalise the target countries to standard ISO codes here (e.g. "UK" → "GB")

Step-3: Research Paper Retrieval
- Once the OpenAlex Concepts are finalized, We will use them to query the OpenAlex API to get the list of research papers that match the student's research interests.
- We will retrieve the papers sorted based on citation count from high to low, top 200 papers per area.
- The higher the citation count, the more popular and impactful the paper is in the research field.
- We also filter papers by the student's target countries and a recency window (last 5 years).

Step-4: Author Extraction
- Once the research papers are retrieved from the OpenAlex API, We will extract all the authors and their details from each paper.
- For every author we check if they are affiliated with one of the student's target countries.
- We also apply a simple PI heuristic — if a paper has more than 2 authors, the last author is most likely the PI/supervisor.
- Same author appearing across multiple papers is merged into one entry.

Step-5: PI Identification & Filtering
- Now we need to identify who among the extracted authors is an actual PI (faculty supervisor who can offer a PhD position).
- We do this in two stages:

    Stage 5A — Disambiguation:
    - We fetch the full author profile from OpenAlex and run it through 5 checks:
        1. Keyword overlap — does the author's research topics match the student's area?
        2. LLM check — if keyword overlap is unclear, we ask the LLM to decide
        3. Institution type — drop if the author is at a company or archive (can't supervise)
        4. Bibliometrics — drop if works count and h-index are too low (likely a PhD student/postdoc)
        5. ORCID employment — check if their ORCID profile confirms a faculty/professor role
    - If an author fails any of these checks, they are dropped from the list.

    Stage 5B — Country Verification:
    - We double-check the author's current country by looking at their recent publications.
    - If the majority of their recent papers show a different country than the student's target — we drop them.

Step-6: Evidence Attachment
- For each PI that survives, We attach supporting evidence:
    - Top 1-3 recent papers from their work (most cited + most recent)
    - Active research grants from CORDIS (EU grants database)
    - Contact email — resolved from ORCID or their homepage (never fabricated, null if not found)
    - Linked PhD programs at their institution (via lookup table, FindAPhD, or PhDScanner)
- For each PhD position we find, We now also check if the student is eligible to apply.
    - We first do a quick keyword check on the position description (e.g. "UK only", "home fees")
    - If the result is unclear, We send the full description to the LLM and ask if a student with the given citizenship can apply
    - Positions where the student is not eligible are removed before reaching the output
- We also check if any of the PI's papers match the student's use cases and flag those with a relevance note.
- If a PI has zero papers and zero grants after this step, they are dropped.

Step-7: Scoring & Tiering
- We score every PI candidate with a match_score between 0 and 1 based on:
    - How well their research topics match the student's area (topic similarity)
    - How recent their publications are (recency)
    - How strong their evidence is — paper count + citation weight (evidence strength)
    - How senior they are — h-index, works count, citations (seniority)
- Based on the score, each PI is assigned a tier:
    - Reach — score ≥ 0.75 (excellent match, student may need to stretch)
    - Target — score ≥ 0.50 (good match, realistic application)
    - Safety — score < 0.50 (weaker match, but likely to respond)
- We also balance the final list so no single research area dominates, and remove duplicates.
- Final output is a ranked list of at least 50 PI recommendations.

Step-8: Why-Match Blurb Generation
- For each PI in the final list, We generate a personalised one-sentence explanation of why this PI is a good match for the student.
- This is done using an LLM call with the PI's papers/grants and the student's skills/projects as input.
- The LLM is told to always mention a specific paper or grant and a specific student skill — no generic phrases.
- All blurbs are generated in parallel to keep the pipeline fast.
- If the LLM fails or times out for a candidate, a fallback sentence is used that references the top paper title directly.

Step-9: Output
- All the final candidates are assembled and validated against the output schema (Refer [Output Schema](schema.md)).
- Hard rules are enforced — if fewer than 50 recommendations are produced, the run fails loudly.
- The final shortlist is written to `data/outputs/{student_id}_{timestamp}.json`
- The output also includes a coverage summary (how many recommendations per research area) and run metadata (total count, time taken, email hit rate).
