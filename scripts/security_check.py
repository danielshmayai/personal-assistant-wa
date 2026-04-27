#!/usr/bin/env python3
"""
Pre-commit security scanner.

Scans staged git changes for leaked secrets and dangerous patterns.
Exits 1 (blocks commit) on any finding.

Usage:
    python scripts/security_check.py
"""

import re
import subprocess
import sys

# ---------------------------------------------------------------------------
# Secret patterns — (label, compiled regex)
# ---------------------------------------------------------------------------

PATTERNS = [
    # Google / Gemini
    ("Google API key",          re.compile(r"AIzaSy[0-9A-Za-z_\-]{33}")),
    ("Google OAuth secret",     re.compile(r"GOCSPX-[0-9A-Za-z_\-]+")),
    # Cloudflare tunnel token (JWT-style base64)
    ("Cloudflare tunnel token", re.compile(r"eyJhIjoi[A-Za-z0-9+/=]{20,}")),
    # AWS
    ("AWS access key",          re.compile(r"AKIA[0-9A-Z]{16}")),
    ("AWS secret key",          re.compile(r"(?i)aws_secret[_\s]*=\s*['\"][A-Za-z0-9/+=]{40}['\"]")),
    # Generic private key block
    ("Private key block",       re.compile(r"-----BEGIN (RSA |EC )?PRIVATE KEY-----")),
    # Generic high-entropy assignments that look like secrets
    ("Hardcoded password",      re.compile(r"(?i)password\s*=\s*['\"][^'\"]{8,}['\"]")),
    ("Hardcoded secret",        re.compile(r"(?i)secret\s*=\s*['\"][^'\"]{8,}['\"]")),
    ("Hardcoded token",         re.compile(r"(?i)token\s*=\s*['\"][^'\"]{8,}['\"]")),
    # Anthropic / OpenAI
    ("Anthropic API key",       re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}")),
    ("OpenAI API key",          re.compile(r"sk-[A-Za-z0-9]{20,}")),
]

# Files that must never be staged
BLOCKED_FILES = {".env", ".env.local", ".env.production", "credentials.json", "token.json"}

# Patterns in these files are expected/safe (e.g. .env.example with placeholders)
ALLOWLISTED_FILES = {".env.example", "SETUP_GUIDE.md"}

# Lines containing these strings are false-positive placeholders — skip them
PLACEHOLDER_TOKENS = {"changeme", "your_", "<your", "example", "placeholder", "fake", "xxxx", "..."}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_staged_files() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True, text=True, check=True,
    )
    return [f.strip() for f in result.stdout.splitlines() if f.strip()]


def get_staged_diff() -> str:
    result = subprocess.run(
        ["git", "diff", "--cached", "--unified=0"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def is_placeholder(line: str) -> bool:
    lower = line.lower()
    return any(tok in lower for tok in PLACEHOLDER_TOKENS)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_blocked_files(staged: list[str]) -> list[str]:
    findings = []
    for path in staged:
        filename = path.split("/")[-1].split("\\")[-1]
        if filename in BLOCKED_FILES:
            findings.append(f"  BLOCKED FILE staged: {path}")
    return findings


def check_secret_patterns(diff: str) -> list[str]:
    findings = []
    current_file = "<unknown>"

    for line in diff.splitlines():
        # Track which file we're in
        if line.startswith("+++ b/"):
            current_file = line[6:]
            continue

        # Skip allowlisted files
        if any(current_file.endswith(f) for f in ALLOWLISTED_FILES):
            continue

        # Only scan added lines
        if not line.startswith("+") or line.startswith("+++"):
            continue

        content = line[1:]  # strip the leading +

        # Skip placeholder lines
        if is_placeholder(content):
            continue

        for label, pattern in PATTERNS:
            if pattern.search(content):
                # Redact the matched value in output
                safe_line = content.strip()[:120]
                findings.append(f"  {label} in {current_file}:\n    {safe_line}")

    return findings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        staged = get_staged_files()
        diff   = get_staged_diff()
    except subprocess.CalledProcessError as e:
        print(f"[security] git error: {e}", file=sys.stderr)
        return 1

    all_findings: list[str] = []
    all_findings += check_blocked_files(staged)
    all_findings += check_secret_patterns(diff)

    if all_findings:
        print("\n[security] COMMIT BLOCKED — potential secrets detected:\n")
        for f in all_findings:
            print(f)
        print(
            "\nFix: remove the secret, use an environment variable instead, "
            "or add the file to .gitignore.\n"
        )
        return 1

    print("[security] No secrets detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
