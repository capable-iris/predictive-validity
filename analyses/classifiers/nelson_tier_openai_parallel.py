"""Resume missing Nelson adjudications with parallel OpenAI Responses calls.

This is a low-latency fallback for Batch API long tails. It uses the same
immutable dossiers, prompt renderer, deterministic result validator, and
audited result schema as ``nelson_tier_openai_batch.py``. Paid calls require
an explicit confirmation flag.
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from openai import OpenAI

from common import CallResult, PRICING, append_jsonl, read_jsonl
import nelson_tier_classify as nelson
import nelson_tier_openai_batch as batch


def direct_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price = PRICING.get(model)
    if price is None:
        return 0.0
    return (
        input_tokens / 1_000_000 * price["input"]
        + output_tokens / 1_000_000 * price["output"]
    )


def score_one(
    client: OpenAI,
    dossier: dict[str, Any],
    model: str,
    max_evidence_chars: int,
) -> dict[str, Any]:
    selected, user = nelson.render_model_input(
        dossier, max_evidence_chars=max_evidence_chars
    )
    last_error: Exception | None = None
    for attempt in range(6):
        try:
            response = client.responses.create(
                model=model,
                instructions=nelson.SYSTEM_PROMPT,
                input=user,
                max_output_tokens=nelson.MODEL_MAX_TOKENS,
                reasoning={"effort": "none"},
                store=False,
            )
            body = response.model_dump()
            usage = body.get("usage") or {}
            input_tokens = int(usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
            actual_model = str(body.get("model") or model)
            result = CallResult(
                text=batch.response_text(body),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=direct_cost(actual_model, input_tokens, output_tokens),
                model=actual_model,
                provider_request_id=str(body.get("id") or ""),
                system_prompt=nelson.SYSTEM_PROMPT,
                user_prompt=user,
                max_tokens=nelson.MODEL_MAX_TOKENS,
                temperature=0.0,
                provider="openai",
            )
            row = nelson.parse_model_result(result, dossier, selected)
            row["_model_parameters"]["batch_api"] = False
            row["_model_parameters"]["reasoning_effort"] = "none"
            return row
        except Exception as exc:  # retry provider and validation failures
            last_error = exc
            if attempt == 5:
                break
            time.sleep(min(30.0, 2.0 ** attempt))
    assert last_error is not None
    raise last_error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dossiers", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--errors-out", type=Path)
    parser.add_argument("--model", default=batch.DEFAULT_MODEL)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--max-evidence-chars", type=int,
        default=nelson.DEFAULT_MAX_EVIDENCE_CHARS,
    )
    parser.add_argument("--confirm-paid", action="store_true")
    args = parser.parse_args()
    if args.errors_out is None:
        args.errors_out = args.out.with_name(f"{args.out.stem}.parallel-errors.jsonl")
    if args.workers <= 0:
        parser.error("--workers must be positive")
    return args


def main() -> None:
    args = parse_args()
    if not args.confirm_paid:
        raise SystemExit("parallel Responses calls are paid; pass --confirm-paid")
    completed = batch.completed_pair_keys(args.out)
    dossiers = [
        dossier for dossier in read_jsonl(args.dossiers)
        if str(dossier.get("pair_key") or "") not in completed
    ]
    print(f"missing={len(dossiers)} workers={args.workers} model={args.model}")
    client = OpenAI()
    succeeded = failed = 0
    cost = 0.0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        pending = {
            executor.submit(
                score_one, client, dossier, args.model, args.max_evidence_chars
            ): dossier
            for dossier in dossiers[: args.workers * 2]
        }
        next_index = len(pending)
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                dossier = pending.pop(future)
                pair_key = str(dossier["pair_key"])
                try:
                    row = future.result()
                    row["_dossier_file"] = args.dossiers.name
                    append_jsonl(args.out, row)
                    succeeded += 1
                    cost += float(row.get("_cost_usd") or 0.0)
                except Exception as exc:
                    append_jsonl(args.errors_out, {
                        "pair_key": pair_key,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    failed += 1
                if next_index < len(dossiers):
                    next_dossier = dossiers[next_index]
                    next_index += 1
                    pending[executor.submit(
                        score_one, client, next_dossier, args.model,
                        args.max_evidence_chars,
                    )] = next_dossier
                if (succeeded + failed) % 20 == 0:
                    print(
                        f"handled={succeeded + failed}/{len(dossiers)} "
                        f"ok={succeeded} failed={failed} cost=${cost:.2f}",
                        flush=True,
                    )
    print(
        f"completed={succeeded} failed={failed} cost=${cost:.4f} out={args.out}"
    )


if __name__ == "__main__":
    main()
