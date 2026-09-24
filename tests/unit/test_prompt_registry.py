"""Unit tests for the prompt registry (no DB)."""

from __future__ import annotations

import codecs
from pathlib import Path

import pytest

from omnigrowth.core.enums import ModelRole, PrivacyClass
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.prompts import PromptRegistry

FRONT = """---
id: {id}
version: {version}
role: CHEAP_LOCAL
privacy_class: PUBLIC
description: test prompt
variables: {variables}
{extra}---
"""

BODY = """## system
You write about {{ topic }}.

## user
Topic: {{topic}}
"""


def write_prompt(
    root: Path,
    rel: str = "demo/greet.v1.md",
    *,
    prompt_id: str = "demo/greet",
    version: int = 1,
    variables: str = "[topic]",
    extra: str = "",
    body: str = BODY,
    newline: str = "\n",
) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    text = FRONT.format(id=prompt_id, version=version, variables=variables, extra=extra) + body
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return path


def expect_config_error(root: Path, fragment: str) -> None:
    with pytest.raises(OmniGrowthError) as info:
        PromptRegistry.load(root)
    assert info.value.code is ErrorCode.CONFIGURATION_ERROR
    assert fragment in info.value.message


# ------------------------------------------------------------------ real package dir
def test_load_real_package_dir() -> None:
    registry = PromptRegistry.load()
    probe = registry.get("system/structured_probe@v1")
    assert probe.ref == "system/structured_probe@v1"
    assert probe.front_matter.role is ModelRole.CHEAP_LOCAL
    assert probe.front_matter.privacy_class is PrivacyClass.PUBLIC
    assert probe.front_matter.variables == ["topic"]
    assert probe.front_matter.output_schema == "omnigrowth.providers.probe.ProbeOutput"
    rendered = probe.render({"topic": "local LLMs"})
    assert "local LLMs" in rendered.messages[1]["content"]
    assert "JSON" in rendered.messages[0]["content"]


# ------------------------------------------------------------------ validation errors
def test_valid_synthetic_tree(tmp_path: Path) -> None:
    write_prompt(tmp_path)
    registry = PromptRegistry.load(tmp_path)
    assert registry.refs() == ["demo/greet@v1"]
    prompt = registry.get("demo/greet")
    assert prompt.system_template == "You write about {{ topic }}."
    assert prompt.user_template == "Topic: {{topic}}"
    assert prompt.front_matter.temperature == 0.2
    assert prompt.front_matter.max_tokens is None


def test_id_mismatch(tmp_path: Path) -> None:
    write_prompt(tmp_path, prompt_id="demo/other")
    expect_config_error(tmp_path, "demo/greet.v1.md")


def test_version_mismatch(tmp_path: Path) -> None:
    write_prompt(tmp_path, version=2)
    expect_config_error(tmp_path, "filename version")


def test_missing_section(tmp_path: Path) -> None:
    write_prompt(tmp_path, body="## system\nHello {{ topic }}\n")
    expect_config_error(tmp_path, "'## user'")


def test_sections_out_of_order(tmp_path: Path) -> None:
    write_prompt(tmp_path, body="## user\n{{ topic }}\n## system\nsys\n")
    expect_config_error(tmp_path, "'## system' then one '## user'")


def test_undeclared_placeholder(tmp_path: Path) -> None:
    write_prompt(tmp_path, body=BODY + "and {{ audience }}\n")
    expect_config_error(tmp_path, "not declared")


def test_invalid_placeholder_syntax(tmp_path: Path) -> None:
    write_prompt(tmp_path, body=BODY + "{{ topic.upper }}\n")
    expect_config_error(tmp_path, "invalid placeholder")


def test_unused_declared_variable(tmp_path: Path) -> None:
    write_prompt(tmp_path, variables="[topic, audience]")
    expect_config_error(tmp_path, "never used")


def test_extra_front_matter_key(tmp_path: Path) -> None:
    write_prompt(tmp_path, extra="model: gpt-9\n")
    expect_config_error(tmp_path, "failed validation")


def test_bad_yaml(tmp_path: Path) -> None:
    write_prompt(tmp_path, variables="[topic")
    expect_config_error(tmp_path, "not valid YAML")


def test_temperature_out_of_range(tmp_path: Path) -> None:
    write_prompt(tmp_path, extra="temperature: 3.5\n")
    expect_config_error(tmp_path, "failed validation")


def test_missing_front_matter(tmp_path: Path) -> None:
    path = tmp_path / "demo" / "greet.v1.md"
    path.parent.mkdir(parents=True)
    path.write_text(BODY, encoding="utf-8")
    expect_config_error(tmp_path, "front-matter")


def test_bad_path_layout(tmp_path: Path) -> None:
    write_prompt(tmp_path, rel="greet.v1.md")
    expect_config_error(tmp_path, "<domain>/<name>.v<N>.md")


