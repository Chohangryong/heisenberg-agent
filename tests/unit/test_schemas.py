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


# ---------------------------------------------------------------------------
# OpenAI strict structured output compatibility
# ---------------------------------------------------------------------------


def _collect_object_nodes(schema: dict, path: str = "root") -> list[tuple[str, dict]]:
    """Recursively find all object-type nodes in a JSON schema."""
    nodes = []
    if not isinstance(schema, dict):
        return nodes
    if schema.get("type") == "object" and "properties" in schema:
        nodes.append((path, schema))
    for name, sub in schema.get("$defs", {}).items():
        nodes.extend(_collect_object_nodes(sub, f"$defs.{name}"))
    for name, sub in schema.get("properties", {}).items():
        nodes.extend(_collect_object_nodes(sub, f"{path}.{name}"))
    if "items" in schema:
        nodes.extend(_collect_object_nodes(schema["items"], f"{path}[items]"))
    return nodes


def test_summary_schema_openai_strict_compatible():
    """Final schema for SummaryResult: additionalProperties + required on all objects."""
    from heisenberg_agent.llm.client import ensure_openai_strict_schema

    schema = SummaryResult.model_json_schema()
    ensure_openai_strict_schema(schema)

    for path, node in _collect_object_nodes(schema):
        assert node.get("additionalProperties") is False, (
            f"{path} missing additionalProperties: false"
        )
        assert set(node["required"]) == set(node["properties"].keys()), (
            f"{path} required does not match properties keys"
        )


def test_critique_schema_openai_strict_compatible():
    """Final schema for CritiqueResult: additionalProperties + required on all objects."""
    from heisenberg_agent.llm.client import ensure_openai_strict_schema

    schema = CritiqueResult.model_json_schema()
    ensure_openai_strict_schema(schema)

    for path, node in _collect_object_nodes(schema):
        assert node.get("additionalProperties") is False, (
            f"{path} missing additionalProperties: false"
        )
        assert set(node["required"]) == set(node["properties"].keys()), (
            f"{path} required does not match properties keys"
        )


def test_nested_evidence_span_has_additional_properties_false():
    """$defs.EvidenceSpan gets additionalProperties: false after postprocessing."""
    from heisenberg_agent.llm.client import ensure_openai_strict_schema

    schema = SummaryResult.model_json_schema()
    ensure_openai_strict_schema(schema)

    es = schema["$defs"]["EvidenceSpan"]
    assert es["additionalProperties"] is False


def test_raw_schema_already_has_additional_properties_via_extra_forbid():
    """Pydantic extra='forbid' sets additionalProperties on root models,
    but this test documents that it also propagates to $defs."""
    raw = SummaryResult.model_json_schema()
    assert raw.get("additionalProperties") is False
    assert raw["$defs"]["EvidenceSpan"].get("additionalProperties") is False


def test_openai_strict_schema_adds_missing_required():
    """evidence_spans is optional in Pydantic but must be in required for OpenAI strict.

    Raw schema omits evidence_spans from required (it has default_factory).
    Post-processing adds it.
    """
    from heisenberg_agent.llm.client import ensure_openai_strict_schema

    raw = SummaryResult.model_json_schema()
    assert "evidence_spans" not in raw.get("required", []), (
        "precondition: evidence_spans should NOT be in raw required"
    )

    ensure_openai_strict_schema(raw)
    assert "evidence_spans" in raw["required"], (
        "evidence_spans must be in required after postprocessing"
    )
