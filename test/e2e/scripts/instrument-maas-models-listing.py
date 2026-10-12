#!/usr/bin/env python3
"""Instrument the disposable, pinned MaaS E2E checkout for PR #123 diagnosis."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

EXPECTED_MAAS_COMMIT = "090cc5d1a52a82f75b81a0a104f0d244c9a0ab9f"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count == 1 and new not in text:
        path.write_text(text.replace(old, new, 1))
    elif count == 0 and new in text:
        return
    else:
        raise RuntimeError(f"expected one patch anchor in {path}, found {count}")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: instrument-maas-models-listing.py MAAS_E2E_DIR", file=sys.stderr)
        return 2

    test_dir = Path(sys.argv[1]).resolve()
    repo = test_dir.parent.parent
    resolved = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    if resolved != EXPECTED_MAAS_COMMIT:
        raise RuntimeError(f"expected MaaS {EXPECTED_MAAS_COMMIT}, got {resolved}")

    models_test = test_dir / "tests/test_models_endpoint.py"
    fixture = test_dir / "tests/worker_tenant_fixtures.py"
    replace_once(models_test, "import logging\nimport subprocess\n", "import logging\nimport os\nimport subprocess\n")

    helper = r"""
_AIGC_DIAG_KEYS = (
    "timestamp", "level", "message", "request_id", "status", "listed",
    "accessible", "subscriptions", "subscription", "totalModels",
    "modelRefsCount", "modelsToCheck", "count", "id", "kind",
)


def _aigc_diag_enabled():
    return os.environ.get("AIGC_MODELS_LISTING_DIAGNOSTICS", "").lower() == "true"


def _aigc_diag_dir():
    from pathlib import Path
    root = Path(os.environ.get("ARTIFACTS_DIR", "/tmp")) / "models-listing-diagnostics"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _aigc_oc_json(args):
    result = subprocess.run(["oc", *args, "-o", "json"], capture_output=True, text=True, timeout=45)
    if result.returncode != 0:
        return {"error": f"oc {' '.join(args)} failed: {result.stderr.strip()[:250]}"}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": "oc returned invalid JSON"}


def _aigc_conditions(conditions):
    return [
        {key: item.get(key) for key in ("type", "status", "reason", "observedGeneration") if key in item}
        for item in (conditions or []) if isinstance(item, dict)
    ]