def test_bad_filename(tmp_path: Path) -> None:
    write_prompt(tmp_path, rel="demo/greet.md")
    expect_config_error(tmp_path, "filename")


# ------------------------------------------------------------------ get()
def test_get_latest_vs_pinned(tmp_path: Path) -> None:
    write_prompt(tmp_path)
    write_prompt(tmp_path, "demo/greet.v2.md", version=2)
    registry = PromptRegistry.load(tmp_path)
    assert registry.get("demo/greet").version == 2
    assert registry.get("demo/greet@v1").version == 1
    assert registry.get("demo/greet@v2").ref == "demo/greet@v2"
    assert len(registry) == 2


@pytest.mark.parametrize("ref", ["demo/greet@v3", "demo/nope", "other/greet@v1"])
def test_get_unknown(tmp_path: Path, ref: str) -> None:
    write_prompt(tmp_path)
    registry = PromptRegistry.load(tmp_path)
    with pytest.raises(OmniGrowthError) as info:
        registry.get(ref)
    assert info.value.code is ErrorCode.NOT_FOUND


def test_get_malformed_ref(tmp_path: Path) -> None:
    write_prompt(tmp_path)
    with pytest.raises(OmniGrowthError) as info:
        PromptRegistry.load(tmp_path).get("demo/greet@1")
    assert info.value.code is ErrorCode.VALIDATION_FAILURE


# ------------------------------------------------------------------ render()
def test_render_substitutes(tmp_path: Path) -> None:
    write_prompt(tmp_path)
    rendered = PromptRegistry.load(tmp_path).get("demo/greet").render({"topic": 42})
    assert rendered.messages == [
        {"role": "system", "content": "You write about 42."},
        {"role": "user", "content": "Topic: 42"},
    ]
    assert rendered.prompt_ref == "demo/greet@v1"
    assert rendered.prompt_id == "demo/greet"
    assert rendered.version == 1


def test_render_rejects_missing_variable(tmp_path: Path) -> None:
    write_prompt(tmp_path)
    with pytest.raises(OmniGrowthError) as info:
        PromptRegistry.load(tmp_path).get("demo/greet").render({})
    assert info.value.code is ErrorCode.VALIDATION_FAILURE
    assert info.value.details["missing"] == ["topic"]


def test_render_rejects_extra_variable(tmp_path: Path) -> None:
    write_prompt(tmp_path)
    with pytest.raises(OmniGrowthError) as info:
        PromptRegistry.load(tmp_path).get("demo/greet").render({"topic": "a", "x": "b"})
    assert info.value.code is ErrorCode.VALIDATION_FAILURE
    assert info.value.details["undeclared"] == ["x"]


def test_render_does_not_reexpand_injected_placeholders(tmp_path: Path) -> None:
    write_prompt(tmp_path)
    rendered = PromptRegistry.load(tmp_path).get("demo/greet").render({"topic": "{{ topic }}"})
    assert rendered.messages[1]["content"] == "Topic: {{ topic }}"


# ------------------------------------------------------------------ hashing
def test_crlf_hash_equals_lf_hash(tmp_path: Path) -> None:
    lf_root, crlf_root = tmp_path / "lf", tmp_path / "crlf"
    write_prompt(lf_root)
    write_prompt(crlf_root, newline="\r\n")
    assert b"\r\n" in (crlf_root / "demo" / "greet.v1.md").read_bytes()
    lf = PromptRegistry.load(lf_root).get("demo/greet")
    crlf = PromptRegistry.load(crlf_root).get("demo/greet")
    assert lf.content_hash == crlf.content_hash
    assert len(lf.content_hash) == 64
    assert "\r" not in crlf.text
    assert crlf.user_template == lf.user_template


def test_input_hash_deterministic_and_input_sensitive(tmp_path: Path) -> None:
    write_prompt(tmp_path)
    write_prompt(tmp_path, "demo/greet.v2.md", version=2)
    registry = PromptRegistry.load(tmp_path)
    v1, v2 = registry.get("demo/greet@v1"), registry.get("demo/greet@v2")
    a = v1.render({"topic": "cats"}).input_hash
    assert a == v1.render({"topic": "cats"}).input_hash
    assert (
        a == PromptRegistry.load(tmp_path).get("demo/greet@v1").render({"topic": "cats"}).input_hash
    )
    assert a != v1.render({"topic": "dogs"}).input_hash
    assert a != v2.render({"topic": "cats"}).input_hash
    # str() conversion: 1 and "1" are the same input.
    assert v1.render({"topic": 1}).input_hash == v1.render({"topic": "1"}).input_hash


def test_bom_is_ignored(tmp_path: Path) -> None:
    plain, bom = tmp_path / "plain", tmp_path / "bom"
    write_prompt(plain)
    path = write_prompt(bom)
    path.write_bytes(codecs.BOM_UTF8 + path.read_bytes())
    assert (
        PromptRegistry.load(plain).get("demo/greet").content_hash
        == PromptRegistry.load(bom).get("demo/greet").content_hash
    )
