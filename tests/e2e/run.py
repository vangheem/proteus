#!/usr/bin/env python3
"""End-to-end scenarios for Proteus.

The coordinator and infra must already be up. scripts/e2e.sh does that.
Each scenario is a happy-path check of a designed behavior, not a string unit test.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("PROTEUS_E2E_BASE", "http://127.0.0.1:8080")
TIMEOUT = float(os.environ.get("PROTEUS_E2E_HTTP_TIMEOUT", "180"))


class Failure(Exception):
    pass


def request(method, path, body=None, headers=None, timeout=TIMEOUT):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs.setdefault("content-type", "application/json")
    req = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, dict(resp.headers), raw
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def get(path, timeout=TIMEOUT):
    status, headers, raw = request("GET", path, timeout=timeout)
    return status, headers, raw


def get_json(path, timeout=TIMEOUT):
    status, _, raw = get(path, timeout=timeout)
    if status != 200:
        raise Failure(f"GET {path} -> {status}: {raw[:300]!r}")
    return json.loads(raw.decode())


def post_json(path, body, timeout=TIMEOUT):
    status, _, raw = request("POST", path, body, timeout=timeout)
    if status != 200:
        raise Failure(f"POST {path} -> {status}: {raw[:500]!r}")
    return json.loads(raw.decode())


def wait_until(label, fn, seconds=120):
    deadline = time.time() + seconds
    last = None
    while time.time() < deadline:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - retry until the stack is ready
            last = exc
            time.sleep(1)
    raise Failure(f"timed out waiting for {label}: {last}")


def scenario_coordinator_health():
    body = wait_until("coordinator health", lambda: get_json("/__proteus/health"))
    if body.get("service") != "proteus-coordinator" or not body.get("ok"):
        raise Failure(body)


def scenario_components_are_routed():
    boot = wait_until("bootstrap health", lambda: get_json("/health"))
    echo = wait_until("echo health", lambda: get_json("/echo/health"))
    if boot.get("name") != "bootstrap" or not boot.get("ok"):
        raise Failure(boot)
    if echo.get("name") != "echo" or not echo.get("ok"):
        raise Failure(echo)
    status, _, raw = get("/__proteus/health")
    if b"proteus-coordinator" not in raw:
        raise Failure("reserved coordinator path was proxied")
    if status != 200:
        raise Failure(status)


def scenario_bootstrap_prompt_and_status_frame():
    status, _, raw = get("/")
    if status != 200:
        raise Failure(status)
    html = raw.decode()
    for needle in (
        'id="proteus-prompt"',
        'name="prompt"',
        "/__proteus/api/tasks",
        'id="proteus-status-frame"',
        'id="proteus-frame-refresh"',
        "fetch('/__proteus/status'",
        "htmx.org",
    ):
        if needle not in html:
            raise Failure(f"bootstrap page missing {needle}")
    if 'data-proteus-introspect="1"' in html:
        raise Failure("root UI was the introspection page")


def scenario_introspection_is_immutable_and_framed():
    status, _, raw = get("/__proteus/introspect")
    if status != 200:
        raise Failure(status)
    html = raw.decode()
    for needle in (
        'data-proteus-introspect="1"',
        "immutable",
        "bootstrap",
        "echo",
        'id="proteus-status-frame"',
        'id="proteus-frame-refresh"',
    ):
        if needle not in html:
            raise Failure(f"introspection missing {needle}")
    if 'id="proteus-prompt"' in html:
        raise Failure("introspection was proxied to bootstrap")


def scenario_status_reports_runtime_and_model():
    body = get_json("/__proteus/status")
    if body.get("runtime") != "docker":
        raise Failure(body)
    if body.get("agent_backend") != "herdr":
        raise Failure(body)
    if body.get("agent_kind") != "opencode":
        raise Failure(body)
    if body.get("model") != "xai/grok-4.7":
        raise Failure(body)
    if not isinstance(body.get("revision"), int) or body["revision"] < 1:
        raise Failure(body)
    names = {item["name"] for item in body.get("components", [])}
    if not {"bootstrap", "echo"} <= names:
        raise Failure(body)


def scenario_schemaless_store_and_tags():
    key = f"e2e-{int(time.time())}"
    status, _, raw = request(
        "PUT",
        f"/proteus/sdk/store/{key}",
        {"value": {"kind": "note", "text": "hello"}, "tags": ["e2e", "note"]},
    )
    if status != 200:
        raise Failure(raw)
    got = get_json(f"/proteus/sdk/store/{key}")
    if got["value"] != {"kind": "note", "text": "hello"}:
        raise Failure(got)
    if sorted(got["tags"]) != ["e2e", "note"]:
        raise Failure(got)
    found = get_json("/proteus/sdk/store?tag=e2e&tag=note")
    keys = [item["key"] for item in found["items"]]
    if key not in keys:
        raise Failure(found)
    missing = get_json("/proteus/sdk/store?tag=e2e&tag=does-not-exist")
    if any(item["key"] == key for item in missing["items"]):
        raise Failure("tag filter was not AND")


def scenario_cache():
    key = f"cache-{int(time.time())}"
    status, _, raw = request("PUT", f"/proteus/sdk/cache/{key}", {"value": "abc", "ttl": 60})
    if status != 200:
        raise Failure(raw)
    got = get_json(f"/proteus/sdk/cache/{key}")
    if got.get("value") != "abc":
        raise Failure(got)


def scenario_temporal_workflow_between_components():
    def call():
        return post_json(
            "/echo/proteus/sdk/bus/workflow",
            {"component": "echo", "name": "EchoWorkflow", "arg": {"text": "hi"}},
            timeout=40,
        )

    body = wait_until("echo workflow", call, seconds=90)
    result = body.get("result") or {}
    if result.get("text") != "hi" or result.get("echo") is not True:
        raise Failure(body)


def scenario_version_swap_and_rollback():
    before = get_json("/__proteus/status")["revision"]
    v2 = {
        "proteus.toml": 'name = "echo"\nroute_prefix = "/echo"\nautostart = true\n',
        "app.py": (
            "from proteus import Component\n\n"
            "component = Component()\n\n"
            "@component.get('/')\n"
            "def index():\n"
            "    return component.page('Echo', \"<p id='echo-body'>echo-v2</p>\")\n\n"
            "def setup():\n"
            "    return component\n"
        ),
    }
    deployed = post_json("/__proteus/api/components/echo/deploy", {"source": v2}, timeout=180)
    if deployed.get("status") != "running" or int(deployed.get("version", 0)) < 2:
        raise Failure(deployed)
    status, _, raw = get("/echo/")
    html = raw.decode()
    if status != 200 or "echo-v2" not in html:
        raise Failure(f"swap did not serve v2: {status} {html[:200]!r}")
    if 'id="proteus-status-frame"' not in html:
        raise Failure("swapped page lost the status frame")
    versions = get_json("/__proteus/api/components/echo/versions")
    nums = [item["version"] for item in versions["versions"]]
    if 1 not in nums or deployed["version"] not in nums:
        raise Failure(versions)
    after = get_json("/__proteus/status")
    if after["revision"] <= before:
        raise Failure("deploy did not publish a new revision")
    if after["latest_event"]["message"] == "":
        raise Failure(after)
    rolled = post_json(
        "/__proteus/api/components/echo/rollback",
        {"version": 1},
        timeout=180,
    )
    if int(rolled.get("version", 0)) != 1:
        raise Failure(rolled)
    _, _, raw = get("/echo/")
    if b"echo-v1" not in raw:
        raise Failure(f"rollback did not restore v1: {raw[:200]!r}")
    still = get_json("/__proteus/api/components/echo/versions")
    if deployed["version"] not in [item["version"] for item in still["versions"]]:
        raise Failure("rollback deleted history")


def scenario_agent_plan_uses_herdr_and_grok():
    prompt = "add a notes component"
    body = get_json("/__proteus/api/agent/plan?prompt=" + urllib.parse.quote(prompt))
    blob = json.dumps(body)
    for needle in (
        "herdr",
        "opencode",
        "xai/grok-4.7",
        "proteus-mutator",
        "components",
        "introspection",
    ):
        if needle not in blob:
            raise Failure(f"agent plan missing {needle}: {blob[:500]}")
    if body.get("model") != "xai/grok-4.7":
        raise Failure(body)


def scenario_global_styles_render_on_every_component():
    marker = f"e2e-global-{int(time.time())}"
    status, _, raw = request(
        "PUT",
        "/echo/proteus/sdk/styles",
        {"css": f"body {{ outline: 1px solid transparent; }} /* {marker} */"},
    )
    if status != 200:
        raise Failure(raw)
    for path in ("/", "/echo/"):
        code, _, body = get(path)
        html = body.decode()
        if code != 200 or marker not in html or 'id="proteus-global-styles"' not in html:
            raise Failure(f"{path} missing global style: {code} {html[:240]!r}")
    _, _, intro = get("/__proteus/introspect")
    if marker.encode() in intro:
        raise Failure("introspection picked up component styles")


def scenario_coding_agent_ability_through_temporal():
    catalog = get_json("/__proteus/api/abilities")
    names = {item["name"] for item in catalog.get("abilities", [])}
    if names != {"ask", "task"} or catalog.get("task_queue") != "proteus-coordinator":
        raise Failure(catalog)
    if catalog.get("model") != "xai/grok-4.7":
        raise Failure(catalog)

    def ask():
        return post_json(
            "/echo/proteus/sdk/abilities/ask",
            {"prompt": "what is proteus?"},
            timeout=40,
        )

    body = wait_until("coordinator ask ability", ask, seconds=90)
    result = body.get("result") or {}
    if result.get("answer") != "echo: what is proteus?":
        raise Failure(body)
    if result.get("executor") != "echo":
        raise Failure(body)

    task = post_json(
        "/echo/proteus/sdk/abilities/task",
        {"prompt": "add a notes page"},
        timeout=40,
    )
    if task.get("result", {}).get("output") != "echo: add a notes page":
        raise Failure(task)


def scenario_introspection_shows_call_failures():
    status, _, raw = get("/__proteus/introspect")
    html = raw.decode()
    if status != 200 or 'data-proteus-calls="1"' not in html or "Calls" not in html:
        raise Failure("introspection is missing the calls section")
    if "Herdr connection:" not in html:
        raise Failure("introspection is missing the agent connection")
    body = get_json("/__proteus/api/calls")
    calls = body.get("calls")
    if not isinstance(calls, list):
        raise Failure(body)
    for call in calls:
        if call.get("status") == "failed" and not call.get("error"):
            raise Failure(f"failed call has no reason: {call}")
        error = call.get("error") or ""
        if error and error not in html:
            raise Failure(f"introspection omitted {error}")


def scenario_herdr_probe():
    body = get_json("/__proteus/api/agent/probe")
    if not body.get("ok"):
        raise Failure(body)


SCENARIOS = [
    ("coordinator_health", scenario_coordinator_health),
    ("components_are_routed", scenario_components_are_routed),
    ("bootstrap_prompt_and_status_frame", scenario_bootstrap_prompt_and_status_frame),
    ("introspection_is_immutable_and_framed", scenario_introspection_is_immutable_and_framed),
    ("status_reports_runtime_and_model", scenario_status_reports_runtime_and_model),
    ("schemaless_store_and_tags", scenario_schemaless_store_and_tags),
    ("cache", scenario_cache),
    ("temporal_workflow_between_components", scenario_temporal_workflow_between_components),
    ("version_swap_and_rollback", scenario_version_swap_and_rollback),
    ("agent_plan_uses_herdr_and_grok", scenario_agent_plan_uses_herdr_and_grok),
    ("global_styles_render_on_every_component", scenario_global_styles_render_on_every_component),
    ("coding_agent_ability_through_temporal", scenario_coding_agent_ability_through_temporal),
    ("introspection_shows_call_failures", scenario_introspection_shows_call_failures),
    ("herdr_probe", scenario_herdr_probe),
]


def main():
    failed = 0
    for name, fn in SCENARIOS:
        started = time.time()
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - scenario report
            failed += 1
            print(f"FAIL  {name}  {exc}")
        else:
            print(f"PASS  {name}  ({time.time() - started:.1f}s)")
    print(f"\n{len(SCENARIOS) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