def _aigc_capture_state(stage, context, subscription_name, subscription_namespace, refs):
    from datetime import datetime, timezone
    result = {
        "stage": stage,
        "capturedAtUtc": datetime.now(timezone.utc).isoformat(),
        "maasSource": os.environ.get("MAAS_COMMIT_RESOLVED", "unknown"),
        "subscription": {},
        "modelRefs": [],
        "api": {},
        "gateway": {},
        "routes": [],
    }
    sub = _aigc_oc_json(["get", "maassubscription", subscription_name, "-n", subscription_namespace])
    if "error" in sub:
        result["subscription"] = sub
    else:
        spec, status = sub.get("spec", {}), sub.get("status", {})
        result["subscription"] = {
            "name": sub.get("metadata", {}).get("name"),
            "namespace": sub.get("metadata", {}).get("namespace"),
            "generation": sub.get("metadata", {}).get("generation"),
            "resourceVersion": sub.get("metadata", {}).get("resourceVersion"),
            "phase": status.get("phase"),
            "readyConditions": _aigc_conditions(status.get("conditions")),
            "specModelRefs": [
                {"name": ref.get("name"), "namespace": ref.get("namespace")}
                for ref in spec.get("modelRefs", []) if isinstance(ref, dict)
            ],
            "statusModelRefs": [
                {key: ref.get(key) for key in ("name", "namespace", "ready", "observedGeneration") if key in ref}
                for ref in status.get("modelRefStatuses", []) if isinstance(ref, dict)
            ],
            "tokenRateLimitStatusCount": len(status.get("tokenRateLimitStatuses", [])),
        }

    for name, namespace in refs:
        obj = _aigc_oc_json(["get", "maasmodelref", name, "-n", namespace])
        if "error" in obj:
            result["modelRefs"].append({"name": name, "namespace": namespace, **obj})
            continue
        status = obj.get("status", {})
        model_ref = obj.get("spec", {}).get("modelRef", {})
        result["modelRefs"].append({
            "name": obj.get("metadata", {}).get("name"),
            "namespace": obj.get("metadata", {}).get("namespace"),
            "generation": obj.get("metadata", {}).get("generation"),
            "resourceVersion": obj.get("metadata", {}).get("resourceVersion"),
            "kind": model_ref.get("kind"),
            "phase": status.get("phase"),
            "hasEndpoint": bool(status.get("endpoint")),
            "resolvedModelAlias": status.get("resolvedModelAlias"),
            "conditions": _aigc_conditions(status.get("conditions")),
        })

    api = _aigc_oc_json(["get", "deployment", context.api_deployment_name, "-n", "odh-ai-gateway-infra"])
    if "error" in api:
        result["api"] = api
    else:
        spec, status = api.get("spec", {}), api.get("status", {})
        selector = spec.get("selector", {}).get("matchLabels", {})
        result["api"] = {
            "deployment": context.api_deployment_name,
            "namespace": "odh-ai-gateway-infra",
            "generation": api.get("metadata", {}).get("generation"),
            "observedGeneration": status.get("observedGeneration"),
            "image": spec.get("template", {}).get("spec", {}).get("containers", [{}])[0].get("image"),
            "diagnosticEnv": {
                env.get("name"): env.get("value")
                for container in spec.get("template", {}).get("spec", {}).get("containers", [])
                for env in container.get("env", [])
                if env.get("name") in ("DEBUG_MODE", "LOG_FORMAT")
            },
            "readyReplicas": status.get("readyReplicas", 0),
            "selector": selector,
        }
        if selector:
            label_arg = ",".join(f"{key}={value}" for key, value in sorted(selector.items()))
            pods = _aigc_oc_json(["get", "pods", "-n", "odh-ai-gateway-infra", "-l", label_arg])
            result["api"]["pods"] = [
                {
                    "name": pod.get("metadata", {}).get("name"),
                    "phase": pod.get("status", {}).get("phase"),
                    "ready": any(c.get("type") == "Ready" and c.get("status") == "True" for c in pod.get("status", {}).get("conditions", [])),
                    "images": [
                        {"name": c.get("name"), "image": c.get("image"), "imageID": c.get("imageID")}
                        for c in pod.get("status", {}).get("containerStatuses", [])
                    ],
                }
                for pod in pods.get("items", [])
            ]

    gateway_ns = os.environ.get("GATEWAY_NAMESPACE", "openshift-ingress")
    gateway = _aigc_oc_json(["get", "gateway", context.gateway_name, "-n", gateway_ns])
    if "error" in gateway:
        result["gateway"] = gateway
    else:
        result["gateway"] = {
            "name": context.gateway_name,
            "namespace": gateway_ns,
            "gatewayClassName": gateway.get("spec", {}).get("gatewayClassName"),
            "labels": gateway.get("metadata", {}).get("labels", {}),
            "listeners": [
                {"name": item.get("name"), "port": item.get("port"), "protocol": item.get("protocol")}
                for item in gateway.get("spec", {}).get("listeners", [])
            ],
            "programmedConditions": _aigc_conditions(gateway.get("status", {}).get("conditions")),
        }

    routes = _aigc_oc_json(["get", "httproutes", "-A"])
    for route in routes.get("items", []):
        spec = route.get("spec", {})
        parents = [{"name": ref.get("name"), "namespace": ref.get("namespace")} for ref in spec.get("parentRefs", [])]
        if not any(ref.get("name") == context.gateway_name for ref in parents):
            continue
        result["routes"].append({
            "name": route.get("metadata", {}).get("name"),
            "namespace": route.get("metadata", {}).get("namespace"),
            "parentRefs": parents,
            "matches": [[match.get("path") for match in rule.get("matches", [])] for rule in spec.get("rules", [])],
            "backendRefs": [
                {"name": backend.get("name"), "port": backend.get("port")}
                for rule in spec.get("rules", []) for backend in rule.get("backendRefs", [])
            ],
        })

    all_pods = _aigc_oc_json(["get", "pods", "-A"])
    result["relevantImagePods"] = []
    for pod in all_pods.get("items", []):
        meta = pod.get("metadata", {})
        name = meta.get("name", "")
        labels = meta.get("labels", {})
        if not (
            name.startswith("payload-processing")
            or name.startswith("payload-pre-processing")
            or name.startswith("maas-api")
            or name.startswith("maas-controller")
            or name.startswith("ai-gateway-controller")
        ):
            continue
        result["relevantImagePods"].append({
            "name": name,
            "namespace": meta.get("namespace"),
            "labels": {key: labels[key] for key in ("app", "app.kubernetes.io/name") if key in labels},
            "phase": pod.get("status", {}).get("phase"),
            "ready": any(c.get("type") == "Ready" and c.get("status") == "True" for c in pod.get("status", {}).get("conditions", [])),
            "containers": [
                {"name": c.get("name"), "image": c.get("image"), "imageID": c.get("imageID")}
                for c in pod.get("status", {}).get("containerStatuses", [])
            ],
        })

    output = _aigc_diag_dir() / f"{stage}.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    log.info("AIGC models-listing snapshot saved: %s", output)


