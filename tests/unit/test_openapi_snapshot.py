"""The committed OpenAPI snapshot (source of the frontend's typed client) matches the app."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_openapi_snapshot_is_current() -> None:
    spec = importlib.util.spec_from_file_location(
        "export_openapi", REPO / "scripts" / "export_openapi.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    committed = module.SNAPSHOT.read_text(encoding="utf-8")
    assert committed == module.render(), (
        "frontend/src/api/openapi.json is stale: run "
        "`uv run python scripts/export_openapi.py` then `npm --prefix frontend run gen:api`"
    )
