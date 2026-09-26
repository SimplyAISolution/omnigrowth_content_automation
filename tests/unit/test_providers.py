"""Provider layer: OpenAI-compatible client, config, router, factory, FakeProvider."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from omnigrowth.core.enums import ModelLocality, ModelRole, PrivacyClass, RoutingProfile
from omnigrowth.core.errors import ConfigurationError, ErrorCode, OmniGrowthError
from omnigrowth.core.settings import Settings
from omnigrowth.providers.base import ChatRequest, UnsupportedResponseFormatError
from omnigrowth.providers.config import ModelsConfig, load_models_config
from omnigrowth.providers.factory import build_providers, resolve_base_url
from omnigrowth.providers.fake import FakeProvider, FixtureMissingError
from omnigrowth.providers.openai_compatible import OpenAICompatibleProvider
from omnigrowth.providers.router import ModelRouter

REPO = Path(__file__).resolve().parents[2]


def settings(**env: Any) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[call-arg]


def completion(content: str, prompt_tokens: int = 10, completion_tokens: int = 5) -> dict[str, Any]:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def provider_with(handler: Any, *, api_key: str | None = None) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        "lmstudio",
        "http://llm.test/v1",
        api_key=api_key,
        transport=httpx.MockTransport(handler),
    )


REQ = ChatRequest(model="m", messages=[{"role": "user", "content": "hi"}])


# ------------------------------------------------------------------ OpenAI-compatible client
async def test_chat_success_parses_text_and_usage() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=completion('{"a": 1}', 12, 3))

    result = await provider_with(handler).chat(REQ)
    assert result.text == '{"a": 1}'
    assert (result.input_tokens, result.output_tokens) == (12, 3)
    assert seen["body"]["model"] == "m" and seen["body"]["stream"] is False
    assert seen["auth"] is None  # local provider sends no key


async def test_api_key_sent_only_when_configured() -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=completion("{}"))

    await provider_with(handler, api_key="sk-test").chat(REQ)
    assert seen["auth"] == "Bearer sk-test"


async def test_provider_offline_is_model_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(OmniGrowthError) as exc:
        await provider_with(handler).chat(REQ)
    assert exc.value.code is ErrorCode.MODEL_UNAVAILABLE
    assert exc.value.retryable


async def test_timeout_is_model_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(OmniGrowthError) as exc:
        await provider_with(handler).chat(REQ)
    assert exc.value.code is ErrorCode.MODEL_TIMEOUT


async def test_rate_limit_carries_retry_after() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "7"}, json={"error": "slow down"})

    with pytest.raises(OmniGrowthError) as exc:
        await provider_with(handler).chat(REQ)
    assert exc.value.code is ErrorCode.PROVIDER_RATE_LIMIT
    assert exc.value.details["retry_after_s"] == 7.0


async def test_rejected_response_format_is_signalled() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "response_format json_schema not supported"})

    req = ChatRequest(model="m", messages=[], response_format={"type": "json_schema"})
    with pytest.raises(UnsupportedResponseFormatError) as exc:
        await provider_with(handler).chat(req)
    assert exc.value.mode == "json_schema"


@pytest.mark.parametrize("status", [404, 500, 503])
async def test_server_errors_are_model_unavailable(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="boom")

    with pytest.raises(OmniGrowthError) as exc:
        await provider_with(handler).chat(REQ)
    assert exc.value.code is ErrorCode.MODEL_UNAVAILABLE


async def test_malformed_envelope_is_llm_output_invalid() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(OmniGrowthError) as exc:
        await provider_with(handler).chat(REQ)
    assert exc.value.code is ErrorCode.LLM_OUTPUT_INVALID
    assert "raw" in exc.value.details


async def test_list_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]})

    assert await provider_with(handler).list_models() == ["a", "b"]


# ------------------------------------------------------------------ config
def test_repo_models_yaml_is_valid() -> None:
    config = load_models_config(REPO / "config")
    assert ModelRole.CHEAP_LOCAL in config.roles
    assert config.providers["cloud"].locality is ModelLocality.REMOTE


def make_config(roles: dict[str, list[dict[str, str]]]) -> ModelsConfig:
    return ModelsConfig.model_validate(
        {
            "presets": {
                "lmstudio": {
                    "base_url_host": "http://h:1234/v1",
                    "base_url_docker": "http://d:1234/v1",
                },
                "ollama": {
                    "base_url_host": "http://h:11434/v1",
                    "base_url_docker": "http://d:11434/v1",
                },
            },
            "remote_patterns": ["*-cloud", "*:cloud"],
            "providers": {
                "lmstudio": {"locality": "LOCAL", "preset": "lmstudio"},
                "ollama": {"locality": "LOCAL", "preset": "ollama", "max_concurrency": 2},
                "cloud": {
                    "locality": "REMOTE",
                    "base_url": "https://cloud.test/v1",
                    "uses_cloud_key": True,
                },
            },
            "roles": roles,
        }
    )


def test_cloud_named_model_behind_local_preset_is_remote() -> None:
    config = make_config(
        {"CHEAP_LOCAL": [{"provider": "ollama", "model": "deepseek-v3.1:671b-cloud"}]}
    )
    candidate = config.roles[ModelRole.CHEAP_LOCAL][0]
    assert config.locality_of(candidate) is ModelLocality.REMOTE


def test_unknown_provider_reference_rejected(tmp_path: Path) -> None:
    (tmp_path / "models.yaml").write_text(
        "presets: {}\nproviders: {}\nroles:\n  CHEAP_LOCAL:\n    - {provider: nope, model: x}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError):
        load_models_config(tmp_path)


def test_missing_models_yaml_is_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_models_config(tmp_path)


# ------------------------------------------------------------------ router
MIXED_ROLES = {
    "CHEAP_LOCAL": [
        {"provider": "cloud", "model": "big-remote"},
        {"provider": "lmstudio", "model": "local-a"},
        {"provider": "ollama", "model": "qwen:cloud"},
        {"provider": "ollama", "model": "local-b"},
    ]
}


def router_for(roles: dict[str, list[dict[str, str]]], providers: tuple[str, ...]) -> ModelRouter:
    fakes = {name: FakeProvider(name, script=[]) for name in providers}
    return ModelRouter(make_config(roles), dict(fakes))


def names(router: ModelRouter, privacy: PrivacyClass, profile: RoutingProfile) -> list[str]:
    return [m.model for m in router.candidates(ModelRole.CHEAP_LOCAL, privacy, profile)]


def test_local_only_never_returns_remote() -> None:
    router = router_for(MIXED_ROLES, ("lmstudio", "ollama", "cloud"))
    assert names(router, PrivacyClass.PUBLIC, RoutingProfile.LOCAL_ONLY) == ["local-a", "local-b"]


def test_balanced_is_local_first_then_remote() -> None:
    router = router_for(MIXED_ROLES, ("lmstudio", "ollama", "cloud"))
    assert names(router, PrivacyClass.PUBLIC, RoutingProfile.BALANCED) == [
        "local-a",
        "local-b",
        "big-remote",
        "qwen:cloud",
    ]


def test_quality_first_is_remote_first() -> None:
    router = router_for(MIXED_ROLES, ("lmstudio", "ollama", "cloud"))
    assert names(router, PrivacyClass.PUBLIC, RoutingProfile.QUALITY_FIRST)[:2] == [
        "big-remote",
        "qwen:cloud",
    ]


@pytest.mark.parametrize("profile", list(RoutingProfile))
def test_private_tasks_never_route_remote(profile: RoutingProfile) -> None:
    router = router_for(MIXED_ROLES, ("lmstudio", "ollama", "cloud"))
    for model in router.candidates(ModelRole.CHEAP_LOCAL, PrivacyClass.PRIVATE, profile):
        assert model.locality is ModelLocality.LOCAL


def test_private_with_only_remote_is_privacy_violation() -> None:
    router = router_for({"CHEAP_LOCAL": [{"provider": "cloud", "model": "r"}]}, ("cloud",))
    with pytest.raises(OmniGrowthError) as exc:
        router.candidates(ModelRole.CHEAP_LOCAL, PrivacyClass.PRIVATE, RoutingProfile.BALANCED)
    assert exc.value.code is ErrorCode.PRIVACY_VIOLATION


def test_unbuilt_providers_are_skipped_and_empty_is_unavailable() -> None:
    router = router_for(MIXED_ROLES, ("cloud",))  # no local provider built
    with pytest.raises(OmniGrowthError) as exc:
        router.candidates(ModelRole.CHEAP_LOCAL, PrivacyClass.PUBLIC, RoutingProfile.LOCAL_ONLY)
    assert exc.value.code is ErrorCode.MODEL_UNAVAILABLE


def test_remote_cost_estimate_and_local_is_free() -> None:
    config = make_config({"CHEAP_LOCAL": MIXED_ROLES["CHEAP_LOCAL"]})
    router = ModelRouter(config, {n: FakeProvider(n, script=[]) for n in ("lmstudio", "cloud")})
    local = router.candidates(ModelRole.CHEAP_LOCAL, PrivacyClass.PUBLIC, RoutingProfile.LOCAL_ONLY)
    assert local[0].estimate_cost(1000, 1000) == 0


# ------------------------------------------------------------------ factory
def test_cloud_provider_disabled_without_omnigrowth_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-generic-000000000000000000")
    config = make_config({})
    built = build_providers(config, settings())
    assert set(built) == {"lmstudio", "ollama"}


def test_cloud_provider_enabled_with_omnigrowth_key() -> None:
    config = make_config({})
    built = build_providers(config, settings(OMNIGROWTH_CLOUD_API_KEY="sk-og-0000000000000000"))
    assert "cloud" in built


def test_env_url_overrides_only_the_configured_preset() -> None:
    config = make_config({})
    s = settings(
        OMNIGROWTH_LOCAL_LLM_URL="http://10.0.0.9:9/v1", OMNIGROWTH_LOCAL_LLM_PRESET="ollama"
    )
    assert (
        resolve_base_url("ollama", config.providers["ollama"], config, s) == "http://10.0.0.9:9/v1"
    )
    assert (
        resolve_base_url("lmstudio", config.providers["lmstudio"], config, s) == "http://h:1234/v1"
    )


def test_docker_runtime_uses_docker_preset_urls() -> None:
    config = make_config({})
    s = settings(OMNIGROWTH_RUNTIME="docker")
    assert (
        resolve_base_url("lmstudio", config.providers["lmstudio"], config, s) == "http://d:1234/v1"
    )


# ------------------------------------------------------------------ FakeProvider fixtures
def fixture_request(prompt_id: str = "system/probe", h: str = "a" * 64) -> ChatRequest:
    return ChatRequest(
        model="m",
        messages=[],
        meta={"prompt_id": prompt_id, "prompt_version": 1, "input_hash": h},
    )


async def test_fake_provider_missing_fixture_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(FixtureMissingError):
        await FakeProvider(fixtures_dir=tmp_path).chat(fixture_request())


async def test_fake_provider_replays_fixture_in_order(tmp_path: Path) -> None:
    path = tmp_path / "system" / "probe" / "v1" / f"{'a' * 64}.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"responses": [{"content": "first"}, {"error": "MODEL_TIMEOUT"}]}),
        encoding="utf-8",
    )
    fake = FakeProvider(fixtures_dir=tmp_path)
    assert (await fake.chat(fixture_request())).text == "first"
    with pytest.raises(OmniGrowthError) as exc:
        await fake.chat(fixture_request())
    assert exc.value.code is ErrorCode.MODEL_TIMEOUT
    with pytest.raises(FixtureMissingError):  # ran past the recorded responses
        await fake.chat(fixture_request())


def test_private_under_local_only_with_only_remote_is_privacy_violation() -> None:
    router = router_for({"CHEAP_LOCAL": [{"provider": "cloud", "model": "r"}]}, ("cloud",))
    with pytest.raises(OmniGrowthError) as exc:
        router.candidates(ModelRole.CHEAP_LOCAL, PrivacyClass.PRIVATE, RoutingProfile.LOCAL_ONLY)
    assert exc.value.code is ErrorCode.PRIVACY_VIOLATION


def test_local_providers_ignore_system_proxies() -> None:
    built = build_providers(
        make_config({}), settings(OMNIGROWTH_CLOUD_API_KEY="sk-og-00000000000000")
    )
    assert built["lmstudio"]._client._trust_env is False  # type: ignore[attr-defined]
    assert built["cloud"]._client._trust_env is True  # type: ignore[attr-defined]


@pytest.mark.parametrize("profile", list(RoutingProfile))
def test_repo_config_never_reaches_a_cloud_model(profile: RoutingProfile) -> None:
    """No silent cloud fallback: even with a cloud key and QUALITY_FIRST, the shipped
    config builds no remote provider and routes every role to LOCAL models only."""
    config = load_models_config(REPO / "config")
    built = build_providers(config, settings(OMNIGROWTH_CLOUD_API_KEY="sk-og-0000000000000000"))
    assert "cloud" not in built  # no base_url configured -> never built
    router = ModelRouter(config, {name: FakeProvider(name, script=[]) for name in built})
    for role in config.roles:
        for model in router.candidates(role, PrivacyClass.PUBLIC, profile):
            assert model.locality is ModelLocality.LOCAL, (role, model)
