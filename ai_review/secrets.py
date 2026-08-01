from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any

from .diff_collector import DiffSummary


@dataclass(frozen=True)
class SecretFinding:
    category: str
    classification: str
    source: str
    file: str | None
    line: int | None
    fingerprint: str
    blocking: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class SecretScanResult:
    findings: list[SecretFinding]

    @property
    def blocked(self) -> bool:
        return any(item.blocking for item in self.findings)


PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pem_private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("pem_certificate", re.compile(r"-----BEGIN CERTIFICATE-----")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_-]{20,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("bearer_token", re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE)),
    ("cookie", re.compile(r"Cookie:\s*[^;\n]+=[^;\n]{12,}", re.IGNORECASE)),
    ("password_assignment", re.compile(r"(?i)\b(password|api[_-]?key|access[_-]?token|secret)\b\s*[:=]\s*['\"]?[^'\"\s]{12,}")),
    ("database_url", re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^:\s]+:[^@\s]+@[^/\s]+")),
)

PEM_END_MARKERS = {
    "pem_certificate": ("-----END CERTIFICATE-----",),
    "pem_private_key": (
        "-----END PRIVATE KEY-----",
        "-----END RSA PRIVATE KEY-----",
        "-----END EC PRIVATE KEY-----",
        "-----END OPENSSH PRIVATE KEY-----",
        "-----END DSA PRIVATE KEY-----",
    ),
}

PAYLOAD_EXCLUDED_KEYS = {"secret_findings", "excluded_files"}
BASE64_LINE = re.compile(r"^[A-Za-z0-9+/=]{4,}$")


def scan_diff_for_secrets(diff: DiffSummary) -> SecretScanResult:
    findings: list[SecretFinding] = []
    seen: set[tuple[str, str | None, int | None, str]] = set()
    current_file: str | None = None
    lines = diff.diff_text.splitlines()
    for index, line in enumerate(lines):
        line_number = index + 1
        if line.startswith("+++ b/"):
            current_file = line.removeprefix("+++ b/")
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        content = line[1:]
        for category, pattern in PATTERNS:
            for match in pattern.finditer(content):
                classification, blocking, reason = classify_secret_candidate(current_file, content, category, lines[index + 1 :])
                fingerprint = _fingerprint(match.group(0))
                key = (category, current_file, line_number, fingerprint)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    SecretFinding(
                        category=category,
                        classification=classification,
                        source="diff",
                        file=current_file,
                        line=line_number,
                        fingerprint=fingerprint,
                        blocking=blocking,
                        reason=reason,
                    )
                )
    return SecretScanResult(findings=findings)


def scan_payload(payload: Any) -> SecretScanResult:
    findings: list[SecretFinding] = []
    seen: set[tuple[str, str | None, int | None, str]] = set()

    def walk(value: Any, path: list[str], context_file: str | None) -> None:
        if isinstance(value, dict):
            next_context = str(value.get("file") or value.get("path") or context_file) if value else context_file
            for child_key, child in value.items():
                if child_key in PAYLOAD_EXCLUDED_KEYS:
                    continue
                if path == [] and child_key == "diff":
                    continue
                walk(child, [*path, str(child_key)], next_context)
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, [*path, str(index)], context_file)
            return
        if not isinstance(value, str):
            return

        source_path = ".".join(path)
        for category, pattern in PATTERNS:
            for match in pattern.finditer(value):
                classification, blocking, reason = classify_secret_candidate(context_file, value, category, [])
                fingerprint = _fingerprint(match.group(0))
                key = (category, context_file, None, fingerprint)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    SecretFinding(
                        category=category,
                        classification=classification,
                        source=f"payload:{source_path}",
                        file=context_file,
                        line=None,
                        fingerprint=fingerprint,
                        blocking=blocking,
                        reason=reason,
                    )
                )

    walk(payload, [], None)
    return SecretScanResult(findings)


def combine_secret_scans(*scans: SecretScanResult) -> SecretScanResult:
    findings: list[SecretFinding] = []
    seen: set[tuple[str, str | None, int | None, str]] = set()
    for scan in scans:
        for finding in scan.findings:
            key = (finding.category, finding.file, finding.line, finding.fingerprint)
            if key in seen:
                continue
            seen.add(key)
            findings.append(finding)
    return SecretScanResult(findings)


def classify_secret_candidate(file: str | None, line: str, category: str, following_lines: list[str] | None = None) -> tuple[str, bool, str | None]:
    normalized = (file or "").replace("\\", "/")
    lowered = line.lower()
    if normalized in {"ai_review/secrets.py"} or normalized.startswith("tests/"):
        if (
            "re.compile" in line
            or "pattern" in lowered
            or "fixture" in lowered
            or "dummy" in lowered
            or "write_text" in line
            or "diff_text" in line
        ):
            classification = "detector_definition" if normalized == "ai_review/secrets.py" else "test_fixture"
            return classification, False, None
    if normalized.startswith("docs/") or normalized.lower().endswith((".md", ".rst", ".txt")):
        if "example" in lowered or "sample" in lowered or "dummy" in lowered or "placeholder" in lowered:
            return "documentation_sample", False, None
    if category in PEM_END_MARKERS and not _pem_has_real_block(category, line, following_lines or []):
        if normalized == "ai_review/secrets.py" or normalized.startswith("tests/"):
            classification = "detector_definition" if normalized == "ai_review/secrets.py" else "test_fixture"
            return classification, False, None
        return "confirmed", True, "malformed_pem"
    return "confirmed", True, None


def _pem_has_real_block(category: str, start_line: str, following_lines: list[str]) -> bool:
    end_markers = PEM_END_MARKERS.get(category)
    if not end_markers:
        return True
    body_lines = 0
    for raw_line in [start_line, *following_lines]:
        stripped = raw_line.removeprefix("+").strip().strip("'\"")
        if any(stripped.startswith(marker) for marker in end_markers):
            return body_lines > 0
        if stripped.startswith("-----BEGIN "):
            continue
        if stripped.startswith("-----END "):
            return False
        if BASE64_LINE.fullmatch(stripped):
            body_lines += 1
    return False


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]
