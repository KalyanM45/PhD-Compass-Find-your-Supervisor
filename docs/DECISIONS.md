### Solved

1. Same name, different person — "Wei Wang", "Sharma", "Yang Shi" are shared by dozens of unrelated researchers. A paper in ML by one Wei Wang can be indexed under a chemistry Wei Wang in OpenAlex. Paper title alone is not enough to catch this.
    - How we are solving: We fetch the full OpenAlex author record and check their x_concepts (career-wide topic fingerprint). If their concepts have zero overlap with the student's area keywords, we drop them. If unclear, we ask the LLM to decide.

2. PhD students and postdocs mistaken for PIs — First-author papers appear in OpenAlex for grad students too. Personal fellowships (NIH F31/F32, MSCA postdoc grants, UKRI studentships) list the junior researcher as the awardee, not the supervisor. If we treat any name in an author list as a PI, we surface 24-year-old grad students.
    - How we are solving: We use the last-author heuristic (last author = PI in most STEM papers), check bibliometric thresholds (works_count and h_index), and verify employment role via ORCID. Anyone below the thresholds without a confirmed faculty role gets dropped.

3. Wrong-domain keyword leakage — "Biodegradable plastic cartridges" leaks into a biomaterials list (it's military ammo R&D). "Trauma-informed" leaks into clinical psychology (it's a literary-history project). "DNA barcoding" leaks into plant biology (it's single-cell Hi-C chromatin work). Keywords match on the surface but the domain is completely different.
    - How we are solving: We use grounded LLM query terms instead of free-text keywords, and filter by OpenAlex topic IDs so papers have to be classified under the right topic by OpenAlex, not just match a keyword string.

4. Dual affiliations and recently moved PIs — A PI holding a joint appointment at ETH Zürich and MIT — which country do we assign? A PI who moved from Germany to the US last year still has mostly old German papers, so our majority vote might say "DE" incorrectly.
    - How we are solving: We run a country re-verification step using only recent papers (last 5 years) and use majority vote across those. A recently moved PI's recent works will reflect the new country and get dropped correctly.

5. Eligibility restrictions buried in PhD ads — "UK only", "home fees", "EU residents only", "Chinese students only" are hidden inside free-text vacancy descriptions. Surfacing an ineligible position to an Indian student is worse than not surfacing it.
    - How we are solving: We scrape the full description text from FindAPhD and PhDScanner. We first run a fast keyword check. If the result is unclear, we send the description to the LLM and ask whether a student with the given citizenship can apply. Ineligible positions are filtered out before they reach the output.

---

### Partially Solved

6. LLM returning wrong or hallucinated concepts — LLM might return a broad concept like "Graph Learning" instead of something specific. That broad query pulls papers from social network analysis and biology — all wrong areas for the student.
    - How we are solving: We use structured output with a strict Pydantic schema so the LLM can't return freeform text. We also use the 4-level OpenAlex topic hierarchy to ground concepts to real topic IDs. Gap — we don't validate that returned concepts actually exist in OpenAlex, and there's no retry on a bad response.

7. Famous PIs dominating retrieval — Citation-sorted results mean the same top professors appear on every first page. Less-known but equally relevant researchers at solid universities never get fetched. The shortlist ends up too heavy on "reach" tier.
    - How we are solving: We apply per-area quotas so no single area fills more than 50% of the final list, and we give recency a dedicated weight in the score so newer PIs still surface. Gap — no explicit tier-balance guarantee (e.g. minimum 10 "target" entries).

8. Recency doesn't mean the PI is actively recruiting — A PI could have a 2024 paper but just retired. Publication date is a proxy for activity, not the real thing.
    - How we are solving: Partially handled by recency score in the formula. Gap — no homepage scraping for "currently accepting students" language yet.

9. OpenAlex author record fragmentation — Prof. Müller publishes under two name variants. OpenAlex creates two records, neither with full x_concepts. We might surface the same person twice.
    - How we are solving: We keep candidates with empty x_concepts rather than dropping them (unverifiable is not the same as wrong). Gap — no name + institution deduplication pass to catch the double-surface.

10. Industry-affiliated researchers — Google Brain, DeepMind, Meta AI researchers publish great papers but cannot formally supervise PhD students. Our institution type check catches "company" type but some joint academic-industry roles are tagged as "education" in OpenAlex.
    - How we are solving: Partially handled via institution type check in disambiguation. Gap — joint appointment cases where OpenAlex picks the academic affiliation over the industry one.

11. Alphabetical authorship fields — In math, economics, and CS theory, authors are listed alphabetically not by contribution. The last-author PI heuristic completely breaks here.
    - How we are solving: Documented as a known limitation in run_metadata. Gap — needs a venue/journal field classifier to detect when alphabetical authorship applies.

12. Cache serving stale data — We cache all OpenAlex API responses to disk with a 24-hour TTL. If a PI updates their affiliation, publishes a new paper, or moves institutions within that window, the pipeline runs on outdated data. A PI who moved countries yesterday will still show up in the old country for up to 24 hours. Same issue if OpenAlex fixes a wrong author record during that window — we keep serving the bad data from cache.
    - How we are solving: The 24-hour TTL is configurable in config.yaml so it can be reduced. But there's no way to selectively invalidate one author's cache entry without clearing the whole cache. Gap — no per-candidate cache invalidation strategy and no staleness warning in the output when cached data is being used.

---

### Not Solved

13. Same-name problem on grants — CORDIS grant search is by name string. "Thomas Müller" returns grants from multiple people. A materials engineer's grant gets attached to a CS professor with the same name.

14. Grant already ended — CORDIS might return a grant that ran 2019–2023. We attach it as evidence of active funding but the grant is over.

15. Co-PI vs actual PI on a grant — A grant might list someone as co-investigator, not the lead PI. They may not be the one recruiting PhD students. CORDIS doesn't clearly distinguish PI from co-PI.

16. Emeritus professors still publishing but not recruiting — A professor who retired in 2023 might still have 2024 papers and rank well. But they are not taking new PhD students.

17. PI explicitly not recruiting — The best-matched professor might say "not accepting new students for 2025/2026" right on their homepage. We surface them anyway.

18. PI pivoted fields — A PI who moved from physics to ML 5 years ago still has physics-dominant x_concepts from their early career. Our disambiguation drops them because the right keywords don't appear in their historical concepts.

19. Multiple co-last authors on a paper — Computational biology papers often have two co-last authors marked with †. Our heuristic marks only the final position as likely_pi.

20. Preprints treated the same as published papers — An arXiv preprint that was never peer-reviewed looks identical to a published paper in our schema. A PI with 10 preprints and 0 accepted papers would score well on evidence strength.

21. Stale contact email from a previous institution — A PI moved universities 2 years ago but their ORCID or old homepage still lists the old email. We send the student to a dead address.

22. Same institution, wrong department — A statistics professor at Oxford might match a CS-area student. The PI is relevant but sits in the stats department. The student applies to the CS doctoral program and the PI isn't affiliated with it.

23. Name change after marriage — A female researcher published under her maiden name for 10 years then changed it. OpenAlex has two sparse records. We drop her or surface her twice.

24. Language mismatch — A PI at a German university who only supervises in German gets recommended to an English-only student. Nothing in OpenAlex or CORDIS tells us this.

25. Self-citations inflating seniority metrics — An h-index inflated by self-citations makes a junior PI look more senior. We use cited_by_count directly from OpenAlex which includes self-citations.

26. PhD position already filled but posting not removed — FindAPhD listings are often not taken down after a position closes. We scrape and surface a position that filled 6 months ago.