"""The example n8n workflows (Phase 12): current, inert, secret-free, and their JavaScript
verifies/signs exactly like OmniGrowth (run in Node against Python-signed requests)."""

from __future__ import annotations

import base64
import importlib.util
import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from omnigrowth.security import webhooks as sig

REPO = Path(__file__).resolve().parents[2]
N8N = REPO / "n8n"
NODE = shutil.which("node")
ALLOWED_NODE_TYPES = {
    "n8n-nodes-base.stickyNote",
    "n8n-nodes-base.webhook",
    "n8n-nodes-base.code",
    "n8n-nodes-base.respondToWebhook",
    "n8n-nodes-base.switch",
    "n8n-nodes-base.noOp",
    "n8n-nodes-base.manualTrigger",
    "n8n-nodes-base.set",
    "n8n-nodes-base.httpRequest",
}


def _module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "export_n8n", REPO / "scripts" / "export_n8n_workflows.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_workflow_files_are_generated_from_the_reviewed_code() -> None:
    module = _module()
    for name, builder in module.WORKFLOWS.items():
        committed = (N8N / "workflows" / name).read_text(encoding="utf-8")
        assert committed == module.render(builder), (
            f"stale {name}: run scripts/export_n8n_workflows.py"
        )


@pytest.mark.parametrize(
    "name", ["omnigrowth-event-notifications.json", "omnigrowth-send-text-source.json"]
)
def test_workflows_are_inactive_secret_free_and_send_nothing_real(name: str) -> None:
    raw = (N8N / "workflows" / name).read_text(encoding="utf-8")
    flow = json.loads(raw)
    assert flow["active"] is False
    names = {n["name"] for n in flow["nodes"]}
    assert len(names) == len(flow["nodes"])
    for source, conn in flow["connections"].items():
        assert source in names
        for branch in conn["main"]:
            for target in branch:
                assert target["node"] in names
    assert {n["type"] for n in flow["nodes"]} <= ALLOWED_NODE_TYPES  # no email/chat/social nodes
    assert "whsec_" not in raw and "og_" not in raw.replace("og_ui", "")
    for node in flow["nodes"]:
        assert "credentials" not in node
    if name == "omnigrowth-send-text-source.json":
        url = next(n for n in flow["nodes"] if n["type"].endswith("httpRequest"))["parameters"][
            "url"
        ]
        assert url.startswith("={{ $env.OMNIGROWTH_API_URL }}")  # no hard-coded host


# ------------------------------------------------------------------ run the JavaScript
HARNESS = r"""
const fs = require("fs");
const [file, casesJson] = process.argv.slice(1);
const code = fs.readFileSync(file, "utf8");
const store = {};
const out = [];
for (const c of JSON.parse(casesJson)) {
  const fn = new Function("$env", "$input", "$getWorkflowStaticData", "require", code);
  try {
    out.push(fn(c.env, { first: () => c.item }, () => store, require)[0].json);
  } catch (e) {
    out.push({ error: String(e.message) });
  }
}
process.stdout.write(JSON.stringify(out));
"""


