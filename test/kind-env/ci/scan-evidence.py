#!/usr/bin/env python3
"""Redact and report likely credential values in retained Kind evidence."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


MARKERS = [
    re.compile(r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)([A-Za-z0-9._~+/-]{12,}={0,2})"),
    re.compile(
        r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|credential|client[_-]?secret|secret[_-]?access[_-]?key|password)\s*[:=]\s*)(['\"]?)([A-Za-z0-9._~+/-]{12,}={0,2})(['\"]?)"
    ),
    re.compile(r"\b(?:sk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|AIza[A-Za-z0-9_-]{30,})\b"),
    re.compile(r"\b(?:kind-only-dummy|transition-provider-key)\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
]
PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----",
    re.DOTALL,
)


def redact_text(text: str) -> tuple[str, int]:
    count = 0
    text, private_count = PRIVATE_KEY.subn("[REDACTED PRIVATE KEY]", text)
    count += private_count
    for index, marker in enumerate(MARKERS):
        if index == 0:
            text, matches = marker.subn(lambda match: match.group(1) + "[REDACTED]", text)
        elif index == 1:
            text, matches = marker.subn(
                lambda match: match.group(1) + match.group(2) + "[REDACTED]" + match.group(4), text
            )
        else:
            text, matches = marker.subn("[REDACTED]", text)
        count += matches
    return text, count


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: scan-evidence.py EVIDENCE_DIR", file=sys.stderr)
        return 2
    root = Path(sys.argv[1])
    if not root.is_dir():
        print(f"evidence directory does not exist: {root}", file=sys.stderr)
        return 2

    files_scanned = 0
    redactions = 0
    affected: list[str] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        sanitized, matches = redact_text(content)
        files_scanned += 1
        if matches:
            path.write_text(sanitized, encoding="utf-8")
            redactions += matches
            affected.append(str(path.relative_to(root)))

    result = {
        "files_scanned": files_scanned,
        "credential_markers_redacted": redactions,
        "affected_files": affected,
        "status": "PASS" if redactions == 0 else "FAIL_REDACTED",
    }
    (root / "credential-scan.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"files_scanned={files_scanned} credential_markers_redacted={redactions} status={result['status']}")
    if affected:
        print("redacted files: " + ", ".join(affected))
    return 0 if redactions == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
