"""Versioned prompt files: loader, validation and plain-substitution rendering.

Files live at ``omnigrowth/prompts/<domain>/<name>.v<N>.md`` (AGENTS.md §4). Each file is
YAML front-matter between leading ``---`` lines, followed by exactly two sections introduced
by the lines ``## system`` and ``## user`` (in that order).

Placeholders are ``{{ name }}``. Rendering is a single-pass substitution: no Jinja, no eval,
no attribute access, and substituted values are never re-scanned.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from omnigrowth.core.enums import ModelRole, PrivacyClass
from omnigrowth.core.errors import ErrorCode, OmniGrowthError

PROMPTS_DIR = Path(__file__).resolve().parent

_SLUG = r"[a-z][a-z0-9_]*"
_FILENAME_RE = re.compile(rf"^(?P<name>{_SLUG})\.v(?P<version>[1-9][0-9]*)\.md$")
_DOMAIN_RE = re.compile(rf"^{_SLUG}$")
_REF_RE = re.compile(rf"^(?P<id>{_SLUG}/{_SLUG})(?:@v(?P<version>[1-9][0-9]*))?$")
# Any ``{{ ... }}`` run; its inner text must be a valid identifier (checked at load).
_ANY_PLACEHOLDER_RE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
_VAR_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
_SYSTEM_MARKER = "## system"
_USER_MARKER = "## user"
_FENCE = "---"
_BOM = chr(0xFEFF)


class PromptFrontMatter(BaseModel):
    """Validated YAML front-matter of a prompt file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    version: int = Field(ge=1)
    role: ModelRole
    privacy_class: PrivacyClass
    description: str
    variables: list[str]
    output_schema: str | None = None
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)


@dataclass(frozen=True)
class RenderedPrompt:
    prompt_ref: str
    prompt_id: str
    version: int
    content_hash: str
    messages: list[dict[str, str]]
    input_hash: str
    # Carried from front-matter so generate_structured can enforce them (ADR-0002 #4, §52).
    role: ModelRole
    privacy_class: PrivacyClass
    temperature: float
    max_tokens: int | None


@dataclass(frozen=True)
class Prompt:
    domain: str
    name: str
    version: int
    front_matter: PromptFrontMatter
    system_template: str
    user_template: str
    content_hash: str
    text: str  # full file text, LF-normalised (stored as prompt_versions.body)

    @property
    def id(self) -> str:
        return f"{self.domain}/{self.name}"

    @property
    def ref(self) -> str:
        return f"{self.id}@v{self.version}"

    def render(self, variables: Mapping[str, object]) -> RenderedPrompt:
        declared = set(self.front_matter.variables)
        given = set(variables)
        missing = sorted(declared - given)
        extra = sorted(given - declared)
        if missing or extra:
            raise OmniGrowthError(
                ErrorCode.VALIDATION_FAILURE,
                f"prompt {self.ref}: variables mismatch",
                details={"prompt": self.ref, "missing": missing, "undeclared": extra},
            )
        values = {key: str(value) for key, value in variables.items()}

        def _sub(match: re.Match[str]) -> str:
            return values[match.group(1)]

        messages = [
            {"role": "system", "content": _PLACEHOLDER_RE.sub(_sub, self.system_template)},
            {"role": "user", "content": _PLACEHOLDER_RE.sub(_sub, self.user_template)},
        ]
        return RenderedPrompt(
            prompt_ref=self.ref,
            prompt_id=self.id,
            version=self.version,
            content_hash=self.content_hash,
            messages=messages,
            input_hash=compute_input_hash(self.ref, values),
            role=self.front_matter.role,
            privacy_class=self.front_matter.privacy_class,
            temperature=self.front_matter.temperature,
            max_tokens=self.front_matter.max_tokens,
        )


