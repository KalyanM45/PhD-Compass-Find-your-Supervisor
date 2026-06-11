from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------


class Education(BaseModel):
    degree: str
    institution: str
    country: str
    grade: Optional[str] = None
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    thesis_title: Optional[str] = None
    thesis_abstract: Optional[str] = None


class Project(BaseModel):
    title: str
    description: str
    tech: list[str] = Field(default_factory=list)


class Publication(BaseModel):
    title: str
    venue: Optional[str] = None
    year: Optional[int] = None
    doi: Optional[str] = None
    role: Optional[str] = None


class TargetIntake(BaseModel):
    semester: str
    year: int


class StudentProfile(BaseModel):
    student_id: str
    education: list[Education] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    publications: list[Publication] = Field(default_factory=list)
    research_interests: list[str] = Field(default_factory=list)
    # Applied domains the student cares about beyond their core methodology.
    # Used to cross-match against PI paper titles/concepts and enrich why_match blurbs.
    # Examples: ["drug discovery", "healthcare AI", "climate modelling", "robotics"]
    use_cases: list[str] = Field(default_factory=list)
    target_countries: list[str]
    target_intake: TargetIntake
    intro_call_summary: Optional[str] = None
    raw_resume_text: Optional[str] = None
    citizenship: Optional[str] = None  # ISO-2 code e.g. "IN", "CN", "US", or "International"

    @model_validator(mode="after")
    def _upper_countries(self) -> "StudentProfile":
        # Basic uppercase normalisation at parse time.
        # Full ISO mapping (UK→GB etc.) is applied by agents.profile_agent.normalise_countries.
        self.target_countries = [c.upper() for c in self.target_countries]
        return self


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------


class Paper(BaseModel):
    title: str
    year: Optional[int] = None
    doi: Optional[str] = None
    url: str
    openalex_id: Optional[str] = None
    relevance_note: Optional[str] = None


class Grant(BaseModel):
    title: str
    funder: Optional[str] = None
    id: Optional[str] = None
    url: str
    years: Optional[str] = None


class Evidence(BaseModel):
    papers: list[Paper] = Field(default_factory=list)
    grants: list[Grant] = Field(default_factory=list)

    @model_validator(mode="after")
    def at_least_one(self) -> "Evidence":
        if not self.papers and not self.grants:
            raise ValueError("Evidence must have at least one paper or grant")
        return self


class OpenPosition(BaseModel):
    title: str
    url: str
    deadline: Optional[str] = None


class LinkedProgram(BaseModel):
    name: str
    url: str
    open_positions: list[OpenPosition] = Field(default_factory=list)


class Supervisor(BaseModel):
    name: str
    openalex_author_id: Optional[str] = None
    orcid: Optional[str] = None
    institution: str
    country: str
    contact_email: Optional[str] = None
    research_focus: Optional[str] = None


class Recommendation(BaseModel):
    supervisor: Supervisor
    research_area: str
    evidence: Evidence
    why_match: str
    match_score: float = Field(ge=0.0, le=1.0)
    tier: Optional[str] = None  # reach | target | safety | null
    linked_programs: list[LinkedProgram] = Field(default_factory=list)


class CoverageSummary(BaseModel):
    model_config = {"extra": "allow"}


class RunMetadata(BaseModel):
    total_recommendations: int
    wall_clock_seconds: float
    email_hit_rate: float
    deferred_limitations: list[str] = Field(default_factory=list)


class Shortlist(BaseModel):
    student_id: str
    generated_at: datetime
    target_countries: list[str]
    target_intake: TargetIntake
    recommendations: list[Recommendation]
    coverage_summary: dict[str, int] = Field(default_factory=dict)
    run_metadata: RunMetadata

    @model_validator(mode="after")
    def validate_hard_rules(self) -> "Shortlist":
        for rec in self.recommendations:
            if rec.supervisor.country not in self.target_countries:
                raise ValueError(
                    f"Supervisor {rec.supervisor.name} country "
                    f"{rec.supervisor.country!r} not in target_countries"
                )
        return self
