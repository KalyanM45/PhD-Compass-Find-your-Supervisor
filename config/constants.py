OPENALEX_BASE_URL = "https://api.openalex.org"
ORCID_BASE_URL = "https://pub.orcid.org/v3.0"
CONCEPT_SCORE_THRESHOLD = 0.15
KEYWORD_CLEAR_MATCH = 2
NON_SUPERVISING_TYPES = frozenset({"company", "funder", "archive"})
FACULTY_KEYWORDS = frozenset({
    "professor", "faculty", "lecturer", "reader", "docent", "privatdozent",
    "principal investigator", "group leader", "chair", "head of", "director",
    "associate professor", "assistant professor", "full professor", "research fellow",
})
JUNIOR_KEYWORDS = frozenset({
    "phd", "doctoral", "graduate student", "postdoc", "post-doc",
    "postdoctoral", "research associate", "research assistant",
    "student", "intern", "trainee",
})
