#!/usr/bin/env python3
"""Check that workflow refs resolve to the sole canonical Kind E2E command."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/kind-e2e.yml"
BEGIN = "# BEGIN TARGET REF RESOLUTION"
END = "# END TARGET REF RESOLUTION"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def extract_resolver(workflow: str) -> str:
    start = workflow.index(BEGIN) + len(BEGIN)
    finish = workflow.index(END, start)
    return textwrap.dedent(workflow[start:finish]).strip()


def run_resolver(script: str, event: str, requested_ref: str) -> tuple[int, str]:
    real_git = shutil.which("git")
    check(real_git is not None, "git must be available to exercise source resolution")
    with tempfile.TemporaryDirectory(prefix="aigc-workflow-contract-") as tmp:
        temp = Path(tmp)
        fake_git = temp / "git"
        fake_git.write_text(
            "#!/usr/bin/env bash\n"
            f"REAL_GIT={real_git!s}\n"
            "if [[ \"$1\" == check-ref-format ]]; then exec \"$REAL_GIT\" \"$@\"; fi\n"
            "if [[ \"$1\" == ls-remote ]]; then\n"
            "  [[ \"$2\" == --exit-code && \"$3\" == https://github.com/nerdalert/ai-gateway-controller.git ]] || exit 2\n"
            "  ref=\"${@: -1}\"\n"
            "  branch=\"${ref#refs/heads/}\"\n"
            "  [[ \"$branch\" != missing-ref ]] || exit 2\n"
            "  sha=$(printf '%s' \"$branch\" | sha1sum | cut -c1-40)\n"
            "  if [[ \"$branch\" == duplicate-ref ]]; then printf '%s\\t%s\\n' \"$sha\" \"$ref\"; fi\n"
            "  printf '%s\\t%s\\n' \"$sha\" \"$ref\"\n"
            "  exit 0\n"
            "fi\n"
            "exec \"$REAL_GIT\" \"$@\"\n",
            encoding="utf-8",
        )
        fake_git.chmod(0o755)
        output_file = temp / "github-output"
        environment = os.environ | {
            "PATH": f"{temp}:{os.environ['PATH']}",
            "EVENT_NAME": event,
            "TARGET_REF_INPUT": requested_ref,
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "nerdalert/ai-gateway-controller",
            "GITHUB_OUTPUT": str(output_file),
        }
        result = subprocess.run(
            ["bash", "-e", "-u", "-o", "pipefail", "-c", script],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode, output_file.read_text(encoding="utf-8") if output_file.exists() else result.stderr


def main() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    check('cron: "0 22 * * *"' in workflow, "daily workflow schedule must be 22:00 UTC")
    check(re.search(r"target_ref:[\s\S]*?default:\s*main", workflow) is not None, "manual target_ref must default to main")
    check("ref: ${{ steps.resolve.outputs.target_sha }}" in workflow, "checkout must use the resolved full SHA")
    check(
        "actions/setup-go@4a3601121dd01d1626a1e23e37211e3254c1c06c" in workflow
        and "go-version-file: go.mod" in workflow,
        "the harness Go toolchain must come from the checked-out source's go.mod",
    )
    check(
        workflow.count("./test/kind-env/e2e.sh 2>&1 | tee") == 1,
        "workflow must invoke the canonical Kind E2E exactly once",
    )
    check("test/kind-dev/" not in workflow, "workflow must use the existing test/kind-env harness")
    check("cancel-in-progress: false" in workflow, "an active cluster qualification must not be canceled by a later run")
    check("PIPESTATUS[0]" in workflow, "the E2E command exit must be captured independently from tee")

    resolver = extract_resolver(workflow)
    for marker in ("EVENT_NAME", "workflow_dispatch", "schedule", "git ls-remote", "target_sha="):
        check(marker in resolver, f"source resolver must handle {marker}")

    cases = [
        ("schedule", "ignored/dispatch-input", "main"),
        ("workflow_dispatch", "", "main"),
        ("workflow_dispatch", "main", "main"),
        ("workflow_dispatch", "feature/e2e-check", "feature/e2e-check"),
    ]
    for event, requested, expected in cases:
        rc, output = run_resolver(resolver, event, requested)
        check(rc == 0, f"resolver failed for event={event} target_ref={requested!r}: {output}")
        check(f"target_ref={expected}\n" in output, f"wrong resolved branch for {event}/{requested!r}: {output}")
        sha = hashlib.sha1(expected.encode()).hexdigest()
        check(f"target_sha={sha}\n" in output, f"wrong branch SHA for {expected}: {output}")

    rc, _ = run_resolver(resolver, "workflow_dispatch", "../not-a-branch")
    check(rc != 0, "resolver must reject an invalid branch name")
    rc, _ = run_resolver(resolver, "workflow_dispatch", "missing-ref")
    check(rc != 0, "resolver must reject a branch absent from the target repository")
    rc, _ = run_resolver(resolver, "workflow_dispatch", "duplicate-ref")
    check(rc != 0, "resolver must reject ambiguous branch resolution")
    print("workflow target contract: PASS (schedule/main, blank/default, explicit branch, invalid branch, canonical e2e command)")


if __name__ == "__main__":
    main()
