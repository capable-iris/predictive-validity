"""Submit and collect auditable Nelson adjudications with Message Batches.

Prepare dossiers first with ``nelson_tier_classify.py --prepare-only``. Batch
submission starts paid asynchronous model work and therefore requires the
explicit ``--confirm-paid-batch`` flag. Collection is resumable and validates
each response against the exact immutable dossier used at submission.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import CallResult, PRICING, append_jsonl, get_client, read_jsonl
import nelson_tier_classify as nelson


BATCH_PRICE_MULTIPLIER = 0.5
DEFAULT_BATCH_SIZE = 500
DEFAULT_MAX_BATCH_BYTES = 200_000_000


def custom_id(pair_key: str) -> str:
    digest = hashlib.sha256(pair_key.encode("utf-8")).hexdigest()[:32]
    return f"nelson_{digest}"


def input_sha256(user_prompt: str) -> str:
    value = f"{nelson.SYSTEM_PROMPT}\0{user_prompt}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def batch_request(dossier: dict[str, Any], model: str, max_chars: int) -> tuple[dict, dict]:
    selected, user = nelson.render_model_input(dossier, max_evidence_chars=max_chars)
    request_id = custom_id(str(dossier["pair_key"]))
    request = {
        "custom_id": request_id,
        "params": {
            "model": model,
            "max_tokens": nelson.MODEL_MAX_TOKENS,
            "temperature": 0.0,
            "system": nelson.SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user}],
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


def submitted_pair_keys(manifest: Path) -> set[str]:
    return {
        str(request["pair_key"])
        for batch in read_jsonl(manifest)
        for request in batch.get("requests", [])
    }


def submit_chunk(client, requests: list[dict], metadata: list[dict], args) -> None:
    batch = client.messages.batches.create(requests=requests)
    manifest_row = {
        "schema_version": "nelson_message_batch_v1",
        "batch_id": batch.id,
        "processing_status": batch.processing_status,
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
    client = get_client()
    if args.batch_size <= 0 or args.max_batch_bytes <= 0:
        raise SystemExit("--batch-size and --max-batch-bytes must be positive")
    already_submitted = submitted_pair_keys(args.manifest)
    requests: list[dict] = []
    metadata: list[dict] = []
    request_bytes = 0
    submitted = 0

    for dossier in read_jsonl(args.dossiers):
        pair_key = str(dossier.get("pair_key") or "")
        if not pair_key or pair_key in already_submitted:
            continue
        if dossier.get("schema_version") != nelson.DOSSIER_SCHEMA_VERSION:
            raise ValueError(f"{pair_key} is not a {nelson.DOSSIER_SCHEMA_VERSION} dossier")
        request, request_metadata = batch_request(
            dossier, args.model, args.max_evidence_chars
        )
        size = len(json.dumps(request, separators=(",", ":")).encode("utf-8"))
        if size > args.max_batch_bytes:
            raise ValueError(
                f"single request {pair_key} is {size} bytes, above batch byte limit"
            )
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
    standard = (
        input_tokens / 1_000_000 * price["input"]
        + output_tokens / 1_000_000 * price["output"]
    )
    return standard * BATCH_PRICE_MULTIPLIER


def model_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


def dossier_offsets(path: Path) -> dict[str, int]:
    return nelson.jsonl_offsets(path, "pair_key")


def load_dossier(path: Path, offsets: dict[str, int], pair_key: str) -> dict[str, Any]:
    offset = offsets.get(pair_key)
    if offset is None:
        raise ValueError(f"dossier missing for pair {pair_key}")
    dossier = nelson.read_jsonl_at(path, offset)
    if dossier.get("schema_version") != nelson.DOSSIER_SCHEMA_VERSION:
        raise ValueError(f"wrong dossier schema for pair {pair_key}")
    return dossier


def collect_batch(client, batch_row: dict, args, offsets: dict[str, int], done: set[str]) -> tuple[int, int, float]:
    batch_id = str(batch_row["batch_id"])
    status = client.messages.batches.retrieve(batch_id)
    counts = model_dump(status.request_counts)
    print(f"batch={batch_id} status={status.processing_status} counts={counts}")
    if status.processing_status != "ended":
        return 0, 0, 0.0

    request_index = {
        str(request["custom_id"]): request for request in batch_row["requests"]
    }
    succeeded = 0
    failed = 0
    cost = 0.0
    for item in client.messages.batches.results(batch_id):
        request = request_index.get(str(item.custom_id))
        if request is None:
            append_jsonl(args.errors_out, {
                "batch_id": batch_id,
                "custom_id": str(item.custom_id),
                "error": "custom_id absent from local manifest",
            })
            failed += 1
            continue
        pair_key = str(request["pair_key"])
        if pair_key in done:
            continue
        if item.result.type != "succeeded":
            append_jsonl(args.errors_out, {
                "batch_id": batch_id,
                "custom_id": str(item.custom_id),
                "pair_key": pair_key,
                "result": model_dump(item.result),
            })
            failed += 1
            continue
        try:
            dossier = load_dossier(args.dossiers, offsets, pair_key)
            if dossier.get("dossier_sha256") != request["dossier_sha256"]:
                raise ValueError("dossier hash differs from submitted manifest")
            selected, user = nelson.render_model_input(
                dossier,
                max_evidence_chars=int(batch_row["max_evidence_chars"]),
            )
            if input_sha256(user) != request["input_sha256"]:
                raise ValueError("rendered input differs from submitted manifest")
            message = item.result.message
            text = "".join(
                block.text for block in message.content if hasattr(block, "text")
            )
            input_tokens = int(message.usage.input_tokens)
            output_tokens = int(message.usage.output_tokens)
            result = CallResult(
                text=text,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=batch_cost(str(message.model), input_tokens, output_tokens),
                model=str(message.model),
                provider_request_id=str(message.id),
                system_prompt=nelson.SYSTEM_PROMPT,
                user_prompt=user,
                max_tokens=nelson.MODEL_MAX_TOKENS,
                temperature=0.0,
            )
            row = nelson.parse_model_result(result, dossier, selected)
            row["_batch_id"] = batch_id
            row["_batch_custom_id"] = str(item.custom_id)
            row["_model_parameters"]["batch_api"] = True
            row["_dossier_file"] = args.dossiers.name
            append_jsonl(args.out, row)
            done.add(pair_key)
            succeeded += 1
            cost += float(row.get("_cost_usd") or 0.0)
        except Exception as exc:
            append_jsonl(args.errors_out, {
                "batch_id": batch_id,
                "custom_id": str(item.custom_id),
                "pair_key": pair_key,
                "error": f"{type(exc).__name__}: {exc}",
            })
            failed += 1
    return succeeded, failed, cost


def collect(args) -> None:
    client = get_client()
    offsets = dossier_offsets(args.dossiers)
    done = {
        str(row["pair_key"])
        for row in read_jsonl(args.out)
        if row.get("pair_key") is not None
    }
    total_succeeded = 0
    total_failed = 0
    total_cost = 0.0
    for batch_row in read_jsonl(args.manifest):
        if batch_row.get("schema_version") != "nelson_message_batch_v1":
            raise ValueError("unsupported batch manifest schema")
        succeeded, failed, cost = collect_batch(
            client, batch_row, args, offsets, done
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
    submit_parser.add_argument("--model", default=nelson.DEFAULT_MODEL)
    submit_parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    submit_parser.add_argument(
        "--max-batch-bytes", type=int, default=DEFAULT_MAX_BATCH_BYTES
    )
    submit_parser.add_argument(
        "--max-evidence-chars",
        type=int,
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
        args.errors_out = args.out.with_name(f"{args.out.stem}.batch-errors.jsonl")
    return args


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv)
    if args.command == "submit":
        submit(args)
    else:
        collect(args)


if __name__ == "__main__":
    main()
