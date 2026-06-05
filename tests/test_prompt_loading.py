"""Prompt .md dosya yukleme testleri."""
import pytest
from pathlib import Path


@pytest.fixture
def prompts_dir():
    return Path(__file__).resolve().parent.parent / "prompts"


def test_all_default_prompts_exist_and_nonempty(prompts_dir):
    for name in ["extractor-default", "curator-default", "ranker-default", "wizard-system"]:
        path = prompts_dir / f"{name}.md"
        assert path.exists(), f"Prompt file missing: {name}.md"
        content = path.read_text(encoding="utf-8")
        assert len(content.strip()) > 10, f"Prompt file too short: {name}.md"


def test_load_prompt_returns_text(prompts_dir):
    from humetric.src.humetric.agents import _load_prompt
    result = _load_prompt("extractor-default")
    assert len(result) > 0
    assert "metrik" in result


def test_load_prompt_missing_file_returns_empty():
    from humetric.src.humetric.agents import _load_prompt
    result = _load_prompt("nonexistent-file-xyz")
    assert result == ""


def test_load_prompt_utf8(prompts_dir):
    path = prompts_dir / "extractor-default.md"
    content = path.read_text(encoding="utf-8")
    assert "ç" in content or "ş" in content or "ğ" in content or "ü" in content or "ı" in content
