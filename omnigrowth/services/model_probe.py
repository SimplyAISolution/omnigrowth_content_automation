"""Model health + capability probe (ADR-0002 #9, A1): ``uv run poe models-probe``.

For every model configured in ``config/models.yaml`` it reports provider, locality (as the
router classifies it — e.g. ``*-cloud`` models behind Ollama are REMOTE), whether the
provider lists the model, and the best structured-output mode found by running the
``system/structured_probe`` prompt. REMOTE models are only probed with ``--include-remote``
(probing costs money and sends the probe prompt off the machine).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path

from omnigrowth.core import aio
from omnigrowth.core.enums import ModelLocality
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.logging import configure_logging
from omnigrowth.core.settings import Settings, get_settings
from omnigrowth.prompts import PromptRegistry
from omnigrowth.providers.factory import build_router
from omnigrowth.providers.probe import ProbeOutput
from omnigrowth.providers.router import ModelRouter, ResolvedModel
from omnigrowth.providers.structured import DEFAULT_CAPABILITIES, probe_structured_mode

log = logging.getLogger(__name__)

PROBE_PROMPT = "system/structured_probe"


@dataclass(frozen=True)
class ModelProbe:
    roles: list[str]
    provider: str
    model: str
    locality: str
    provider_built: bool
    listed: bool | None
    structured_mode: str | None
    status: str  # OK | UNREACHABLE | NOT_LISTED | NO_STRUCTURED_MODE | SKIPPED_REMOTE | ...


def _unique_models(router: ModelRouter) -> dict[tuple[str, str], tuple[ResolvedModel, list[str]]]:
    seen: dict[tuple[str, str], tuple[ResolvedModel, list[str]]] = {}
    for role, candidates in router.config.roles.items():
        for candidate in candidates:
            key = (candidate.provider, candidate.model)
            if key not in seen:
                resolved = ResolvedModel(
                    role=role,
                    provider_name=candidate.provider,
                    model=candidate.model,
                    locality=router.config.locality_of(candidate),
                    input_cost_per_mtok=candidate.input_cost_per_mtok,
                    output_cost_per_mtok=candidate.output_cost_per_mtok,
                )
                seen[key] = (resolved, [])
            seen[key][1].append(str(role))
    return seen


async def probe_models(
    router: ModelRouter, *, include_remote: bool = False, structured: bool = True
) -> list[ModelProbe]:
    prompt = (
        PromptRegistry.load()
        .get(PROBE_PROMPT)
        .render({"topic": "Local AI agents versus cloud-hosted chatbots"})
    )
    listed_by_provider: dict[str, list[str] | None] = {}
    results: list[ModelProbe] = []
    for (provider_name, model_name), (model, roles) in _unique_models(router).items():
        built = provider_name in router.providers
        make = partial(
            ModelProbe,
            roles=roles,
            provider=provider_name,
            model=model_name,
            locality=str(model.locality),
            provider_built=built,
        )
        if not built:
            results.append(make(listed=None, structured_mode=None, status="DISABLED"))
            continue
        if provider_name not in listed_by_provider:
            try:
                listed_by_provider[provider_name] = await router.providers[
                    provider_name
                ].list_models()
            except OmniGrowthError as exc:
                if exc.code not in (ErrorCode.MODEL_UNAVAILABLE, ErrorCode.MODEL_TIMEOUT):
                    raise
                listed_by_provider[provider_name] = None
        listed_models = listed_by_provider[provider_name]
        if listed_models is None:
            results.append(make(listed=None, structured_mode=None, status="UNREACHABLE"))
            continue
        listed = model_name in listed_models
        if not listed:
            results.append(make(listed=False, structured_mode=None, status="NOT_LISTED"))
            continue
        if not structured or model.role.value == "EMBEDDING_LOCAL":
            results.append(make(listed=True, structured_mode=None, status="OK"))
            continue
        if model.locality is ModelLocality.REMOTE and not include_remote:
            results.append(make(listed=True, structured_mode=None, status="SKIPPED_REMOTE"))
            continue
        try:
            mode = await probe_structured_mode(
                ProbeOutput, prompt, router=router, model=model, capabilities=DEFAULT_CAPABILITIES
            )
        except OmniGrowthError as exc:
            if exc.code not in (
                ErrorCode.MODEL_UNAVAILABLE,
                ErrorCode.MODEL_TIMEOUT,
                ErrorCode.PROVIDER_RATE_LIMIT,
            ):
                raise
            results.append(make(listed=True, structured_mode=None, status=str(exc.code)))
            continue
        results.append(
            make(
                listed=True,
                structured_mode=str(mode) if mode else None,
                status="OK" if mode else "NO_STRUCTURED_MODE",
            )
        )
    return results


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Probe configured models (ADR-0002 #9)")
    parser.add_argument("--include-remote", action="store_true")
    parser.add_argument("--no-structured", action="store_true", help="only check reachability")
    parser.add_argument("--output", type=Path, help="write the JSON report to this file")
    args = parser.parse_args(argv)
    settings: Settings = get_settings()
    configure_logging(settings.log_level, json_output=False)

    async def _run() -> list[ModelProbe]:
        router = build_router(settings)
        try:
            return await probe_models(
                router, include_remote=args.include_remote, structured=not args.no_structured
            )
        finally:
            await router.aclose()

    results = aio.run(_run)
    report = json.dumps([asdict(r) for r in results], indent=2) + "\n"
    if args.output is not None:
        args.output.write_text(report, encoding="utf-8")
    for r in results:
        log.info(
            "model probe",
            extra={
                "provider": r.provider,
                "model": r.model,
                "locality": r.locality,
                "status": r.status,
                "structured_mode": r.structured_mode,
            },
        )
    if args.output is None:
        sys.stdout.write(report)


if __name__ == "__main__":
    main()
