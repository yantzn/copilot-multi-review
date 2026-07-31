from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from .diff_collector import DiffSummary


@dataclass(frozen=True)
class SecretFinding:
    category: str
    classification: str
    source: str
    file: str | None
    line: int | None
    fingerprint: str


@dataclass(frozen=True)
class SecretScanResult:
    findings: list[SecretFinding]

    @property
    def blocked(self) -> bool:
        return any(item.classification == "confirmed" for item in self.findings)


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


def scan_diff_for_secrets(diff: DiffSummary) -> SecretScanResult:
    findings: list[SecretFinding] = []
    seen: set[tuple[str, str | None, int | None, str]] = set()
    current_file: str | None = None
    for line_number, line in enumerate(diff.diff_text.splitlines(), start=1):
        if line.startswith("+++ b/"):
            current_file = line.removeprefix("+++ b/")
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        content = line[1:]
        for category, pattern in PATTERNS:
            for match in pattern.finditer(content):
                classification = classify_secret_candidate(current_file, content, category)
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
                    )
                )
    return SecretScanResult(findings=findings)


def classify_secret_candidate(file: str | None, line: str, category: str) -> str:
    normalized = (file or "").replace("\\", "/")
    lowered = line.lower()
    if normalized in {"ai_review/secrets.py"} or normalized.startswith("tests/"):
        if "re.compile" in line or "pattern" in lowered or "fixture" in lowered or "dummy" in lowered:
            return "detector_definition" if normalized == "ai_review/secrets.py" else "test_fixture"
    if category == "pem_certificate" and "BEGIN CERTIFICATE" in line and "END CERTIFICATE" not in line:
        if normalized == "ai_review/secrets.py" or normalized.startswith("tests/"):
            return "detector_definition" if normalized == "ai_review/secrets.py" else "test_fixture"
    return "confirmed"


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]
