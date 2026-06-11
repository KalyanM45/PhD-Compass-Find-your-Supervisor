# schema.md — Output JSON Schema

> Required deliverable per PDF §8.5.  
> Every field, its type, whether it is required, and any validation rules.

---

## Top-level Shortlist Object

```jsonc
{
  "student_id":       "stu_001",                    // string, required
  "generated_at":     "2026-06-10T14:30:22Z",       // ISO 8601 datetime UTC, required
  "target_countries": ["DE", "NL", "CH"],            // list[string] ISO 3166-1 alpha-2, required
  "target_intake":    { "semester": "Fall", "year": 2026 },  // required
  "recommendations":  [ ...Recommendation ],         // list, required, length ≥ 50
  "coverage_summary": { "area_name": 28, ... },      // dict[string → int], required
  "run_metadata":     { ... }                        // RunMetadata, required
}
```

### Hard validation rules (enforced by Pydantic — run fails loudly if violated)

| Rule | Error if violated |
|------|------------------|
| `len(recommendations) >= 50` | `ValidationError` |
| Every `supervisor.country` ∈ `target_countries` | `ValidationError` |
| Every `Evidence` object has ≥ 1 paper **or** grant | `ValidationError` |
| `match_score` ∈ [0.0, 1.0] | `ValidationError` |

---

## Recommendation Object

```jsonc
{
  "supervisor":      { ...Supervisor },     // required
  "research_area":   "geometric deep learning",  // string, which student area this maps to, required
  "evidence":        { ...Evidence },       // required
  "why_match":       "Your MSc thesis...", // string, required — must reference specific paper/grant
  "match_score":     0.82,                 // float [0.0–1.0], required
  "tier":            "reach",              // "reach" | "target" | "safety" | null
  "linked_programs": [ ...LinkedProgram ]  // list, may be empty
}
```

---

## Supervisor Object

```jsonc
{
  "name":               "Jane Müller",                      // string, required
  "openalex_author_id": "https://openalex.org/A1234567890", // string | null
  "orcid":              "0000-0002-1825-0097",              // string | null
  "institution":        "ETH Zürich",                       // string, required
  "country":            "CH",                               // string ISO 3166-1 alpha-2, required
  "contact_email":      "jane.mueller@ethz.ch",             // string | null — never fabricated
  "research_focus":     "Geometric deep learning for molecular systems" // string | null
}
```

---

## Evidence Object

```jsonc
{
  "papers": [ ...Paper ],   // list[Paper], may be empty if grants present
  "grants": [ ...Grant ]    // list[Grant], may be empty if papers present
  // Constraint: at least one of papers or grants must be non-empty
}
```

### Paper Object

```jsonc
{
  "title":         "Equivariant message passing for molecular property prediction", // string, required
  "year":          2024,                      // int | null
  "doi":           "10.xxxx/xxxxx",           // string | null
  "url":           "https://doi.org/10.xxxx", // string, required (doi URL or OpenAlex URL)
  "openalex_id":   "https://openalex.org/W...", // string | null
  "relevance_note": "Same equivariant GNN family as the student's thesis" // string | null
}
```

### Grant Object

```jsonc
{
  "title":  "SNSF: Equivariant deep learning for chemistry", // string, required
  "funder": "Swiss National Science Foundation",             // string | null
  "id":     "200021_207372",                                 // string | null (funder grant ID)
  "url":    "https://cordis.europa.eu/project/id/...",       // string, required
  "years":  "2023–2027"                                      // string | null
}
```

---

## LinkedProgram Object

```jsonc
{
  "name": "ETH Zürich Doctoral Program in Computer Science", // string, required
  "url":  "https://inf.ethz.ch/doctorate",                  // string, required
  "open_positions": [ ...OpenPosition ]                      // list, may be empty
}
```

### OpenPosition Object

```jsonc
{
  "title":    "PhD in ML for chemistry",  // string, required
  "url":      "https://...",              // string, required
  "deadline": "2026-01-15"               // string | null (ISO date)
}
```

---

## CoverageSummary Object

A plain `dict[string, int]` mapping each research area name to the count of
recommendations in that area.

```jsonc
{
  "graph neural networks for molecular property prediction": 28,
  "geometric deep learning": 22,
  "AI for drug discovery": 18
}
```

---

## RunMetadata Object

```jsonc
{
  "total_recommendations": 68,       // int, required
  "wall_clock_seconds":    540.3,    // float, required
  "email_hit_rate":        0.61,     // float [0.0–1.0], required
  "deferred_limitations":  [         // list[string], may be empty
    "alphabetical-authorship fields (math/econ) break last-author PI heuristic",
    "linked_programs not yet live-scraped (department URL acceptable for v1)",
    "grant coverage limited to CORDIS (EU); DFG/UKRI not yet integrated"
  ]
}
```

---

## Tier Definitions

| Tier | Condition | Meaning |
|------|-----------|---------|
| `"reach"` | `match_score >= 0.75` | Strong alignment; student may be under-qualified but PI is an excellent fit |
| `"target"` | `match_score >= 0.50` | Good alignment; realistic application |
| `"safety"` | `match_score < 0.50` | Weaker alignment; likely to respond but not a perfect match |
| `null` | Not computed | Score present; tier not assigned |

---

## match_score Formula

```
match_score = 0.5 × topic_similarity
            + 0.2 × recency_score
            + 0.2 × evidence_strength
            + 0.1 × seniority_score
```

| Component | Formula |
|-----------|---------|
| `topic_similarity` | Keyword overlap: area + query_hint words ∩ author x_concepts names ÷ query word count. Capped at 1.0. |
| `recency_score` | `max(0, 1 − age/10)` where age = current_year − best_paper_year |
| `evidence_strength` | `min(1, n_papers × 0.2 + total_citations/50 × 0.8)` |
| `seniority_score` | `0.50×(h_index/60) + 0.25×(cited_by_count/10000) + 0.15×(works_count/200) + 0.10×(i10_index/100)` |

---

## Full Example Record

```jsonc
{
  "supervisor": {
    "name": "Stephan Günnemann",
    "openalex_author_id": "https://openalex.org/A2208157607",
    "orcid": "0000-0002-5902-4537",
    "institution": "Technical University of Munich",
    "country": "DE",
    "contact_email": null,
    "research_focus": "geometric deep learning"
  },
  "research_area": "geometric deep learning",
  "evidence": {
    "papers": [
      {
        "title": "Equivariant message passing for the prediction of tensorial properties and molecular spectra",
        "year": 2021,
        "doi": "10.48550/arXiv.2102.03207",
        "url": "https://doi.org/10.48550/arXiv.2102.03207",
        "openalex_id": "https://openalex.org/W3127584398",
        "relevance_note": null
      }
    ],
    "grants": [
      {
        "title": "Graph Neural Networks for Molecular Property Prediction",
        "funder": "European Commission (Horizon)",
        "id": "101070596",
        "url": "https://cordis.europa.eu/project/id/101070596",
        "years": "2022–2026"
      }
    ]
  },
  "why_match": "Your MSc thesis on SE(3)-equivariant message passing directly extends Prof. Günnemann's 2021 tensor-property prediction paper, and his active Horizon grant funds exactly this equivariant GNN direction.",
  "match_score": 0.81,
  "tier": "reach",
  "linked_programs": []
}
```