def compute_input_hash(prompt_ref: str, variables: Mapping[str, str]) -> str:
    """Deterministic FakeProvider fixture key component (SPEC_AMENDMENTS A8)."""
    payload = json.dumps(
        {"prompt": prompt_ref, "variables": dict(variables)}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalise_text(raw: str) -> str:
    """Strip a UTF-8 BOM and normalise CRLF / CR line endings to LF (Windows checkouts)."""
    return raw.removeprefix(_BOM).replace("\r\n", "\n").replace("\r", "\n")


def _config_error(path: Path, reason: str, **details: Any) -> OmniGrowthError:
    return OmniGrowthError(
        ErrorCode.CONFIGURATION_ERROR,
        f"invalid prompt file {path.as_posix()}: {reason}",
        details={"file": path.as_posix(), "reason": reason, **details},
    )


def _split_front_matter(text: str, path: Path) -> tuple[str, str]:
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != _FENCE:
        raise _config_error(path, "file must start with a '---' front-matter line")
    for idx in range(1, len(lines)):
        if lines[idx].rstrip() == _FENCE:
            return "\n".join(lines[1:idx]), "\n".join(lines[idx + 1 :])
    raise _config_error(path, "unterminated front-matter (missing closing '---')")


def _split_sections(body: str, path: Path) -> tuple[str, str]:
    lines = body.split("\n")
    markers = [
        (idx, line.rstrip())
        for idx, line in enumerate(lines)
        if line.rstrip() in (_SYSTEM_MARKER, _USER_MARKER)
    ]
    if [m for _, m in markers] != [_SYSTEM_MARKER, _USER_MARKER]:
        raise _config_error(
            path, "body must contain exactly one '## system' then one '## user' section"
        )
    (sys_idx, _), (user_idx, _) = markers
    if any(line.strip() for line in lines[:sys_idx]):
        raise _config_error(path, "unexpected text before '## system'")
    system = "\n".join(lines[sys_idx + 1 : user_idx]).strip()
    user = "\n".join(lines[user_idx + 1 :]).strip()
    if not system or not user:
        raise _config_error(path, "'## system' and '## user' sections must be non-empty")
    return system, user


def _placeholders(template: str, path: Path) -> set[str]:
    names: set[str] = set()
    for match in _ANY_PLACEHOLDER_RE.finditer(template):
        inner = match.group(1).strip()
        if not _VAR_NAME_RE.match(inner):
            raise _config_error(path, f"invalid placeholder {match.group(0)!r}")
        names.add(inner)
    return names


def parse_prompt_file(path: Path, root: Path) -> Prompt:
    """Parse and validate one prompt file. Raises CONFIGURATION_ERROR naming the file."""
    rel = path.relative_to(root)
    if len(rel.parts) != 2 or not _DOMAIN_RE.match(rel.parts[0]):
        raise _config_error(rel, "path must be <domain>/<name>.v<N>.md")
    file_match = _FILENAME_RE.match(rel.parts[1])
    if file_match is None:
        raise _config_error(rel, "filename must be <name>.v<N>.md")
    domain, name = rel.parts[0], file_match.group("name")
    version = int(file_match.group("version"))

    try:
        text = normalise_text(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise _config_error(rel, f"unreadable ({type(exc).__name__})") from exc

    fm_text, body = _split_front_matter(text, rel)
    try:
        raw_fm = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        raise _config_error(rel, "front-matter is not valid YAML") from exc
    if not isinstance(raw_fm, dict):
        raise _config_error(rel, "front-matter must be a YAML mapping")
    try:
        front_matter = PromptFrontMatter.model_validate(raw_fm)
    except ValidationError as exc:
        errors = [
            {"loc": ".".join(str(p) for p in err["loc"]), "msg": err["msg"]} for err in exc.errors()
        ]
        raise _config_error(rel, "front-matter failed validation", errors=errors) from exc

    expected_id = f"{domain}/{name}"
    if front_matter.id != expected_id:
        raise _config_error(rel, f"id {front_matter.id!r} != path-derived {expected_id!r}")
    if front_matter.version != version:
        raise _config_error(rel, f"version {front_matter.version} != filename version {version}")
    declared = front_matter.variables
    if len(set(declared)) != len(declared):
        raise _config_error(rel, "duplicate names in 'variables'")
    bad_names = [v for v in declared if not _VAR_NAME_RE.match(v)]
    if bad_names:
        raise _config_error(rel, f"invalid variable names {bad_names}")

    system, user = _split_sections(body, rel)
    used = _placeholders(system, rel) | _placeholders(user, rel)
    undeclared = sorted(used - set(declared))
    unused = sorted(set(declared) - used)
    if undeclared:
        raise _config_error(rel, f"placeholders not declared in 'variables': {undeclared}")
    if unused:
        raise _config_error(rel, f"declared variables never used in the body: {unused}")

    return Prompt(
        domain=domain,
        name=name,
        version=version,
        front_matter=front_matter,
        system_template=system,
        user_template=user,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text=text,
    )


class PromptRegistry:
    """In-memory index of validated prompt files, keyed by id and version."""

    def __init__(self, prompts: Mapping[str, Mapping[int, Prompt]]) -> None:
        self._prompts: dict[str, dict[int, Prompt]] = {k: dict(v) for k, v in prompts.items()}

    @classmethod
    def load(cls, root: Path | None = None) -> PromptRegistry:
        base = (root or PROMPTS_DIR).resolve()
        if not base.is_dir():
            raise OmniGrowthError(
                ErrorCode.CONFIGURATION_ERROR,
                f"prompts directory not found: {base.as_posix()}",
                details={"root": base.as_posix()},
            )
        index: dict[str, dict[int, Prompt]] = {}
        for path in sorted(base.rglob("*.md")):
            prompt = parse_prompt_file(path, base)
            versions = index.setdefault(prompt.id, {})
            if prompt.version in versions:
                raise _config_error(
                    path.relative_to(base), f"duplicate prompt {prompt.ref}", ref=prompt.ref
                )
            versions[prompt.version] = prompt
        return cls(index)

    def get(self, ref: str) -> Prompt:
        """Resolve ``domain/name@vN`` (pinned) or ``domain/name`` (latest version)."""
        match = _REF_RE.match(ref)
        if match is None:
            raise OmniGrowthError(
                ErrorCode.VALIDATION_FAILURE,
                f"malformed prompt ref {ref!r} (expected domain/name or domain/name@vN)",
                details={"ref": ref},
            )
        versions = self._prompts.get(match.group("id"))
        if versions:
            wanted = match.group("version")
            version = int(wanted) if wanted else max(versions)
            if version in versions:
                return versions[version]
        raise OmniGrowthError(ErrorCode.NOT_FOUND, f"unknown prompt {ref!r}", details={"ref": ref})

    def __iter__(self) -> Iterator[Prompt]:
        for prompt_id in sorted(self._prompts):
            versions = self._prompts[prompt_id]
            for version in sorted(versions):
                yield versions[version]

    def __len__(self) -> int:
        return sum(len(v) for v in self._prompts.values())

    def refs(self) -> list[str]:
        return [p.ref for p in self]