def _run(script: str, cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if NODE is None:
        pytest.skip("node is not installed")
    result = subprocess.run(  # noqa: S603 - fixed argv, test data
        [NODE, "-e", HARNESS, str(N8N / "code" / script), json.dumps(cases)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return list(json.loads(result.stdout))


def _delivery(
    secret: str, event: dict[str, Any], *, ts: int | None = None, tamper: bool = False
) -> dict[str, Any]:
    body = json.dumps(event).encode()
    stamp = ts if ts is not None else int(time.time())
    delivery = f"dlv-{uuid.uuid4().hex[:12]}"
    signature = sig.signature(secret, stamp, delivery, body)
    sent = body + (b" " if tamper else b"")
    return {
        "env": {"OMNIGROWTH_WEBHOOK_SECRET": secret},
        "item": {
            "json": {
                "headers": {
                    "X-OmniGrowth-Timestamp": str(stamp),
                    "X-OmniGrowth-Signature": signature,
                    "X-OmniGrowth-Event-ID": event["id"],
                    "X-OmniGrowth-Delivery-ID": delivery,
                }
            },
            "binary": {"data": {"data": base64.b64encode(sent).decode()}},
        },
    }


def _event(kind: str, **data: Any) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "type": kind,
        "api_version": "2026-09",
        "occurred_at": "2026-09-27T12:00:00Z",
        "brand_id": str(uuid.uuid4()),
        "data": data,
        "links": {
            "content": "http://127.0.0.1:5173/content/x",
            "publishing": "http://127.0.0.1:5173/publications",
        },
    }


def test_n8n_verification_accepts_signed_events_and_rejects_forgeries_stale_and_duplicates() -> (
    None
):
    secret = "whsec_" + "a" * 43
    approval = _event("content.approval_requested", platform="LINKEDIN", format="TEXT_POST")
    failed = _event(
        "publication.failed",
        platform="X",
        attempt_count=5,
        last_error_code="PLATFORM_TIMEOUT",
        simulated=True,
    )
    uncertain = _event("publication.uncertain", platform="X", simulated=None)
    out = _run(
        "verify-and-route.js",
        [
            _delivery(secret, approval),
            _delivery(secret, approval),  # at-least-once duplicate
            _delivery(secret, failed),
            _delivery(secret, uncertain),
            _delivery(secret, _event("content.approval_requested"), tamper=True),
            _delivery(secret, _event("content.approval_requested"), ts=int(time.time()) - 400),
            {  # signed with another secret than the receiver holds
                **_delivery("whsec_" + "b" * 43, _event("content.approval_requested")),
                "env": {"OMNIGROWTH_WEBHOOK_SECRET": secret},
            },
        ],
    )
    assert [(o["httpStatus"], o["route"]) for o in out] == [
        (200, 0),
        (200, 3),
        (200, 1),
        (200, 2),
        (401, 3),
        (401, 3),
        (401, 3),
    ]
    assert "needs human approval" in out[0]["message"] and "/content/x" in out[0]["message"]
    assert "duplicate" in out[1]["message"]
    assert "SIMULATED (mock publisher: no real post" in out[2]["message"]
    assert "mode unknown" in out[3]["message"] and "reconcile it in OmniGrowth" in out[3]["message"]
    manual = _run(
        "verify-and-route.js",
        [
            _delivery(
                secret,
                _event("publication.failed", platform="X", publish_mode="MANUAL", simulated=False),
            )
        ],
    )[0]
    assert "MANUAL publication (a person posts it)" in manual["message"]


def test_n8n_routes_confirmed_not_published_and_labels_sandbox_evidence() -> None:
    """Phase 13 (A17): the reconciliation event gets its own route (4) and says nothing is
    retried; a live post to a sandbox account is labelled SANDBOX, not real."""
    secret = "whsec_" + "c" * 43
    confirmed = _event(
        "publication.not_published_confirmed",
        platform="MASTODON",
        publish_mode="LIVE",
        evidence_class="REAL",
        simulated=False,
        will_retry=False,
    )
    sandbox_fail = _event(
        "publication.failed",
        platform="MASTODON",
        publish_mode="LIVE",
        evidence_class="SANDBOX",
        simulated=False,
        attempt_count=1,
    )
    out = _run(
        "verify-and-route.js", [_delivery(secret, confirmed), _delivery(secret, sandbox_fail)]
    )
    assert (out[0]["httpStatus"], out[0]["route"]) == (200, 4)
    assert "Confirmed NOT published [REAL publication" in out[0]["message"]
    assert "will not be retried automatically" in out[0]["message"]
    assert out[1]["route"] == 1 and "SANDBOX (live API call to a test account" in out[1]["message"]


def test_n8n_inbound_signing_is_accepted_by_omnigrowth_verification() -> None:
    secret = "whsec_" + "c" * 43
    brand = str(uuid.uuid4())
    out = _run(
        "sign-inbound.js",
        [
            {
                "env": {"OMNIGROWTH_INBOUND_SECRET": secret, "OMNIGROWTH_BRAND_ID": brand},
                "item": {"json": {"content": "Hello from n8n."}},
            }
        ],
    )[0]
    body = out["body"].encode()
    assert sig.verify(
        [secret], out["timestamp"], out["signature"], out["deliveryId"], body, time.time()
    )
    assert not sig.verify(
        [secret], out["timestamp"], out["signature"], "n8n-other", body, time.time()
    )
    envelope = json.loads(body)
    assert envelope == {
        "operation": "source.text_ingest",
        "payload": {"brand_id": brand, "title": "From n8n", "content": "Hello from n8n."},
    }
    assert out["deliveryId"].startswith("n8n-")
    missing = _run("sign-inbound.js", [{"env": {}, "item": {"json": {}}}])[0]
    assert "OMNIGROWTH_INBOUND_SECRET is not set" in missing["error"]
