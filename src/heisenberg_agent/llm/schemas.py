"""Pydantic v2 models for structured LLM output.

These models serve two purposes:
1. Generate JSON schema for LiteLLM strict structured output (response_format)
2. Post-validation of LLM response (business constraints like confidence range)
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EvidenceSpan(BaseModel):
    """A reference to a specific part of the article that supports a point."""

    model_config = ConfigDict(extra="forbid")

    section_kind: str = Field(description="Which section this evidence comes from")
    quote_hint: str = Field(description="Short quote or paraphrase from the section")
    reason: str = Field(description="Why this evidence is relevant")


class SummaryResult(BaseModel):
    """Structured summary of an article."""

    model_config = ConfigDict(extra="forbid")

    core_thesis: str = Field(description="The central argument or finding")
    supporting_points: list[str] = Field(description="Key supporting arguments")
    conclusion: str = Field(description="Author's conclusion")
    keywords: list[str] = Field(description="3-7 keywords")
    importance: str = Field(description="high, medium, or low")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score 0-1")
    evidence_spans: list[EvidenceSpan] = Field(
        default_factory=list,
        description="References to article sections supporting the summary",
    )


class CritiqueResult(BaseModel):
    """Structured critical analysis of an article."""

    model_config = ConfigDict(extra="forbid")

    logic_gaps: list[str] = Field(description="Logical weaknesses or gaps")
    missing_views: list[str] = Field(description="Perspectives not considered")
    claims_to_verify: list[str] = Field(description="Claims that need fact-checking")
    interest_analysis: str = Field(description="Stakeholder interest analysis")
    overall_assessment: str = Field(description="Overall critical assessment")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score 0-1")


class AnalysisResult(BaseModel):
    """Unified summary + critique from a single LLM call."""

    model_config = ConfigDict(extra="forbid")

    # Summary fields
    core_thesis: str = Field(description="The central argument or finding")
    supporting_points: list[str] = Field(description="Key supporting arguments")
    conclusion: str = Field(description="Author's conclusion")
    keywords: list[str] = Field(description="3-7 keywords")
    importance: str = Field(description="high, medium, or low")
    confidence: float = Field(ge=0.0, le=1.0, description="Summary confidence score 0-1")
    evidence_spans: list[EvidenceSpan] = Field(
        default_factory=list,
        description="References to article sections supporting the summary",
    )

    # Critique fields
    logic_gaps: list[str] = Field(description="Logical weaknesses or gaps")
    missing_views: list[str] = Field(description="Perspectives not considered")
    claims_to_verify: list[str] = Field(description="Claims that need fact-checking")
    interest_analysis: str = Field(description="Stakeholder interest analysis")
    overall_assessment: str = Field(description="Overall critical assessment")
    critique_confidence: float = Field(ge=0.0, le=1.0, description="Critique confidence score 0-1")
