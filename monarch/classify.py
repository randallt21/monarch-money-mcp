"""
Local-Claude-CLI merchant categorizer.

Classifies unknown merchants using the local `claude` CLI on the user's
existing subscription — no Anthropic API key required.

The subprocess mirrors the codex_audit.py pattern: binary resolved via
shutil.which, one batched call per maintain run, timeout with graceful skip
on any failure so maintain never breaks due to classifier unavailability.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from typing import Any

logger = logging.getLogger(__name__)

UNCERTAIN_SENTINEL = "__uncertain__"
CLASSIFY_TIMEOUT_SECONDS = 90
_CLAUDE_FALLBACK = "/Users/randall/.local/bin/claude"


def _find_claude() -> str:
    """Resolve the real claude binary (not a shell function wrapper)."""
    return shutil.which("claude") or _CLAUDE_FALLBACK


def _build_schema(category_names: list[str]) -> str:
    enum_values = [*category_names, UNCERTAIN_SENTINEL]
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "merchant": {"type": "string"},
                        "category": {"type": "string", "enum": enum_values},
                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                        },
                    },
                    "required": ["merchant", "category", "confidence"],
                },
            }
        },
        "required": ["results"],
    }
    return json.dumps(schema)


def _build_prompt(merchants: list[str], category_names: list[str]) -> str:
    cats = ", ".join(category_names)
    merchant_list = "\n".join(f"- {m}" for m in merchants)
    return (
        f"Classify each merchant below into the most appropriate category.\n"
        f"Valid categories: {cats}\n"
        f"Use '{UNCERTAIN_SENTINEL}' if you have no reasonable guess.\n\n"
        f"Merchants to classify:\n{merchant_list}"
    )


def classify_merchants(
    merchants: list[str],
    category_names: list[str],
    *,
    timeout: int = CLASSIFY_TIMEOUT_SECONDS,
) -> dict[str, dict[str, Any]]:
    """
    Classify a list of merchant names using the local claude CLI.

    Returns {merchant_name: {"category": str, "confidence": float}}.
    Merchants that get UNCERTAIN_SENTINEL are excluded from the result.
    Returns {} on any subprocess error, timeout, or parse failure —
    the caller must treat an empty result as "no guesses available".
    """
    if not merchants or not category_names:
        return {}

    claude_bin = _find_claude()
    schema = _build_schema(category_names)
    prompt = _build_prompt(merchants, category_names)

    system_prompt = (
        "You output ONLY a raw JSON object matching the provided schema. "
        "No markdown, no code fences, no explanations. "
        "Classify each merchant into the most specific matching category."
    )

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    claude_bin,
                    "--print",
                    "--model", "haiku",
                    "--output-format", "json",
                    "--json-schema", schema,
                    "--no-session-persistence",
                    "--disallowedTools", "*",
                    "--append-system-prompt", system_prompt,
                ],
                input=prompt,
                text=True,
                capture_output=True,
                timeout=timeout,
                # Neutral cwd avoids loading the repo's CLAUDE.md into the prompt,
                # keeping the call cheap and deterministic.
                cwd=tmpdir,
            )
    except subprocess.TimeoutExpired:
        logger.warning("classify_merchants: timed out after %ds", timeout)
        return {}
    except FileNotFoundError:
        logger.warning("classify_merchants: claude binary not found at %s", claude_bin)
        return {}
    except Exception:
        logger.exception("classify_merchants: unexpected error invoking claude")
        return {}

    if result.returncode != 0:
        logger.warning(
            "classify_merchants: claude exited %d stderr=%s",
            result.returncode,
            (result.stderr or "")[:200],
        )
        return {}

    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning("classify_merchants: could not parse claude JSON envelope")
        return {}

    if envelope.get("is_error"):
        logger.warning("classify_merchants: claude reported is_error=true in envelope")
        return {}

    structured = envelope.get("structured_output")
    if not structured:
        logger.warning("classify_merchants: no structured_output in envelope")
        return {}

    guesses: dict[str, dict[str, Any]] = {}
    for item in (structured.get("results") or []):
        merchant = str(item.get("merchant") or "")
        category = str(item.get("category") or "")
        confidence = float(item.get("confidence") or 0)
        if merchant and category and category != UNCERTAIN_SENTINEL:
            guesses[merchant] = {"category": category, "confidence": confidence}

    return guesses
