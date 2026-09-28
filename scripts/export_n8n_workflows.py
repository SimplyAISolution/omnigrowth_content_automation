"""Build the importable example n8n workflows in ``n8n/workflows/`` (Phase 12).

The JavaScript lives in ``n8n/code/*.js`` (reviewable and tested by
``tests/unit/test_n8n_workflows.py``); this script embeds it into n8n workflow JSON. The
workflows contain NO secrets and NO real notification channel: the "Notify" steps are No-Op
placeholders you replace with your own channel after review. They are exported inactive.

    uv run python scripts/export_n8n_workflows.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1] / "n8n"


def _code(name: str) -> str:
    return (ROOT / "code" / name).read_text(encoding="utf-8")


def _node(
    node_id: str,
    name: str,
    kind: str,
    version: float,
    position: tuple[int, int],
    parameters: dict[str, Any],
    **extra: Any,
) -> dict[str, Any]:
    return {
        "parameters": parameters,
        "id": node_id,
        "name": name,
        "type": kind,
        "typeVersion": version,
        "position": list(position),
        **extra,
    }


def _link(*targets: str) -> dict[str, Any]:
    return {"main": [[{"node": t, "type": "main", "index": 0}] for t in targets]}


def events_workflow() -> dict[str, Any]:
    notify = [
        ("Notify reviewers (replace me)", "Content needs human approval"),
        ("Notify on publication failure (replace me)", "Publication failed"),
        ("Ask an editor to reconcile (replace me)", "Publication outcome uncertain"),
        ("Ignore (duplicate, invalid or test)", "Nothing to do"),
    ]
    nodes = [
        _node(
            "a1f0c3e2-0000-4000-8000-000000000001",
            "Setup notes",
            "n8n-nodes-base.stickyNote",
            1,
            (-40, -260),
            {
                "content": (
                    "## OmniGrowth events\n"
                    "1. Set env: OMNIGROWTH_WEBHOOK_SECRET (the subscription secret), "
                    "NODE_FUNCTION_ALLOW_BUILTIN=crypto.\n"
                    "2. Create an OmniGrowth subscription to this Webhook's production URL.\n"
                    "3. Replace the No-Op 'Notify' steps with your channel, then activate.\n"
                    "Events inform people; approving and publishing happen in OmniGrowth only."
                ),
                "height": 220,
                "width": 460,
            },
        ),
        _node(
            "a1f0c3e2-0000-4000-8000-000000000002",
            "OmniGrowth event",
            "n8n-nodes-base.webhook",
            2,
            (0, 0),
            {
                "httpMethod": "POST",
                "path": "omnigrowth-events",
                "responseMode": "responseNode",
                "options": {"rawBody": True},
            },
            webhookId="omnigrowth-events",
        ),
        _node(
            "a1f0c3e2-0000-4000-8000-000000000003",
            "Verify signature and deduplicate",
            "n8n-nodes-base.code",
            2,
            (240, 0),
            {"jsCode": _code("verify-and-route.js")},
        ),
        _node(
            "a1f0c3e2-0000-4000-8000-000000000004",
            "Acknowledge",
            "n8n-nodes-base.respondToWebhook",
            1.1,
            (480, 0),
            {
                "respondWith": "json",
                "responseBody": "={{ { accepted: $json.httpStatus === 200 } }}",
                "options": {"responseCode": "={{ $json.httpStatus }}"},
            },
        ),
        _node(
            "a1f0c3e2-0000-4000-8000-000000000005",
            "Route by event",
            "n8n-nodes-base.switch",
            3,
            (720, 0),
            {"mode": "expression", "numberOutputs": 4, "output": "={{ $json.route }}"},
        ),
    ]
    for i, (name, note) in enumerate(notify):
        nodes.append(
            _node(
                f"a1f0c3e2-0000-4000-8000-00000000001{i}",
                name,
                "n8n-nodes-base.noOp",
                1,
                (980, -180 + i * 120),
                {},
                notes=f"{note}. Message: {{{{ $json.message }}}}",
            )
        )
    return {
        "name": "OmniGrowth — event notifications (example)",
        "nodes": nodes,
        "connections": {
            "OmniGrowth event": _link("Verify signature and deduplicate"),
            "Verify signature and deduplicate": _link("Acknowledge"),
            "Acknowledge": _link("Route by event"),
            "Route by event": {
                "main": [[{"node": name, "type": "main", "index": 0}] for name, _ in notify]
            },
        },
        "active": False,
        "settings": {"executionOrder": "v1"},
        "pinData": {},
        "meta": {"templateCredsSetupCompleted": False},
    }


def inbound_workflow() -> dict[str, Any]:
    nodes = [
        _node(
            "b2e1d4f3-0000-4000-8000-000000000001",
            "Setup notes",
            "n8n-nodes-base.stickyNote",
            1,
            (-40, -260),
            {
                "content": (
                    "## Send a text source to OmniGrowth\n"
                    "Env: OMNIGROWTH_API_URL, OMNIGROWTH_ENDPOINT_KEY, OMNIGROWTH_INBOUND_SECRET, "
                    "OMNIGROWTH_BRAND_ID, NODE_FUNCTION_ALLOW_BUILTIN=crypto.\n"
                    "The endpoint can only ingest sources / start planning for its brands."
                ),
                "height": 200,
                "width": 460,
            },
        ),
        _node(
            "b2e1d4f3-0000-4000-8000-000000000002",
            "Run manually",
            "n8n-nodes-base.manualTrigger",
            1,
            (0, 0),
            {},
        ),
        _node(
            "b2e1d4f3-0000-4000-8000-000000000003",
            "Example text",
            "n8n-nodes-base.set",
            3.4,
            (220, 0),
            {
                "assignments": {
                    "assignments": [
                        {
                            "id": "title",
                            "name": "title",
                            "type": "string",
                            "value": "Notes from n8n",
                        },
                        {
                            "id": "content",
                            "name": "content",
                            "type": "string",
                            "value": (
                                "Replace this with the text to send "
                                "(an article, notes, a transcript)."
                            ),
                        },
                    ]
                },
                "options": {},
            },
        ),
        _node(
            "b2e1d4f3-0000-4000-8000-000000000004",
            "Sign request",
            "n8n-nodes-base.code",
            2,
            (440, 0),
            {"jsCode": _code("sign-inbound.js")},
        ),
        _node(
            "b2e1d4f3-0000-4000-8000-000000000005",
            "Send to OmniGrowth",
            "n8n-nodes-base.httpRequest",
            4.2,
            (660, 0),
            {
                "method": "POST",
                "url": (
                    "={{ $env.OMNIGROWTH_API_URL }}/api/v1/webhooks/inbound/"
                    "{{ $env.OMNIGROWTH_ENDPOINT_KEY }}"
                ),
                "sendHeaders": True,
                "headerParameters": {
                    "parameters": [
                        {"name": "X-OmniGrowth-Timestamp", "value": "={{ $json.timestamp }}"},
                        {"name": "X-OmniGrowth-Signature", "value": "={{ $json.signature }}"},
                        {"name": "X-OmniGrowth-Delivery-ID", "value": "={{ $json.deliveryId }}"},
                    ]
                },
                "sendBody": True,
                "contentType": "raw",
                "rawContentType": "application/json",
                "body": "={{ $json.body }}",
                "options": {"timeout": 10000},
            },
        ),
    ]
    return {
        "name": "OmniGrowth — send a text source (example)",
        "nodes": nodes,
        "connections": {
            "Run manually": _link("Example text"),
            "Example text": _link("Sign request"),
            "Sign request": _link("Send to OmniGrowth"),
        },
        "active": False,
        "settings": {"executionOrder": "v1"},
        "pinData": {},
        "meta": {"templateCredsSetupCompleted": False},
    }


WORKFLOWS = {
    "omnigrowth-event-notifications.json": events_workflow,
    "omnigrowth-send-text-source.json": inbound_workflow,
}


def render(builder: Any) -> str:
    return json.dumps(builder(), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    for name, builder in WORKFLOWS.items():
        (ROOT / "workflows" / name).write_text(render(builder), encoding="utf-8", newline="\n")
        print(f"wrote n8n/workflows/{name}")


if __name__ == "__main__":
    main()
