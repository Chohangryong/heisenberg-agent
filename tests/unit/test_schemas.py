"""Unit tests for LLM structured output Pydantic schemas."""

import pytest
from pydantic import ValidationError

from heisenberg_agent.llm.schemas import CritiqueResult, EvidenceSpan, SummaryResult


# ---------------------------------------------------------------------------
# SummaryResult
# ---------------------------------------------------------------------------


def test_summary_valid():
    data = {
        "core_thesis": "AI inference is improving",
        "supporting_points": ["Point 1", "Point 2"],
        "conclusion": "Nvidia leads",
        "keywords": ["AI", "GPU"],
        "importance": "high",
        "confidence": 0.85,
        "evidence_spans": [
            {
                "section_kind": "main_body",
                "quote_hint": "Blackwell Ultra",
                "reason": "Key product announcement",
            }
        ],
    }
    result = SummaryResult.model_validate(data)
    assert result.core_thesis == "AI inference is improving"
    assert result.importance == "high"
    assert result.confidence == 0.85
    assert len(result.evidence_spans) == 1
    assert result.evidence_spans[0].section_kind == "main_body"


def test_summary_valid_without_evidence_spans():
    data = {
        "core_thesis": "Thesis",
        "supporting_points": [],
        "conclusion": "Conclusion",
        "keywords": ["k1"],
        "importance": "low",
        "confidence": 0.5,
    }
    result = SummaryResult.model_validate(data)
    assert result.evidence_spans == []


def test_summary_missing_required_field():
    data = {
        "supporting_points": ["Point 1"],
        "conclusion": "Conclusion",
        "keywords": ["k1"],
        "importance": "medium",
        "confidence": 0.5,
    }
    with pytest.raises(ValidationError):
        SummaryResult.model_validate(data)


def test_summary_confidence_out_of_range():
    data = {
        "core_thesis": "Thesis",
        "supporting_points": [],
        "conclusion": "Conclusion",
        "keywords": [],
        "importance": "high",
        "confidence": 1.5,
    }
    with pytest.raises(ValidationError):
        SummaryResult.model_validate(data)


def test_summary_confidence_negative():
    data = {
        "core_thesis": "Thesis",
        "supporting_points": [],
        "conclusion": "Conclusion",
        "keywords": [],
        "importance": "high",
        "confidence": -0.1,
    }
    with pytest.raises(ValidationError):
        SummaryResult.model_validate(data)


def test_summary_json_schema_generation():
    schema = SummaryResult.model_json_schema()
    assert "properties" in schema
    assert "core_thesis" in schema["properties"]
    assert "confidence" in schema["properties"]


# ---------------------------------------------------------------------------
# CritiqueResult
# ---------------------------------------------------------------------------


def test_critique_valid():
    data = {
        "logic_gaps": ["Gap 1"],
        "missing_views": ["View 1"],
        "claims_to_verify": ["Claim 1"],
        "interest_analysis": "Nvidia has commercial interest",
        "overall_assessment": "Generally solid but biased",
        "confidence": 0.7,
    }
    result = CritiqueResult.model_validate(data)
    assert result.overall_assessment == "Generally solid but biased"
    assert result.confidence == 0.7


def test_critique_missing_required_field():
    data = {
        "logic_gaps": [],
        "missing_views": [],
        # claims_to_verify missing
        "interest_analysis": "None",
        "overall_assessment": "OK",
        "confidence": 0.5,
    }
    with pytest.raises(ValidationError):
        CritiqueResult.model_validate(data)


def test_critique_confidence_boundary():
    data = {
        "logic_gaps": [],
        "missing_views": [],
        "claims_to_verify": [],
        "interest_analysis": "None",
        "overall_assessment": "OK",
        "confidence": 0.0,
    }
    result = CritiqueResult.model_validate(data)
    assert result.confidence == 0.0

    data["confidence"] = 1.0
    result = CritiqueResult.model_validate(data)
    assert result.confidence == 1.0


def test_critique_json_schema_generation():
    schema = CritiqueResult.model_json_schema()
    assert "properties" in schema
    assert "logic_gaps" in schema["properties"]


# ---------------------------------------------------------------------------
# EvidenceSpan
# ---------------------------------------------------------------------------


def test_evidence_span_valid():
    span = EvidenceSpan(
        section_kind="main_body",
        quote_hint="Blackwell Ultra",
        reason="Key product",
    )
    assert span.section_kind == "main_body"
