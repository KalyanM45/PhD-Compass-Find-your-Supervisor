"""Subagent: find linked PhD programs and open positions for a single PI candidate.

Two live sources run in parallel, preceded by an offline institution lookup:

  1. Institution lookup table  — ~80 major universities mapped to doctoral program URLs.
                                  Zero network calls, zero failure rate.
  2. FindAPhD.com              — UK/EU structured listings with funding + eligibility.
  3. PhD Scanner               — UK aggregator, catches positions not on FindAPhD.

Output per program (matches LinkedProgram schema):
  {name, url, open_positions: [{title, url, deadline}]}
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from src.utils.sources import findaphd, phdscanner

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Strategy 1 — Institution → doctoral program URL lookup table
# ---------------------------------------------------------------------------

_INSTITUTION_PROGRAMS: dict[str, tuple[str, str]] = {
    # Germany
    "technical university of munich":   ("TU Munich Doctoral Program", "https://www.tum.de/en/research/doctoral-programs"),
    "tu munich":                        ("TU Munich Doctoral Program", "https://www.tum.de/en/research/doctoral-programs"),
    "ludwig maximilian":                ("LMU Munich Graduate Center", "https://www.uni-muenchen.de/forschung/graduiertenausbildung/index.html"),
    "humboldt":                         ("Humboldt-Universität Graduate School", "https://www.hu-berlin.de/en/research/graduate-education"),
    "freie universität":                ("FU Berlin Doctoral Programs", "https://www.fu-berlin.de/en/sites/promovieren/index.html"),
    "heidelberg":                       ("Heidelberg Graduate School", "https://www.uni-heidelberg.de/en/research/graduate-education"),
    "rwth aachen":                      ("RWTH Aachen Doctoral Studies", "https://www.rwth-aachen.de/cms/root/research/~eoe/doctoral-studies"),
    "max planck":                       ("Max Planck PhD Programs", "https://www.mpg.de/phd-programs"),
    "karlsruhe":                        ("KIT Graduate School", "https://www.kit.edu/kit/english/10380.php"),
    "tu berlin":                        ("TU Berlin Doctoral Studies", "https://www.tu.berlin/en/research/doctoral-studies"),
    "tu dresden":                       ("TU Dresden Graduate Academy", "https://tu-dresden.de/forschung/wissenschaftliche-qualifizierung"),
    "tübingen":                         ("University of Tübingen Graduate Training", "https://www.uni-tuebingen.de/en/research/graduate-training.html"),
    # Netherlands
    "delft":                            ("TU Delft PhD Programs", "https://www.tudelft.nl/en/education/phd-education"),
    "amsterdam":                        ("University of Amsterdam PhD", "https://www.uva.nl/en/research/phd"),
    "eindhoven":                        ("TU/e PhD Programs", "https://www.tue.nl/en/education/phd"),
    "utrecht":                          ("Utrecht University PhD", "https://www.uu.nl/en/education/phd"),
    "leiden":                           ("Leiden University PhD", "https://www.universiteitleiden.nl/en/education/phd"),
    "groningen":                        ("University of Groningen PhD", "https://www.rug.nl/research/phd"),
    # Switzerland
    "eth zurich":                       ("ETH Zürich Doctoral Studies", "https://ethz.ch/en/doctorate.html"),
    "eth zürich":                       ("ETH Zürich Doctoral Studies", "https://ethz.ch/en/doctorate.html"),
    "epfl":                             ("EPFL Doctoral School", "https://www.epfl.ch/education/phd"),
    "university of zurich":             ("UZH Graduate Campus", "https://www.graduatecampus.uzh.ch/en.html"),
    "basel":                            ("University of Basel PhD", "https://www.unibas.ch/en/Research/PhD.html"),
    # UK
    "oxford":                           ("University of Oxford Graduate School", "https://www.ox.ac.uk/admissions/graduate"),
    "cambridge":                        ("University of Cambridge Graduate Admissions", "https://www.graduate.study.cam.ac.uk"),
    "imperial college":                 ("Imperial College London Doctoral Studies", "https://www.imperial.ac.uk/study/pg/graduate-school/doctoral-studies"),
    "university college london":        ("UCL Graduate School", "https://www.ucl.ac.uk/graduate"),
    "ucl":                              ("UCL Graduate School", "https://www.ucl.ac.uk/graduate"),
    "edinburgh":                        ("University of Edinburgh PhD", "https://www.ed.ac.uk/studying/postgraduate/research"),
    "manchester":                       ("University of Manchester Doctoral Academy", "https://www.manchester.ac.uk/study/postgraduate-research"),
    "bristol":                          ("University of Bristol Doctoral College", "https://www.bristol.ac.uk/doctoral-college"),
    "warwick":                          ("University of Warwick Doctoral College", "https://warwick.ac.uk/fac/grad"),
    "southampton":                      ("University of Southampton Doctoral College", "https://www.southampton.ac.uk/doctoral-college"),
    # US
    "mit":                              ("MIT Graduate Admissions", "https://gradadmissions.mit.edu"),
    "stanford":                         ("Stanford Graduate Admissions", "https://gradadmissions.stanford.edu"),
    "carnegie mellon":                  ("CMU PhD Programs", "https://www.cmu.edu/graduate"),
    "caltech":                          ("Caltech Graduate Studies", "https://www.gradoffice.caltech.edu"),
}


def _lookup_institution(institution: str) -> tuple[str, str] | None:
    lower = institution.lower()
    for key, value in _INSTITUTION_PROGRAMS.items():
        if key in lower:
            return value
    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch_programs_for_candidate(
    candidate: dict[str, Any],
    area: str,
) -> list[dict[str, Any]]:
    """Return linked PhD program records for a PI candidate."""
    programs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    supervisor_name = candidate.get("name", "")
    institution = candidate.get("institution", "")
    country = candidate.get("country", "")

    # --- Strategy 1: institution lookup (instant, no network) ---
    lookup = _lookup_institution(institution)
    if lookup:
        prog_name, prog_url = lookup
        if prog_url not in seen_urls:
            seen_urls.add(prog_url)
            programs.append({"name": prog_name, "url": prog_url, "open_positions": []})

    # --- Strategies 2 + 3: FindAPhD and PhD Scanner in parallel ---
    tasks = {
        "findaphd":   lambda: findaphd.search(supervisor_name, area, country),
        "phdscanner": lambda: phdscanner.search(supervisor_name, area),
    }
    source_labels = {
        "findaphd":   f"FindAPhD — {supervisor_name}",
        "phdscanner": f"PhD Scanner — {supervisor_name}",
    }
    source_urls = {
        "findaphd":   f"https://www.findaphd.com/phds/search/?Keywords={supervisor_name.replace(' ', '+')}",
        "phdscanner": f"https://phdscanner.com/phd-opportunities/?search={supervisor_name.replace(' ', '+')}",
    }

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks.items()}
        for future in as_completed(futures):
            source = futures[future]
            try:
                vacancies = future.result()
            except Exception as exc:
                logger.debug("Vacancy source %r failed for %r: %s", source, supervisor_name, exc)
                continue

            if not vacancies:
                continue

            prog_url = source_urls[source]
            if prog_url in seen_urls:
                continue
            seen_urls.add(prog_url)

            open_positions = [
                {"title": v["title"], "url": v["url"], "deadline": v.get("deadline")}
                for v in vacancies
            ]
            programs.append({
                "name": source_labels[source],
                "url": prog_url,
                "open_positions": open_positions,
            })
            logger.debug(
                "ProgramFetcher: %s → %d positions for %r",
                source, len(open_positions), supervisor_name,
            )

    logger.info(
        "ProgramFetcher: %r — %d program entries, %d open positions",
        supervisor_name,
        len(programs),
        sum(len(p["open_positions"]) for p in programs),
    )
    return programs
