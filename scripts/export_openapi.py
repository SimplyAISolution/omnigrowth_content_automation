"""Write the API's OpenAPI document to ``frontend/src/api/openapi.json`` (API_PLAN §1.1).

The frontend's typed client is generated from this snapshot (``npm run gen:api``), and
``tests/unit/test_openapi_snapshot.py`` fails when the app and the snapshot disagree, so every
contract change is explicit in review.

    uv run python scripts/export_openapi.py
"""

from __future__ import annotations

import json
from pathlib import Path

from omnigrowth.api.app import create_app

SNAPSHOT = Path(__file__).resolve().parents[1] / "frontend" / "src" / "api" / "openapi.json"


def render() -> str:
    return json.dumps(create_app().openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {SNAPSHOT}")


if __name__ == "__main__":
    main()
