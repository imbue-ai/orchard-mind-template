"""Unit tests for the personalization ('why reach out') generator."""

import pytest
from orchard import ai, service
from orchard.errors import DraftError


def _completion(text: str) -> ai.Completion:
    return ai.Completion(text=text, cost_usd=0.05)


def test_generate_returns_one_clean_sentence() -> None:
    result = service.generate_personalization(
        "https://github.com/x", research=lambda _p, _s: _completion("  They build   great agents. ")
    )
    assert result["sentence"] == "They build great agents."
    assert result["cost_usd"] == 0.05


def test_generate_requires_material() -> None:
    with pytest.raises(DraftError, match="link or some notes"):
        service.generate_personalization("   ", research=lambda _p, _s: _completion("x"))


def test_generate_feeds_material_and_company_context_to_the_model() -> None:
    captured: dict[str, str] = {}

    def research(prompt: str, system: str) -> ai.Completion:
        captured["prompt"] = prompt
        captured["system"] = system
        return _completion("Grounded reason.")

    service.generate_personalization("https://foo.dev and notes", research=research)
    assert "https://foo.dev" in captured["prompt"]
    assert "recruiter" in captured["system"] and "sender's company" in captured["system"]
