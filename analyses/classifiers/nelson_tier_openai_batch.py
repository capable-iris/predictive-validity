"""Submit and collect auditable Nelson adjudications with OpenAI Batches.

This is the OpenAI counterpart to ``nelson_tier_batch.py``. It consumes the
same immutable dossiers and writes the same validated result schema, allowing
successful results from both providers to coexist in one resumable output.
Submission starts paid work and therefore requires ``--confirm-paid-batch``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import CallResult, PRICING, append_jsonl, read_jsonl
import nelson_tier_batch as anthropic_batch
import nelson_tier_classify as nelson

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None


DEFAULT_MODEL = "gpt-5.6-luna"
BATCH_PRICE_MULTIPLIER = 0.5
DEFAULT_BATCH_SIZE = 500
DEFAULT_MAX_BATCH_BYTES = 190_000_000
TERMINAL_STATUSES = {"completed", "failed", "expired", "cancelled"}


def get_client():
    if OpenAI is None:
        raise RuntimeError("openai package not installed. `pip install openai`")
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY not set")
    return OpenAI()


def input_sha256(user_prompt: str) -> str:
    return anthropic_batch.input_sha256(user_prompt)


def batch_request(
    dossier: dict[str, Any], model: str, max_chars: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    selected, user = nelson.render_model_input(dossier, max_evidence_chars=max_chars)
    request_id = anthropic_batch.custom_id(str(dossier["pair_key"]))
    request = {
        "custom_id": request_id,
        "method": "POST",
        "url": "/v1/responses",
        "body": {
            "model": model,
            "instructions": nelson.SYSTEM_PROMPT,
            "input": user,
            "max_output_tokens": nelson.MODEL_MAX_TOKENS,
            "reasoning": {"effort": "none"},
            "store": False,
        },
    }
    metadata = {
        "custom_id": request_id,
        "pair_key": dossier["pair_key"],
        "dossier_sha256": dossier["dossier_sha256"],
        "dossier_source_document_id": dossier["dossier_source_document_id"],
        "input_sha256": input_sha256(user),
        "prompt_evidence_chars": selected["selection_summary"]["full_evidence_chars"],
    }
    return request, metadata


def completed_pair_keys(path: Path | None) -> set[str]:
    if path is None:
        return set()
    return {
        str(row["pair_key"])
        for row in read_jsonl(path)
        if row.get("pair_key") is not None
    }


def submitted_pair_keys(path: Path) -> set[str]:
    return {
        str(request["pair_key"])
        for batch in read_jsonl(path)
        for request in batch.get("requests", [])
    }


def submit_chunk(client, requests: list[dict], metadata: list[dict], args) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", encoding="utf-8"
    ) as handle:
        for request in requests:
            handle.write(json.dumps(request, separators=(",", ":")) + "\n")
        handle.flush()
        with open(handle.name, "rb") as upload:
            input_file = client.files.create(file=upload, purpose="batch")
    batch = client.batches.create(
        input_file_id=input_file.id,
        endpoint="/v1/responses",
        completion_window="24h",
        metadata={
            "task": "nelson_tier",
            "prompt_version": nelson.PROMPT_VERSION,
        },
    )
    manifest_row = {
        "schema_version": "nelson_openai_batch_v1",
        "batch_id": batch.id,
        "input_file_id": input_file.id,
        "status": batch.status,
        "model": args.model,
        "prompt_version": nelson.PROMPT_VERSION,
        "dossier_schema_version": nelson.DOSSIER_SCHEMA_VERSION,
        "result_schema_version": nelson.RESULT_SCHEMA_VERSION,
        "max_evidence_chars": args.max_evidence_chars,
        "request_count": len(requests),
        "requests": metadata,
    }
    append_jsonl(args.manifest, manifest_row)
    print(f"submitted batch={batch.id} requests={len(requests)}", flush=True)


def submit(args) -> None:
    if not args.confirm_paid_batch:
        raise SystemExit(
            "batch submission begins paid model work; pass --confirm-paid-batch "
            "only after explicit approval"
        )
    if args.batch_size <= 0 or args.max_batch_bytes <= 0:
        raise SystemExit("--batch-size and --max-batch-bytes must be positive")
    client = get_client()
    skip = submitted_pair_keys(args.manifest) | completed_pair_keys(args.completed_out)
    requests: list[dict] = []
    metadata: list[dict] = []
    request_bytes = 0
    submitted = 0
    for dossier in read_jsonl(args.dossiers):
        pair_key = str(dossier.get("pair_key") or "")
        if not pair_key or pair_key in skip:
            continue
        if dossier.get("schema_version") != nelson.DOSSIER_SCHEMA_VERSION:
            raise ValueError(f"{pair_key} is not a {nelson.DOSSIER_SCHEMA_VERSION} dossier")
        request, request_metadata = batch_request(
            dossier, args.model, args.max_evidence_chars
        )
        size = len(json.dumps(request, separators=(",", ":")).encode("utf-8")) + 1
        if size > args.max_batch_bytes:
            raise ValueError(f"single request {pair_key} exceeds batch byte limit")
        if requests and (
            len(requests) >= args.batch_size
            or request_bytes + size > args.max_batch_bytes
        ):
            submit_chunk(client, requests, metadata, args)
            submitted += len(requests)
            requests, metadata, request_bytes = [], [], 0
        requests.append(request)
        metadata.append(request_metadata)
        request_bytes += size
    if requests:
        submit_chunk(client, requests, metadata, args)
        submitted += len(requests)
    print(f"submitted_requests={submitted} manifest={args.manifest}")


def batch_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price = PRICING.get(model)
    if price is None:
        return 0.0
    return BATCH_PRICE_MULTIPLIER * (
        input_tokens / 1_000_000 * price["input"]
        + output_tokens / 1_000_000 * price["output"]
    )


def response_text(body: dict[str, Any]) -> str:
    parts = []
    for item in body.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                parts.append(str(content.get("text") or ""))
    return "".join(parts)


def collect_batch(client, batch_row, args, offsets, handled) -> tuple[int, int, float]:
    batch_id = str(batch_row["batch_id"])
    batch = client.batches.retrieve(batch_id)
    counts = batch.request_counts.model_dump() if batch.request_counts else None
    print(f"batch={batch_id} status={batch.status} counts={counts}")
    if batch.status not in TERMINAL_STATUSES:
        return 0, 0, 0.0
    # Cancelled and expired batches may expose a partial output file containing
    # every request that finished before termination. Collect those rows so a
    # subsequent manifest needs to resubmit only genuinely missing pairs.
    if not batch.output_file_id:
        if batch_id not in handled:
            append_jsonl(args.errors_out, {
                "batch_id": batch_id,
                "error": f"batch ended with status {batch.status} without output",
            })
            handled.add(batch_id)
        return 0, int((counts or {}).get("total", 0) or 0), 0.0

    request_index = {
        str(request["custom_id"]): request for request in batch_row["requests"]
    }
    content = client.files.content(batch.output_file_id).text
    succeeded = failed = 0
    cost = 0.0
    for line in content.splitlines():
        item = json.loads(line)
        cid = str(item.get("custom_id") or "")
        request = request_index.get(cid)
        if request is None:
            append_jsonl(args.errors_out, {
                "batch_id": batch_id, "custom_id": cid,
                "error": "custom_id absent from local manifest",
            })
            failed += 1
            continue
        pair_key = str(request["pair_key"])
        if pair_key in handled:
            continue
        response = item.get("response") or {}
        body = response.get("body") or {}
        if item.get("error") or int(response.get("status_code") or 0) != 200:
            append_jsonl(args.errors_out, {
                "batch_id": batch_id, "custom_id": cid, "pair_key": pair_key,
                "error": item.get("error") or body.get("error") or response,
            })
            handled.add(pair_key)
            failed += 1
            continue
        try:
            dossier = anthropic_batch.load_dossier(args.dossiers, offsets, pair_key)
            if dossier.get("dossier_sha256") != request["dossier_sha256"]:
                raise ValueError("dossier hash differs from submitted manifest")
            selected, user = nelson.render_model_input(
                dossier, max_evidence_chars=int(batch_row["max_evidence_chars"])
            )
            if input_sha256(user) != request["input_sha256"]:
                raise ValueError("rendered input differs from submitted manifest")
            usage = body.get("usage") or {}
            input_tokens = int(usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
            model = str(body.get("model") or batch_row["model"])
            result = CallResult(
                text=response_text(body),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=batch_cost(model, input_tokens, output_tokens),
                model=model,
                provider_request_id=str(body.get("id") or response.get("request_id") or ""),
                system_prompt=nelson.SYSTEM_PROMPT,
                user_prompt=user,
                max_tokens=nelson.MODEL_MAX_TOKENS,
                temperature=0.0,
                provider="openai",
            )
            row = nelson.parse_model_result(result, dossier, selected)
            row["_batch_id"] = batch_id
            row["_batch_custom_id"] = cid
            row["_model_parameters"]["batch_api"] = True
            row["_model_parameters"]["reasoning_effort"] = "none"
            row["_dossier_file"] = args.dossiers.name
            append_jsonl(args.out, row)
            handled.add(pair_key)
            succeeded += 1
            cost += float(row.get("_cost_usd") or 0.0)
        except Exception as exc:
            append_jsonl(args.errors_out, {
                "batch_id": batch_id, "custom_id": cid, "pair_key": pair_key,
                "error": f"{type(exc).__name__}: {exc}",
                "raw_response": response_text(body),
                "model": body.get("model") or batch_row["model"],
                "provider_request_id": body.get("id") or response.get("request_id"),
            })
            handled.add(pair_key)
            failed += 1
    return succeeded, failed, cost


def collect(args) -> None:
    client = get_client()
    offsets = anthropic_batch.dossier_offsets(args.dossiers)
    handled = completed_pair_keys(args.out)
    handled.update(
        str(row.get("pair_key") or row.get("batch_id"))
        for row in read_jsonl(args.errors_out)
    )
    total_succeeded = total_failed = 0
    total_cost = 0.0
    for batch_row in read_jsonl(args.manifest):
        if batch_row.get("schema_version") != "nelson_openai_batch_v1":
            raise ValueError("unsupported batch manifest schema")
        succeeded, failed, cost = collect_batch(
            client, batch_row, args, offsets, handled
        )
        total_succeeded += succeeded
        total_failed += failed
        total_cost += cost
    print(
        f"collected={total_succeeded} failed={total_failed} "
        f"new_cost=${total_cost:.4f} results={args.out}"
    )


def parse_args(argv: Iterable[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    submit_parser = subparsers.add_parser("submit")
    submit_parser.add_argument("--dossiers", type=Path, required=True)
    submit_parser.add_argument("--manifest", type=Path, required=True)
    submit_parser.add_argument(
        "--completed-out", type=Path,
        help="Existing validated results whose pair keys must not be resubmitted",
    )
    submit_parser.add_argument("--model", default=DEFAULT_MODEL)
    submit_parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    submit_parser.add_argument(
        "--max-batch-bytes", type=int, default=DEFAULT_MAX_BATCH_BYTES
    )
    submit_parser.add_argument(
        "--max-evidence-chars", type=int,
        default=nelson.DEFAULT_MAX_EVIDENCE_CHARS,
    )
    submit_parser.add_argument("--confirm-paid-batch", action="store_true")

    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--dossiers", type=Path, required=True)
    collect_parser.add_argument("--manifest", type=Path, required=True)
    collect_parser.add_argument("--out", type=Path, required=True)
    collect_parser.add_argument("--errors-out", type=Path)
    args = parser.parse_args(argv)
    if args.command == "collect" and args.errors_out is None:
        args.errors_out = args.out.with_name(f"{args.out.stem}.openai-batch-errors.jsonl")
    return args


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv)
    submit(args) if args.command == "submit" else collect(args)


if __name__ == "__main__":
    main()