def _aigc_capture_gateway_xds(context):
    from datetime import datetime, timezone
    result = {"capturedAtUtc": datetime.now(timezone.utc).isoformat(), "gateway": context.gateway_name, "pods": []}
    pods = _aigc_oc_json([
        "get", "pods", "-A", "-l",
        f"gateway.networking.k8s.io/gateway-name={context.gateway_name}",
    ])
    for pod in pods.get("items", []):
        meta = pod.get("metadata", {})
        pod_summary = {
            "name": meta.get("name"),
            "namespace": meta.get("namespace"),
            "labels": {
                key: meta.get("labels", {}).get(key)
                for key in ("gateway.networking.k8s.io/gateway-name", "gateway.networking.k8s.io/gateway-class")
                if meta.get("labels", {}).get(key) is not None
            },
            "containers": [
                {"name": c.get("name"), "image": c.get("image"), "imageID": c.get("imageID")}
                for c in pod.get("status", {}).get("containerStatuses", [])
            ],
        }
        result["pods"].append(pod_summary)
        if not any(c.get("name") == "istio-proxy" for c in pod.get("spec", {}).get("containers", [])):
            continue
        dump = subprocess.run(
            ["oc", "exec", "-n", meta.get("namespace", ""), meta.get("name", ""),
             "-c", "istio-proxy", "--", "pilot-agent", "request", "GET", "config_dump"],
            capture_output=True, text=True, timeout=45,
        )
        if dump.returncode != 0:
            pod_summary["xdsError"] = dump.stderr.strip()[:250]
            continue
        try:
            config = json.loads(dump.stdout)
        except json.JSONDecodeError:
            pod_summary["xdsError"] = "pilot-agent returned invalid JSON"
            continue
        filters, route_overrides = set(), []
        def visit(node):
            if isinstance(node, dict):
                name = node.get("name")
                if isinstance(name, str) and "ext_proc" in name:
                    filters.add(name)
                per_filter = node.get("typed_per_filter_config")
                if isinstance(per_filter, dict) and any("ext_proc" in key for key in per_filter):
                    route_overrides.append({
                        "routeName": name,
                        "path": node.get("match", {}).get("path") or node.get("match", {}).get("prefix"),
                        "filters": {
                            key: {"disabled": value.get("disabled")}
                            for key, value in per_filter.items()
                            if "ext_proc" in key and isinstance(value, dict)
                        },
                    })
                for value in node.values():
                    visit(value)
            elif isinstance(node, list):
                for value in node:
                    visit(value)
        visit(config)
        pod_summary["xdsHttpExtProcFilters"] = sorted(filters)
        pod_summary["xdsRoutesWithExtProcOverrides"] = route_overrides

    filters = _aigc_oc_json(["get", "envoyfilters.networking.istio.io", "-A"])
    selected = []
    for item in filters.get("items", []):
        spec = item.get("spec", {})
        selector = spec.get("workloadSelector", {}).get("labels", {})
        patch_names = set()
        for patch in spec.get("configPatches", []):
            value = patch.get("patch", {}).get("value", {})
            if isinstance(value, dict) and isinstance(value.get("name"), str) and "ext_proc" in value["name"]:
                patch_names.add(value["name"])
            typed = value.get("typed_per_filter_config", {}) if isinstance(value, dict) else {}
            patch_names.update(key for key in typed if "ext_proc" in key)
        if selector and patch_names:
            selected.append({
                "name": item.get("metadata", {}).get("name"),
                "namespace": item.get("metadata", {}).get("namespace"),
                "selector": selector,
                "matchesGatewayPod": any(
                    all(pod.get("metadata", {}).get("labels", {}).get(key) == val for key, val in selector.items())
                    for pod in pods.get("items", [])
                ),
                "extProcPatchNames": sorted(patch_names),
            })
    result["envoyFilterSelectors"] = selected
    output = _aigc_diag_dir() / "gateway-xds.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    log.info("AIGC gateway xDS snapshot saved: %s", output)


