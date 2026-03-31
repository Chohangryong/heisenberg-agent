"""LLM client — structured output via LiteLLM.

Call flow:
1. Render prompt template with article text
2. Call LiteLLM with json_schema strict response_format
3. Parse JSON response
4. Validate with Pydantic model (business constraints)

Fallback: if primary model fails, retry once with fallback model.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from heisenberg_agent.utils.logger import get_logger

logger = get_logger()

T = TypeVar("T", bound=BaseModel)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def ensure_openai_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Post-process a Pydantic JSON schema for OpenAI strict structured output.

    OpenAI strict mode (strict: true) requires every object node to have:
    1. additionalProperties: false
    2. required: [every key in properties]

    This function enforces both rules recursively without changing the
    Pydantic model's own optional/required semantics — those are preserved
    at the model_validate() layer.

    Mutates and returns the same dict for convenience.
    """
    if not isinstance(schema, dict):
        return schema

    # If this node is an object with properties, enforce strict rules
    if schema.get("type") == "object" and "properties" in schema:
        schema.setdefault("additionalProperties", False)
        schema["required"] = list(schema["properties"].keys())

    # Recurse into $defs
    for _def_name, def_schema in schema.get("$defs", {}).items():
        ensure_openai_strict_schema(def_schema)

    # Recurse into properties (for nested inline objects, if any)
    for _prop_name, prop_schema in schema.get("properties", {}).items():
        ensure_openai_strict_schema(prop_schema)

    # Recurse into items (for array-of-objects)
    if "items" in schema:
        ensure_openai_strict_schema(schema["items"])

    return schema


# Backward-compatible alias
ensure_additional_properties_false = ensure_openai_strict_schema


@dataclass
class UsageMeta:
    """Token usage and cost metadata from an LLM call."""

    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    fallback_used: bool = False


@dataclass
class LLMResult:
    """Result of a structured LLM call."""

    data: Any  # Pydantic model instance
    usage: UsageMeta = field(default_factory=UsageMeta)


class LLMClient:
    """Calls LLM with structured output and Pydantic validation."""

    def __init__(self, config: dict[str, Any]) -> None:
        """
        Args:
            config: LLM config dict with keys like:
                summary: {provider, model, max_tokens, temperature}
                critique: {provider, model, max_tokens, temperature}
                fallback: {provider, model}
                max_input_chars: int (default 12000)
        """
        self._config = config

    def call(
        self,
        prompt_name: str,
        article_text: str,
        response_model: type[T],
        *,
        task_key: str = "summary",
    ) -> LLMResult:
        """Call LLM with structured output.

        Args:
            prompt_name: Filename in llm/prompts/ (e.g. "summary.md").
            article_text: Prepared article text.
            response_model: Pydantic model class for validation.
            task_key: Config key for model selection ("summary" or "critique").

        Returns:
            LLMResult with validated Pydantic instance and usage metadata.

        Raises:
            LLMError: If both primary and fallback calls fail.
        """
        prompt_template = self._load_prompt(prompt_name)
        rendered = prompt_template.replace("{article_text}", article_text)

        task_config = self._config.get(task_key, {})

        # Build fallback chain: fallback, fallback_2, fallback_3, ...
        fallback_chain: list[dict[str, Any]] = []
        if self._config.get("fallback"):
            fallback_chain.append(self._config["fallback"])
        for i in range(2, 10):
            key = f"fallback_{i}"
            if self._config.get(key):
                fallback_chain.append(self._config[key])

        # Try primary model
        try:
            return self._do_call(rendered, response_model, task_config, fallback_used=False)
        except Exception as primary_err:
            logger.warning(
                "llm.primary_failed",
                task=task_key,
                model=task_config.get("model"),
                error=str(primary_err),
            )

        # Try fallback chain
        last_err = primary_err
        for fb_config in fallback_chain:
            try:
                return self._do_call(rendered, response_model, fb_config, fallback_used=True)
            except Exception as fb_err:
                logger.warning(
                    "llm.fallback_failed",
                    task=task_key,
                    model=fb_config.get("model"),
                    error=str(fb_err),
                )
                last_err = fb_err

        raise LLMError(
            f"All models failed for {task_key}"
        ) from last_err

    def _do_call(
        self,
        rendered_prompt: str,
        response_model: type[T],
        model_config: dict[str, Any],
        *,
        fallback_used: bool,
    ) -> LLMResult:
        """Execute a single LLM call with structured output."""
        import litellm

        model = model_config.get("model", "claude-sonnet-4-6")
        provider = model_config.get("provider", "anthropic")
        max_tokens = model_config.get("max_tokens", 1800)
        temperature = model_config.get("temperature", 0.2)

        # Build JSON schema from Pydantic model, ensure OpenAI strict compatibility
        schema = response_model.model_json_schema()
        ensure_openai_strict_schema(schema)
        schema_name = response_model.__name__

        start = time.monotonic()
        response = litellm.completion(
            model=f"{provider}/{model}" if provider else model,
            messages=[{"role": "user", "content": rendered_prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
        )
        latency_ms = int((time.monotonic() - start) * 1000)

        # Step 2: Parse JSON
        raw_content = response.choices[0].message.content
        parsed = json.loads(raw_content)

        # Step 3: Pydantic post-validation (business constraints)
        validated = response_model.model_validate(parsed)

        # Collect usage metadata
        usage = response.get("usage", {})
        cost = litellm.completion_cost(completion_response=response)

        return LLMResult(
            data=validated,
            usage=UsageMeta(
                provider=provider,
                model=model,
                input_tokens=getattr(usage, "prompt_tokens", 0),
                output_tokens=getattr(usage, "completion_tokens", 0),
                cost_usd=cost or 0.0,
                latency_ms=latency_ms,
                fallback_used=fallback_used,
            ),
        )

    def _load_prompt(self, name: str) -> str:
        """Load prompt template from prompts directory."""
        path = _PROMPTS_DIR / name
        if not path.exists():
            raise FileNotFoundError(f"Prompt template not found: {path}")
        return path.read_text(encoding="utf-8")


class LLMError(Exception):
    """Raised when LLM call fails after all retries."""

    pass
