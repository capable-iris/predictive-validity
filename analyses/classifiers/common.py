"""Shared utilities for the classifier scripts.

Includes:
- Anthropic client construction (reads ANTHROPIC_API_KEY from env)
- Rate-limited call wrapper (retries on 429/5xx)
- JSONL append/read helpers (resumability)
- Cost accounting per model
- Prompt-versioning helper

Every classifier records its prompt version explicitly so that re-runs against
newer prompt versions coexist with older records in the DB (per the schema's
`source_version` and `classifier_version` fields).
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover
    Anthropic = None


# Pricing per 1M input / output tokens (as of Aug 2026 — update if prices change).
PRICING = {
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
    "claude-sonnet-4-6":         {"input": 3.0, "output": 15.0},
    "claude-opus-4-7":           {"input": 15.0, "output": 75.0},
}


@dataclass
class CallResult:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model: str


def get_client():
    if Anthropic is None:
        raise RuntimeError("anthropic package not installed. `pip install anthropic`")
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set. See .env.example")
    return Anthropic(api_key=key)


def call_with_retry(
    client,
    model: str,
    system: str,
    user: str,
    max_tokens: int = 2048,
    temperature: float = 0.0,
    max_attempts: int = 5,
) -> CallResult:
    """Call Anthropic; retry with exponential backoff on 429 / 5xx."""
    for attempt in range(max_attempts):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            in_tok = resp.usage.input_tokens
            out_tok = resp.usage.output_tokens
            price = PRICING.get(model)
            if price is None:
                cost = 0.0
            else:
                cost = (in_tok / 1e6) * price["input"] + (out_tok / 1e6) * price["output"]
            text_parts = [b.text for b in resp.content if hasattr(b, "text")]
            return CallResult(
                text="".join(text_parts),
                input_tokens=in_tok,
                output_tokens=out_tok,
                cost_usd=cost,
                model=model,
            )
        except Exception as e:
            if attempt == max_attempts - 1:
                raise
            wait = 2 ** attempt
            print(
                f"  [call retry {attempt+1}/{max_attempts}] {type(e).__name__}: "
                f"waiting {wait}s",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(wait)


def extract_json_block(text: str) -> dict:
    """Extract the first JSON object from an LLM response.

    Robust to code fences and preamble. Raises if no valid JSON found.
    """
    text = text.strip()
    if text.startswith("```"):
        # strip fence
        parts = text.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                text = p
                break
    # Grab from first { to matching }
    start = text.find("{")
    if start < 0:
        raise ValueError(f"no JSON object in response: {text[:200]}")
    depth = 0
    for i, ch in enumerate(text[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError(f"unterminated JSON in response: {text[start:start+200]}")


def annotate(row: dict, result: CallResult, prompt_version: str) -> dict:
    """Add the canonical audit-trail fields to a parsed classifier row.

    Every classifier row records: which model produced it, which prompt version
    generated it, input/output token counts, and USD cost. All computed from
    the API response — the LLM never produces these fields.
    """
    row["_model"] = result.model
    row["_prompt_version"] = prompt_version
    row["_input_tokens"] = result.input_tokens
    row["_output_tokens"] = result.output_tokens
    row["_cost_usd"] = round(result.cost_usd, 6)
    return row


def append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(obj) + "\n")


def read_jsonl(path: Path) -> Iterator[dict]:
    if not path.exists():
        return iter(())
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                pass


def load_processed_keys(path: Path, key_field: str) -> set:
    """Return set of subject keys already present in a JSONL file (for resumability)."""
    seen = set()
    for row in read_jsonl(path):
        k = row.get(key_field)
        if k is not None:
            seen.add(k)
    return seen


def db_conn():
    """Neon connection from DATABASE_URL."""
    import psycopg2
    return psycopg2.connect(os.environ["DATABASE_URL"])