def _aigc_capture_response(stage, response, request_id):
    from datetime import datetime, timezone
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError):
        body = {}
    ids = [item.get("id") for item in body.get("data", []) if isinstance(item, dict)]
    record = {
        "stage": stage,
        "capturedAtUtc": datetime.now(timezone.utc).isoformat(),
        "requestId": request_id,
        "status": response.status_code,
        "responseRequestId": response.headers.get("X-Request-ID"),
        "accessCheckedAt": response.headers.get("X-Access-Checked-At"),
        "object": body.get("object"),
        "modelCount": len(ids),
        "modelIds": ids,
    }
    with (_aigc_diag_dir() / "responses.jsonl").open("a") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")
    log.info("AIGC /v1/models response stage=%s request_id=%s status=%s model_count=%s", stage, request_id, response.status_code, len(ids))


def _aigc_capture_request_logs(context, request_ids):
    deployment = _aigc_oc_json(["get", "deployment", context.api_deployment_name, "-n", "odh-ai-gateway-infra"])
    labels = deployment.get("spec", {}).get("selector", {}).get("matchLabels", {})
    label_arg = ",".join(f"{key}={value}" for key, value in sorted(labels.items()))
    pods = _aigc_oc_json(["get", "pods", "-n", "odh-ai-gateway-infra", "-l", label_arg])
    records = []
    for pod in pods.get("items", []):
        name = pod.get("metadata", {}).get("name", "")
        if not any(c.get("name") == "maas-api" for c in pod.get("spec", {}).get("containers", [])):
            continue
        raw = subprocess.run(
            ["oc", "logs", "-n", "odh-ai-gateway-infra", f"pod/{name}", "-c", "maas-api", "--since=10m"],
            capture_output=True, text=True, timeout=45,
        )
        for line in raw.stdout.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("request_id") in request_ids:
                records.append({key: item[key] for key in _AIGC_DIAG_KEYS if key in item})
    output = _aigc_diag_dir() / "request-logs.json"
    output.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n")
    log.info("AIGC request-correlated MaaS API logs saved: %s", output)
"""
    replace_once(models_test, "log = logging.getLogger(__name__)\n", "log = logging.getLogger(__name__)\n" + helper)
    replace_once(
        models_test,
        '    context = request.getfixturevalue("worker_tenant_context")\n    ensure_worker_models(\n',
        '    context = request.getfixturevalue("worker_tenant_context")\n'
        '    if _aigc_diag_enabled():\n'
        '        globals()["_AIGC_DIAGNOSTIC_WORKER_CONTEXT"] = context\n'
        '    ensure_worker_models(\n',
    )

    old_query = """            # Query /v1/models (use retry helper for gateway/Authorino propagation)
            log.info(f"Querying /v1/models with subscription: {subscription_name}")
            r = _request_with_gateway_retry(
                requests.get,
                f"{_maas_api_url()}/v1/models",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "x-maas-subscription": subscription_name,
                },
            )
"""
    new_query = """            # Query /v1/models (use retry helper for gateway/Authorino propagation)
            log.info(f"Querying /v1/models with subscription: {subscription_name}")
            diagnostic = _aigc_diag_enabled()
            context = globals().get("_AIGC_DIAGNOSTIC_WORKER_CONTEXT")
            request_ids = []
            headers = {
                "Authorization": f"Bearer {api_key}",
                "x-maas-subscription": subscription_name,
            }
            if diagnostic and context:
                primary_id = f"aigc-pr123-{uuid.uuid4().hex[:12]}-primary"
                request_ids.append(primary_id)
                headers["X-Request-ID"] = primary_id
                refs = ((DISTINCT_MODEL_REF, MODEL_NAMESPACE), (DISTINCT_MODEL_2_REF, MODEL_NAMESPACE))
                _aigc_capture_state("before-query", context, subscription_name, maas_ns, refs)
                _aigc_capture_gateway_xds(context)
            elif diagnostic:
                raise RuntimeError("diagnostics enabled but the worker tenant context was not activated")
            r = _request_with_gateway_retry(
                requests.get,
                f"{_maas_api_url()}/v1/models",
                headers=headers,
            )
            if diagnostic and context:
                _aigc_capture_response("primary", r, headers["X-Request-ID"])
                refs = ((DISTINCT_MODEL_REF, MODEL_NAMESPACE), (DISTINCT_MODEL_2_REF, MODEL_NAMESPACE))
                _aigc_capture_state("immediately-after-primary", context, subscription_name, maas_ns, refs)
                for delay_seconds in (2, 5):
                    time.sleep(delay_seconds)
                    _aigc_capture_state(f"after-{delay_seconds}s", context, subscription_name, maas_ns, refs)
                    repeat_id = f"aigc-pr123-{uuid.uuid4().hex[:12]}-repeat-{delay_seconds}s"
                    request_ids.append(repeat_id)
                    repeated = requests.get(
                        f"{_maas_api_url()}/v1/models",
                        headers={**headers, "X-Request-ID": repeat_id},
                        timeout=TIMEOUT,
                        verify=TLS_VERIFY,
                    )
                    _aigc_capture_response(f"repeat-{delay_seconds}s", repeated, repeat_id)
                _aigc_capture_request_logs(context, request_ids)
"""
    replace_once(models_test, old_query, new_query)

    old_fixture = """    deployment_name = f"maas-api-{case['tenant_label_name']}"
    wait_for_deployment_available(deployment_name, namespace=INFRA_NAMESPACE, timeout=180)

    apply_gateway_access_label(context.model_namespace, context.gateway_name)
"""
    new_fixture = """    deployment_name = f"maas-api-{case['tenant_label_name']}"
    wait_for_deployment_available(deployment_name, namespace=INFRA_NAMESPACE, timeout=180)
    if os.environ.get("AIGC_MODELS_LISTING_DIAGNOSTICS", "").lower() == "true":
        # Apply the same test-only log setting before creating test resources.
        _oc_run(["set", "env", f"deployment/{deployment_name}", "-n", INFRA_NAMESPACE,
                 "DEBUG_MODE=true", "LOG_FORMAT=otel-json"], timeout=60)
        _oc_run(["rollout", "status", f"deployment/{deployment_name}", "-n", INFRA_NAMESPACE,
                 "--timeout=180s"], timeout=210)
        wait_for_deployment_available(deployment_name, namespace=INFRA_NAMESPACE, timeout=180)

    apply_gateway_access_label(context.model_namespace, context.gateway_name)
"""
    replace_once(fixture, old_fixture, new_fixture)

    print(f"Instrumented MaaS diagnostics in disposable checkout at {resolved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
