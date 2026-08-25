"""Incrementally ingest audited classifier JSONL outputs.

This is the narrow JSONL -> Postgres bridge for new paid LLM runs. It does
not retrieve sources or call a model, and it deliberately does not rerun the
big-bang ``02_ingest.py`` loader.

Examples:
    .venv/bin/dotenv run -- .venv/bin/python db/13_ingest_llm_outputs.py \
      --task why-stopped data/clinical_trials/why_stopped_2026.jsonl

    .venv/bin/dotenv run -- .venv/bin/python db/13_ingest_llm_outputs.py \
      --task target-literature data/target_evidence/literature_scores_2026.jsonl

    .venv/bin/dotenv run -- .venv/bin/python db/13_ingest_llm_outputs.py \
      --task nelson-tier data/target_evidence/nelson_tiers_all_v5.jsonl

    .venv/bin/dotenv run -- .venv/bin/python db/13_ingest_llm_outputs.py \
      --task nelson-tier --preflight data/target_evidence/nelson_tiers_all_v5.jsonl

Migration ``10_clinical_trial_source_audit.sql`` must be applied first. New
rows are required to contain the exact audit metadata written by
``analyses/classifiers/common.py``. The whole invocation is transactional.
``--dry-run`` executes the task's write path and then rolls back. Cohort-wide Nelson
files should use ``--preflight`` first: it performs local audit validation and
set-based database reference checks through temporary COPY staging.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import sys
import uuid
import zlib
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

import psycopg2
from psycopg2.extras import Json


TASKS = (
    "why-stopped",
    "target-literature",
    "silent-kill",
    "drug-evidence",
    "nelson-tier",
)
REQUIRED_AUDIT_FIELDS = (
    "_run_id",
    "_model",
    "_prompt_version",
    "_system_prompt",
    "_user_prompt",
    "_raw_response",
)

# These audit-envelope fields have dedicated normalized columns/tables. Keeping
# them inside parsed_output as well would duplicate the largest payloads (most
# notably the exact user prompt) without adding any provenance.
REDUNDANT_PARSED_OUTPUT_FIELDS = frozenset(
    {
        "_system_prompt",
        "_user_prompt",
        "_raw_response",
        "_source_documents",
    }
)

TARGET_DIMENSIONS = (
    ("line_b", "line_b_lit", "B_mechanistic"),
    ("line_c", "line_c_lit", "C_cell"),
    ("line_d", "line_d_lit", "D_animal"),
    ("line_e", "line_e_lit", "E_pd"),
)

DRUG_NUMERIC_DIMENSIONS = (
    ("cell_efficacy_score", "drug_cell_efficacy", "C_cell"),
    ("rodent_efficacy_score", "drug_rodent_efficacy", "D_animal"),
    ("non_rodent_efficacy_score", "drug_nonrodent_efficacy", "D_animal"),
    ("preclinical_tox_signal", "drug_tox_signal", "D_animal"),
    ("target_engagement_score", "drug_target_engagement", "E_pd"),
    ("structural_biology_score", "drug_structural_biology", "G_pharmacology"),
)

DRUG_TEXT_DIMENSIONS = (
    ("phase2_endpoint", "drug_phase2_endpoint", "F_clinical"),
    ("phase3_endpoint", "drug_phase3_endpoint", "F_clinical"),
    ("effect_size_reported", "drug_effect_size", "F_clinical"),
    ("cell_effect_direction", "drug_cell_direction", "C_cell"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--task", choices=TASKS, required=True)
    parser.add_argument("inputs", nargs="+", type=Path, metavar="JSONL")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and execute all SQL, then roll the transaction back",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help=(
            "bulk-validate Nelson inputs without executing persistent inserts; "
            "uses temporary COPY staging and set-based reference checks"
        ),
    )
    parser.add_argument(
        "--direct-db",
        action="store_true",
        help="replace a Neon -pooler host with its direct endpoint for bulk COPY",
    )
    return parser.parse_args()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def prompt_hash(system_prompt: str, user_prompt: str) -> str:
    canonical = json.dumps(
        {"system": system_prompt, "user": user_prompt},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256_text(canonical)


def parsed_output_payload(row: dict) -> dict:
    """Return model output/metadata without duplicated audit-envelope fields."""
    return {
        key: value
        for key, value in row.items()
        if key not in REDUNDANT_PARSED_OUTPUT_FIELDS
    }


def compress_user_prompt(prompt: str) -> tuple[str, int]:
    """Return base64 zlib bytes for COPY plus the exact UTF-8 byte count."""
    raw = prompt.encode("utf-8")
    compressed = zlib.compress(raw, level=6)
    return base64.b64encode(compressed).decode("ascii"), len(raw)


def restore_user_prompt(
    user_prompt: str | None,
    user_prompt_compressed: bytes | None,
    compression: str | None,
) -> str | None:
    """Read either legacy plain text or the compressed exact prompt."""
    if user_prompt is not None:
        return user_prompt
    if user_prompt_compressed is None:
        return None
    if compression != "zlib":
        raise ValueError(f"unsupported user-prompt compression: {compression!r}")
    return zlib.decompress(bytes(user_prompt_compressed)).decode("utf-8")


def direct_database_url(database_url: str) -> str:
    """Return the matching direct Neon URL without exposing credentials."""
    parsed = urlsplit(database_url)
    if "-pooler." not in (parsed.hostname or ""):
        raise ValueError("--direct-db requires a Neon URL with a -pooler host")
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc.replace("-pooler.", ".", 1),
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


def read_cost(row: dict):
    for key in ("_cost_usd", "_cost_share", "_cost"):
        if row.get(key) is not None:
            return row[key]
    return None


def model_family(model: str) -> str:
    lowered = model.lower()
    if "haiku" in lowered:
        return "claude-haiku"
    if "sonnet" in lowered:
        return "claude-sonnet"
    return model


def normalize_drug(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower().strip())


def evidence_snapshot(
    *,
    subject_type: str,
    subject_id: int,
    subject_id2: int | None = None,
    dimension: str,
    category: str,
    source: str,
    version: str,
    model: str,
    value_numeric=None,
    value_text=None,
    value_json=None,
    confidence=None,
    citation_pmids=None,
) -> dict:
    """Return the immutable fact value produced by one extraction run."""
    return {
        "subject_type": subject_type,
        "subject_id": subject_id,
        "subject_id2": subject_id2,
        "dimension": dimension,
        "category": category,
        "value_numeric": value_numeric,
        "value_text": value_text,
        "value_boolean": None,
        "value_json": value_json,
        "source": source,
        "source_version": version,
        "confidence": confidence,
        "citation_pmids": [str(value) for value in (citation_pmids or [])],
        "extracted_by": model,
    }


def read_jsonl(paths: Iterable[Path]):
    for path in paths:
        if not path.is_file():
            raise ValueError(f"input does not exist or is not a file: {path}")
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
                if not isinstance(row, dict):
                    raise ValueError(f"{path}:{line_number}: expected a JSON object")
                yield path, line_number, row


def validate_audit_row(path: Path, line_number: int, row: dict) -> uuid.UUID:
    missing = [key for key in REQUIRED_AUDIT_FIELDS if row.get(key) in (None, "")]
    if missing:
        raise ValueError(
            f"{path}:{line_number}: missing exact audit fields: {', '.join(missing)}"
        )
    try:
        return uuid.UUID(str(row["_run_id"]))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path}:{line_number}: _run_id is not a UUID") from exc


def require_source_inputs(row: dict, task: str, expected_count: int | None = None) -> None:
    sources = row.get("_source_documents")
    if not isinstance(sources, list) or not sources:
        raise ValueError(f"{task} row has no canonical source-document inputs")
    if expected_count is not None and len(sources) != expected_count:
        raise ValueError(
            f"{task} row reports {expected_count} model inputs but links {len(sources)} sources"
        )
    if any(
        not isinstance(source, dict)
        or (
            source.get("relationship") != "dossier_snapshot"
            and not isinstance(source.get("excerpt_text"), str)
        )
        for source in sources
    ):
        raise ValueError(f"{task} source inputs must include the exact excerpt_text")


def require_schema(cur) -> None:
    cur.execute(
        """
        SELECT to_regclass('preclin.llm_run'),
               to_regclass('preclin.llm_run_source'),
               to_regclass('preclin.llm_run_evidence_score')
        """
    )
    if any(value is None for value in cur.fetchone()):
        raise RuntimeError("apply db/10_clinical_trial_source_audit.sql first")


def _copy_rows(cur, table: str, columns: tuple[str, ...], rows: list[tuple]) -> None:
    """COPY compact, already-validated scalar rows into a temporary table."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter="\t", lineterminator="\n")
    for row in rows:
        writer.writerow([r"\N" if value is None else value for value in row])
    buffer.seek(0)
    column_sql = ", ".join(columns)
    cur.copy_expert(
        f"COPY {table} ({column_sql}) FROM STDIN "
        "WITH (FORMAT CSV, DELIMITER E'\\t', NULL '\\N')",
        buffer,
    )


def _require_no_preflight_rows(cur, label: str, query: str) -> None:
    cur.execute(query)
    examples = cur.fetchmany(10)
    if examples:
        raise ValueError(f"Nelson preflight {label}; examples={examples}")


def nelson_preflight(conn, inputs: list[Path]) -> None:
    """Validate a complete Nelson file with local checks and bulk SQL joins.

    Unlike ``--dry-run``, this path never executes the persistent INSERT/UPSERT
    statements. It validates the same identities and source references using
    compact temporary staging tables, avoiding per-row network round trips and
    multi-gigabyte transient writes.
    """
    cur = conn.cursor()
    require_schema(cur)
    cur.execute(
        """
        CREATE TEMP TABLE nelson_preflight_row (
            line_number integer NOT NULL,
            pair_key text NOT NULL,
            target_id bigint NOT NULL,
            indication_id bigint NOT NULL,
            gene text,
            dossier_document_id bigint NOT NULL,
            dossier_sha256 text NOT NULL,
            run_id uuid NOT NULL,
            provider text NOT NULL,
            model text NOT NULL,
            prompt_version text NOT NULL,
            input_sha256 text NOT NULL,
            output_sha256 text NOT NULL
        ) ON COMMIT DROP;

        CREATE TEMP TABLE nelson_preflight_source (
            line_number integer NOT NULL,
            pair_key text NOT NULL,
            run_id uuid NOT NULL,
            source_document_id bigint NOT NULL,
            relationship text NOT NULL,
            ordinal integer NOT NULL,
            excerpt_sha256 text
        ) ON COMMIT DROP
        """
    )

    staged_rows: list[tuple] = []
    staged_sources: list[tuple] = []
    pair_keys: set[str] = set()
    run_ids: set[str] = set()
    per_file_counts = {path: 0 for path in inputs}
    for path, line_number, row in read_jsonl(inputs):
        run_id = str(validate_audit_row(path, line_number, row))
        if row.get("schema_version") != "nelson_tier_result_v5":
            raise ValueError(
                f"{path}:{line_number}: Nelson row must use nelson_tier_result_v5"
            )
        tier = str(row.get("tier") or "").upper()
        if tier not in {"T0", "T1", "T2", "T3"}:
            raise ValueError(f"{path}:{line_number}: invalid Nelson tier {tier!r}")
        try:
            target_id = int(row["target_id"])
            indication_id = int(row["indication_id"])
            dossier_document_id = int(row["dossier_source_document_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"{path}:{line_number}: invalid target, indication, or dossier ID"
            ) from exc
        pair_key = f"{target_id}:{indication_id}"
        if row.get("pair_key") != pair_key:
            raise ValueError(
                f"{path}:{line_number}: pair_key must be {pair_key}"
            )
        if pair_key in pair_keys:
            raise ValueError(f"{path}:{line_number}: duplicate pair_key {pair_key}")
        if run_id in run_ids:
            raise ValueError(f"{path}:{line_number}: duplicate run_id {run_id}")
        pair_keys.add(pair_key)
        run_ids.add(run_id)

        sources = row.get("_source_documents")
        if not isinstance(sources, list):
            raise ValueError(
                f"{path}:{line_number}: Nelson row must include _source_documents"
            )
        require_source_inputs(row, "nelson-tier")
        dossier_links = [
            source
            for source in sources
            if source.get("relationship") == "dossier_snapshot"
            and source.get("source_document_id") == dossier_document_id
        ]
        if len(dossier_links) != 1:
            raise ValueError(
                f"{path}:{line_number}: row must link its one canonical dossier"
            )

        source_keys: dict[tuple[int, str, int], str | None] = {}
        for fallback_ordinal, source in enumerate(sources):
            document_id = source.get("source_document_id")
            if not isinstance(document_id, int):
                raise ValueError(
                    f"{path}:{line_number}: source_document_id must be an integer"
                )
            relationship = source.get("relationship") or "model_input"
            ordinal = source.get("ordinal", fallback_ordinal)
            if not isinstance(ordinal, int) or ordinal < 0:
                raise ValueError(
                    f"{path}:{line_number}: source ordinal must be non-negative"
                )
            excerpt = source.get("excerpt_text")
            if excerpt is not None and not isinstance(excerpt, str):
                raise ValueError(
                    f"{path}:{line_number}: excerpt_text must be a string or null"
                )
            excerpt_hash = sha256_text(excerpt) if excerpt is not None else None
            source_key = (document_id, relationship, ordinal)
            if source_key in source_keys and source_keys[source_key] != excerpt_hash:
                raise ValueError(
                    f"{path}:{line_number}: duplicate source key has different excerpt"
                )
            source_keys[source_key] = excerpt_hash
        for (document_id, relationship, ordinal), excerpt_hash in source_keys.items():
            staged_sources.append(
                (
                    line_number,
                    pair_key,
                    run_id,
                    document_id,
                    relationship,
                    ordinal,
                    excerpt_hash,
                )
            )

        system_prompt = row["_system_prompt"]
        user_prompt = row["_user_prompt"]
        raw_response = row["_raw_response"]
        staged_rows.append(
            (
                line_number,
                pair_key,
                target_id,
                indication_id,
                row.get("gene"),
                dossier_document_id,
                str(row.get("dossier_sha256") or ""),
                run_id,
                str(row.get("_provider") or "anthropic"),
                str(row["_model"]),
                str(row["_prompt_version"]),
                prompt_hash(system_prompt, user_prompt),
                sha256_text(raw_response),
            )
        )
        per_file_counts[path] += 1

    if not staged_rows:
        raise ValueError("Nelson preflight received no rows")
    _copy_rows(
        cur,
        "nelson_preflight_row",
        (
            "line_number", "pair_key", "target_id", "indication_id", "gene",
            "dossier_document_id", "dossier_sha256", "run_id", "provider",
            "model", "prompt_version", "input_sha256", "output_sha256",
        ),
        staged_rows,
    )
    _copy_rows(
        cur,
        "nelson_preflight_source",
        (
            "line_number", "pair_key", "run_id", "source_document_id",
            "relationship", "ordinal", "excerpt_sha256",
        ),
        staged_sources,
    )
    cur.execute("ANALYZE nelson_preflight_row; ANALYZE nelson_preflight_source")

    _require_no_preflight_rows(
        cur,
        "found unknown or mismatched targets/indications",
        """
        SELECT s.line_number, s.pair_key, s.gene, t.symbol, i.display_name
        FROM nelson_preflight_row s
        LEFT JOIN public.targets t
          ON t.id = s.target_id AND t.ip_type != 'Genomic'
        LEFT JOIN preclin.indication i ON i.indication_id = s.indication_id
        WHERE t.id IS NULL OR i.indication_id IS NULL
           OR (s.gene IS NOT NULL AND upper(s.gene) IS DISTINCT FROM upper(t.symbol))
        LIMIT 10
        """,
    )
    _require_no_preflight_rows(
        cur,
        "found dossier source-document mismatches",
        """
        SELECT s.line_number, s.pair_key, s.dossier_document_id,
               d.source_name, d.external_id, d.content_sha256
        FROM nelson_preflight_row s
        LEFT JOIN preclin.source_document d
          ON d.source_document_id = s.dossier_document_id
        WHERE d.source_document_id IS NULL
           OR d.source_name IS DISTINCT FROM 'nelson_dossier'
           OR d.external_id IS DISTINCT FROM s.pair_key
           OR d.content_sha256 IS DISTINCT FROM s.dossier_sha256
        LIMIT 10
        """,
    )
    _require_no_preflight_rows(
        cur,
        "found missing source documents",
        """
        SELECT s.line_number, s.pair_key, s.source_document_id
        FROM nelson_preflight_source s
        LEFT JOIN preclin.source_document d
          ON d.source_document_id = s.source_document_id
        WHERE d.source_document_id IS NULL
        LIMIT 10
        """,
    )
    _require_no_preflight_rows(
        cur,
        "found existing run IDs with different content",
        """
        SELECT s.line_number, s.pair_key, s.run_id
        FROM nelson_preflight_row s
        JOIN preclin.llm_run r ON r.run_id = s.run_id
        WHERE (r.subject_type, r.subject_key, r.classifier_task,
               r.classifier_model, r.classifier_version,
               r.input_sha256, r.output_sha256)
          IS DISTINCT FROM
              ('target_indication', s.pair_key, 'nelson_tier',
               s.model, s.prompt_version, s.input_sha256, s.output_sha256)
        LIMIT 10
        """,
    )
    _require_no_preflight_rows(
        cur,
        "found existing source links with different excerpts",
        """
        SELECT s.line_number, s.pair_key, s.run_id, s.source_document_id,
               s.relationship, s.ordinal
        FROM nelson_preflight_source s
        JOIN preclin.llm_run_source r
          ON r.run_id = s.run_id
         AND r.source_document_id = s.source_document_id
         AND r.relationship = s.relationship
         AND r.ordinal = s.ordinal
        WHERE r.excerpt_sha256 IS DISTINCT FROM s.excerpt_sha256
        LIMIT 10
        """,
    )

    print(
        f"Nelson bulk preflight passed: rows={len(staged_rows)} "
        f"source_links={len(staged_sources)} unique_pairs={len(pair_keys)}"
    )
    for path, count in per_file_counts.items():
        print(f"{path}: rows={count}")
    conn.rollback()
    print("validated with temporary staging; rolled back")


def _nelson_details(row: dict, gene: str, indication: str) -> dict:
    return {
        "schema_version": row["schema_version"],
        "pair_key": row["pair_key"],
        "gene": gene,
        "indication": indication,
        "genetic_effect_direction": row.get("genetic_effect_direction"),
        "disease_match": row.get("disease_match"),
        "supporting_evidence": row.get("supporting_evidence") or [],
        "evidence_variants": row.get("evidence_variants") or [],
        "evidence_url": row.get("evidence_url") or "",
        "dossier_sha256": row.get("dossier_sha256"),
        "dossier_source_document_id": int(row["dossier_source_document_id"]),
        "dossier_file": row.get("_dossier_file"),
        "evidence_counts": row.get("evidence_counts") or {},
        "prompt_selection": row.get("prompt_selection") or {},
        "deterministic_validation": row.get("deterministic_validation") or {},
    }


class _IteratorReader:
    """Expose an iterator of strings as the read() interface COPY expects."""

    def __init__(self, chunks):
        self._chunks = iter(chunks)
        self._buffer = ""

    def read(self, size=-1):
        if size < 0:
            return self._buffer + "".join(self._chunks)
        while len(self._buffer) < size:
            try:
                self._buffer += next(self._chunks)
            except StopIteration:
                break
        result, self._buffer = self._buffer[:size], self._buffer[size:]
        return result


def _copy_iterator(cur, table: str, columns: tuple[str, ...], rows) -> None:
    def csv_chunks():
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter="\t", lineterminator="\n")
        for row in rows:
            buffer.seek(0)
            buffer.truncate(0)
            writer.writerow([r"\N" if value is None else value for value in row])
            yield buffer.getvalue()

    cur.copy_expert(
        f"COPY {table} ({', '.join(columns)}) FROM STDIN "
        "WITH (FORMAT CSV, DELIMITER E'\\t', NULL '\\N')",
        _IteratorReader(csv_chunks()),
        size=4 * 1024 * 1024,
    )


def _compact_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def nelson_bulk_ingest(conn, inputs: list[Path]) -> dict[Path, dict]:
    """Atomically stage Nelson audit data with COPY, then merge set-wise."""
    cur = conn.cursor()
    require_schema(cur)
    cur.execute(
        "SELECT id, symbol FROM public.targets WHERE ip_type != 'Genomic'"
    )
    genes = dict(cur.fetchall())
    cur.execute("SELECT indication_id, display_name FROM preclin.indication")
    indications = dict(cur.fetchall())
    cur.execute(
        """
        CREATE TEMP TABLE nelson_run_stage (
          run_id uuid, provider text, provider_request_id text,
          subject_type text, subject_key text, classifier_task text,
          classifier_model text, classifier_version text,
          system_prompt text COMPRESSION lz4,
          user_prompt_base64 text, user_prompt_uncompressed_bytes integer,
          input_sha256 text,
          raw_response text COMPRESSION lz4, output_sha256 text,
          parsed_output jsonb COMPRESSION lz4, model_parameters jsonb,
          input_tokens integer, output_tokens integer, cost_usd double precision
        ) ON COMMIT DROP;
        CREATE TEMP TABLE nelson_source_stage (
          run_id uuid, source_document_id bigint, relationship text,
          ordinal integer, excerpt_text text COMPRESSION lz4, excerpt_sha256 text
        ) ON COMMIT DROP;
        CREATE TEMP TABLE nelson_fact_stage (
          run_id uuid, subject_id integer, subject_id2 integer,
          source_version text, value_text text,
          value_json jsonb COMPRESSION lz4, citation_pmids jsonb,
          citation_details jsonb, extracted_by text, notes text,
          fact_snapshot jsonb COMPRESSION lz4
        ) ON COMMIT DROP
        """
    )

    counts = {path: {"runs": 0, "classifications": 0, "facts": 0} for path in inputs}

    def run_rows():
        for path, line_number, row in read_jsonl(inputs):
            run_id = str(validate_audit_row(path, line_number, row))
            target_id = int(row["target_id"])
            indication_id = int(row["indication_id"])
            pair_key = f"{target_id}:{indication_id}"
            if row.get("pair_key") != pair_key:
                raise ValueError(f"{path}:{line_number}: pair_key must be {pair_key}")
            system_prompt = row["_system_prompt"]
            user_prompt = row["_user_prompt"]
            raw_response = row["_raw_response"]
            compressed_prompt, prompt_bytes = compress_user_prompt(user_prompt)
            counts[path]["runs"] += 1
            counts[path]["facts"] += 1
            yield (
                run_id, row.get("_provider", "anthropic"),
                row.get("_provider_request_id"), "target_indication", pair_key,
                "nelson_tier", row["_model"], row["_prompt_version"],
                system_prompt, compressed_prompt, prompt_bytes,
                prompt_hash(system_prompt, user_prompt),
                raw_response, sha256_text(raw_response),
                _compact_json(parsed_output_payload(row)),
                _compact_json(row.get("_model_parameters") or {}),
                row.get("_input_tokens"), row.get("_output_tokens"), read_cost(row),
            )

    _copy_iterator(
        cur, "nelson_run_stage",
        (
            "run_id", "provider", "provider_request_id", "subject_type",
            "subject_key", "classifier_task", "classifier_model",
            "classifier_version", "system_prompt", "user_prompt_base64",
            "user_prompt_uncompressed_bytes", "input_sha256",
            "raw_response", "output_sha256", "parsed_output", "model_parameters",
            "input_tokens", "output_tokens", "cost_usd",
        ),
        run_rows(),
    )
    print(f"staged {sum(c['runs'] for c in counts.values())} Nelson runs", flush=True)

    def source_rows():
        for path, line_number, row in read_jsonl(inputs):
            run_id = str(validate_audit_row(path, line_number, row))
            require_source_inputs(row, "nelson-tier")
            seen = set()
            for fallback_ordinal, source in enumerate(row["_source_documents"]):
                document_id = int(source["source_document_id"])
                relationship = source.get("relationship") or "model_input"
                ordinal = source.get("ordinal", fallback_ordinal)
                excerpt = source.get("excerpt_text")
                key = (document_id, relationship, ordinal)
                if key in seen:
                    continue
                seen.add(key)
                yield (
                    run_id, document_id, relationship, ordinal, excerpt,
                    sha256_text(excerpt) if excerpt is not None else None,
                )

    _copy_iterator(
        cur, "nelson_source_stage",
        (
            "run_id", "source_document_id", "relationship", "ordinal",
            "excerpt_text", "excerpt_sha256",
        ),
        source_rows(),
    )
    print("staged Nelson source links", flush=True)

    def fact_rows():
        for path, line_number, row in read_jsonl(inputs):
            run_id = str(validate_audit_row(path, line_number, row))
            tier = str(row.get("tier") or "").upper()
            if tier not in {"T0", "T1", "T2", "T3"}:
                raise ValueError(f"{path}:{line_number}: invalid Nelson tier {tier!r}")
            target_id = int(row["target_id"])
            indication_id = int(row["indication_id"])
            gene = genes.get(target_id)
            indication = indications.get(indication_id)
            if gene is None or indication is None:
                raise ValueError(f"{path}:{line_number}: unknown target-indication pair")
            version = str(row["_prompt_version"])
            model = str(row["_model"])
            details = _nelson_details(row, gene, indication)
            citations = [str(value) for value in (row.get("supporting_pmids") or [])]
            snapshot = evidence_snapshot(
                subject_type="target_indication", subject_id=target_id,
                subject_id2=indication_id, dimension="nelson_tier",
                category="A_genetics", source="nelson_llm", version=version,
                model=model, value_text=tier, value_json=details,
                citation_pmids=citations,
            )
            yield (
                run_id, target_id, indication_id, version, tier,
                _compact_json(details), _compact_json(citations),
                _compact_json({"dossier_sha256": row.get("dossier_sha256")}),
                model, str(row.get("rationale") or "")[:2000],
                _compact_json(snapshot),
            )

    _copy_iterator(
        cur, "nelson_fact_stage",
        (
            "run_id", "subject_id", "subject_id2", "source_version",
            "value_text", "value_json", "citation_pmids", "citation_details",
            "extracted_by", "notes", "fact_snapshot",
        ),
        fact_rows(),
    )
    print("staged Nelson evidence facts", flush=True)

    cur.execute(
        """
        INSERT INTO preclin.llm_run
          (run_id, provider, provider_request_id, subject_type, subject_key,
           classifier_task, classifier_model, classifier_version,
           system_prompt, user_prompt, user_prompt_compressed,
           user_prompt_compression, user_prompt_uncompressed_bytes,
           input_sha256, raw_response,
           output_sha256, parsed_output, model_parameters, input_tokens,
           output_tokens, cost_usd)
        SELECT run_id, provider, provider_request_id, subject_type, subject_key,
               classifier_task, classifier_model, classifier_version,
               system_prompt, NULL,
               decode(user_prompt_base64, 'base64'), 'zlib',
               user_prompt_uncompressed_bytes, input_sha256, raw_response,
               output_sha256, parsed_output, model_parameters, input_tokens,
               output_tokens, cost_usd
        FROM nelson_run_stage
        ON CONFLICT (run_id) DO NOTHING;

        INSERT INTO preclin.llm_run_source
          (run_id, source_document_id, relationship, ordinal,
           excerpt_text, excerpt_sha256)
        SELECT run_id, source_document_id, relationship, ordinal,
               excerpt_text, excerpt_sha256
        FROM nelson_source_stage
        ON CONFLICT DO NOTHING;

        INSERT INTO preclin.evidence_score
          (subject_type, subject_id, subject_id2, dimension, category,
           value_text, value_json, source, source_version, citation_pmids,
           citation_details, extracted_by, notes)
        SELECT 'target_indication', subject_id, subject_id2,
               'nelson_tier', 'A_genetics', value_text, value_json,
               'nelson_llm', source_version,
               ARRAY(SELECT jsonb_array_elements_text(citation_pmids)),
               citation_details, extracted_by, notes
        FROM nelson_fact_stage
        ON CONFLICT
          (subject_type, subject_id, subject_id2, dimension, source, source_version)
        DO UPDATE SET
          category = EXCLUDED.category, value_numeric = NULL,
          value_text = EXCLUDED.value_text, value_boolean = NULL,
          value_json = EXCLUDED.value_json, confidence = NULL,
          citation_pmids = EXCLUDED.citation_pmids,
          citation_details = EXCLUDED.citation_details,
          extracted_by = EXCLUDED.extracted_by, notes = EXCLUDED.notes,
          extracted_at = now();

        INSERT INTO preclin.llm_run_evidence_score
          (run_id, evidence_id, role, fact_snapshot)
        SELECT s.run_id, e.evidence_id, 'produced', s.fact_snapshot
        FROM nelson_fact_stage s
        JOIN preclin.evidence_score e
          ON e.subject_type = 'target_indication'
         AND e.subject_id = s.subject_id
         AND e.subject_id2 = s.subject_id2
         AND e.dimension = 'nelson_tier'
         AND e.source = 'nelson_llm'
         AND e.source_version = s.source_version
        ON CONFLICT DO NOTHING
        """
    )
    _require_no_preflight_rows(
        cur,
        "found immutable fact-snapshot mismatches after bulk merge",
        """
        SELECT s.run_id, s.subject_id, s.subject_id2
        FROM nelson_fact_stage s
        JOIN preclin.evidence_score e
          ON e.subject_type = 'target_indication'
         AND e.subject_id = s.subject_id AND e.subject_id2 = s.subject_id2
         AND e.dimension = 'nelson_tier' AND e.source = 'nelson_llm'
         AND e.source_version = s.source_version
        JOIN preclin.llm_run_evidence_score l
          ON l.run_id = s.run_id AND l.evidence_id = e.evidence_id
         AND l.role = 'produced'
        WHERE l.fact_snapshot IS DISTINCT FROM s.fact_snapshot
        LIMIT 10
        """,
    )
    return counts


def insert_run(cur, row: dict, subject_type: str, subject_key: str, task: str) -> str:
    run_id = str(uuid.UUID(str(row["_run_id"])))
    system_prompt = row["_system_prompt"]
    user_prompt = row["_user_prompt"]
    raw_response = row["_raw_response"]
    input_hash = prompt_hash(system_prompt, user_prompt)
    output_hash = sha256_text(raw_response)
    model = row["_model"]
    version = row["_prompt_version"]

    cur.execute(
        """
        INSERT INTO preclin.llm_run
          (run_id, provider, provider_request_id, subject_type, subject_key,
           classifier_task, classifier_model, classifier_version,
           system_prompt, user_prompt, input_sha256, raw_response,
           output_sha256, parsed_output, model_parameters, input_tokens,
           output_tokens, cost_usd)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s)
        ON CONFLICT (run_id) DO NOTHING
        """,
        (
            run_id,
            row.get("_provider", "anthropic"),
            row.get("_provider_request_id"),
            subject_type,
            subject_key,
            task,
            model,
            version,
            system_prompt,
            user_prompt,
            input_hash,
            raw_response,
            output_hash,
            Json(parsed_output_payload(row)),
            Json(row.get("_model_parameters") or {}),
            row.get("_input_tokens"),
            row.get("_output_tokens"),
            read_cost(row),
        ),
    )

    cur.execute(
        """
        SELECT subject_type, subject_key, classifier_task, classifier_model,
               classifier_version, input_sha256, output_sha256
        FROM preclin.llm_run
        WHERE run_id = %s
        """,
        (run_id,),
    )
    stored = cur.fetchone()
    expected = (subject_type, subject_key, task, model, version, input_hash, output_hash)
    if stored != expected:
        raise ValueError(f"run_id {run_id} already exists with different content")

    for fallback_ordinal, source in enumerate(row.get("_source_documents") or []):
        if not isinstance(source, dict):
            raise ValueError(f"run_id {run_id}: source entry must be an object")
        document_id = source.get("source_document_id")
        if not isinstance(document_id, int):
            raise ValueError(f"run_id {run_id}: source_document_id must be an integer")
        relationship = source.get("relationship") or "model_input"
        ordinal = source.get("ordinal", fallback_ordinal)
        if not isinstance(ordinal, int) or ordinal < 0:
            raise ValueError(f"run_id {run_id}: source ordinal must be a non-negative integer")
        excerpt = source.get("excerpt_text")
        if excerpt is not None and not isinstance(excerpt, str):
            raise ValueError(f"run_id {run_id}: excerpt_text must be a string or null")
        excerpt_hash = sha256_text(excerpt) if excerpt is not None else None

        cur.execute(
            """
            SELECT excerpt_sha256
            FROM preclin.llm_run_source
            WHERE run_id = %s AND source_document_id = %s
              AND relationship = %s AND ordinal = %s
            """,
            (run_id, document_id, relationship, ordinal),
        )
        existing = cur.fetchone()
        if existing and existing[0] != excerpt_hash:
            raise ValueError(
                f"run_id {run_id}: source link already exists with a different excerpt"
            )
        cur.execute(
            """
            INSERT INTO preclin.llm_run_source
              (run_id, source_document_id, relationship, ordinal,
               excerpt_text, excerpt_sha256)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (run_id, document_id, relationship, ordinal, excerpt, excerpt_hash),
        )
    return run_id


def upsert_classification(
    cur,
    row: dict,
    run_id: str,
    subject_type: str,
    subject_key: str,
    task: str,
    category: str,
    rationale: str | None,
) -> int:
    citations = [str(value) for value in (row.get("citation_pmids") or [])]
    cur.execute(
        """
        INSERT INTO preclin.classification
          (subject_type, subject_key, classifier_task, category, confidence,
           rationale, citation_pmids, classifier_model, classifier_version,
           cost_usd, raw_output, latest_run_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT
          (subject_type, subject_key, classifier_task,
           classifier_model, classifier_version)
        DO UPDATE SET
          category = EXCLUDED.category,
          confidence = EXCLUDED.confidence,
          rationale = EXCLUDED.rationale,
          citation_pmids = EXCLUDED.citation_pmids,
          cost_usd = EXCLUDED.cost_usd,
          raw_output = EXCLUDED.raw_output,
          latest_run_id = EXCLUDED.latest_run_id,
          extracted_at = now()
        RETURNING classification_id
        """,
        (
            subject_type,
            subject_key,
            task,
            category,
            row.get("confidence"),
            rationale,
            citations,
            model_family(row["_model"]),
            row["_prompt_version"],
            read_cost(row),
            Json(row),
            run_id,
        ),
    )
    return cur.fetchone()[0]


def resolve_target(cur, gene: str) -> int:
    cur.execute(
        """
        SELECT id
        FROM public.targets
        WHERE upper(symbol) = upper(%s)
          AND ip_type != 'Genomic'
        ORDER BY id
        LIMIT 1
        """,
        (gene,),
    )
    result = cur.fetchone()
    if not result:
        raise ValueError(f"target not found: {gene}")
    return result[0]


def resolve_drug(cur, value: str) -> tuple[int, str]:
    normalized = normalize_drug(value)
    cur.execute(
        "SELECT drug_id, normalized_name FROM preclin.drug WHERE normalized_name = %s",
        (normalized,),
    )
    result = cur.fetchone()
    if not result:
        raise ValueError(f"drug not found: {value}")
    return result


def upsert_evidence(
    cur,
    *,
    subject_type: str,
    subject_id: int,
    subject_id2: int | None = None,
    dimension: str,
    category: str,
    source: str,
    version: str,
    model: str,
    run_id: str,
    value_numeric=None,
    value_text=None,
    value_json=None,
    confidence=None,
    citation_pmids=None,
    citation_details=None,
    notes=None,
) -> int:
    snapshot = evidence_snapshot(
        subject_type=subject_type,
        subject_id=subject_id,
        subject_id2=subject_id2,
        dimension=dimension,
        category=category,
        source=source,
        version=version,
        model=model,
        value_numeric=value_numeric,
        value_text=value_text,
        value_json=value_json,
        confidence=confidence,
        citation_pmids=citation_pmids,
    )
    cur.execute(
        """
        SELECT evidence_id
        FROM preclin.evidence_score
        WHERE subject_type = %s AND subject_id = %s
          AND subject_id2 IS NOT DISTINCT FROM %s
          AND dimension = %s AND source = %s
          AND source_version IS NOT DISTINCT FROM %s
        ORDER BY evidence_id
        LIMIT 1
        """,
        (subject_type, subject_id, subject_id2, dimension, source, version),
    )
    existing = cur.fetchone()
    citations = [str(value) for value in (citation_pmids or [])]
    if existing:
        evidence_id = existing[0]
        cur.execute(
            """
            UPDATE preclin.evidence_score
            SET category = %s, value_numeric = %s, value_text = %s,
                value_boolean = NULL, value_json = %s, confidence = %s,
                citation_pmids = %s, citation_details = %s,
                extracted_by = %s, notes = %s, extracted_at = now()
            WHERE evidence_id = %s
            """,
            (
                category,
                value_numeric,
                value_text,
                Json(value_json) if value_json is not None else None,
                confidence,
                citations,
                Json(citation_details) if citation_details is not None else None,
                model,
                notes,
                evidence_id,
            ),
        )
    else:
        cur.execute(
            """
            INSERT INTO preclin.evidence_score
              (subject_type, subject_id, subject_id2, dimension, category,
               value_numeric, value_text, value_json, source, source_version,
               confidence, citation_pmids, citation_details, extracted_by, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s)
            RETURNING evidence_id
            """,
            (
                subject_type,
                subject_id,
                subject_id2,
                dimension,
                category,
                value_numeric,
                value_text,
                Json(value_json) if value_json is not None else None,
                source,
                version,
                confidence,
                citations,
                Json(citation_details) if citation_details is not None else None,
                model,
                notes,
            ),
        )
        evidence_id = cur.fetchone()[0]
    cur.execute(
        """
        INSERT INTO preclin.llm_run_evidence_score
          (run_id, evidence_id, role, fact_snapshot)
        VALUES (%s, %s, 'produced', %s)
        ON CONFLICT DO NOTHING
        """,
        (run_id, evidence_id, Json(snapshot)),
    )
    cur.execute(
        """
        SELECT fact_snapshot
        FROM preclin.llm_run_evidence_score
        WHERE run_id = %s AND evidence_id = %s AND role = 'produced'
        """,
        (run_id, evidence_id),
    )
    stored_snapshot = cur.fetchone()
    if not stored_snapshot or stored_snapshot[0] != snapshot:
        raise ValueError(
            f"run_id {run_id} already links evidence_id {evidence_id} "
            "with a different immutable fact value"
        )
    return evidence_id


def ingest_row(cur, task: str, row: dict) -> tuple[int, int]:
    """Return (classification rows, evidence facts) affected by one model run."""
    if task == "why-stopped":
        nct_id = str(row.get("nct_id") or "").strip()
        if not nct_id:
            raise ValueError("why-stopped row has no nct_id")
        require_source_inputs(row, task)
        run_id = insert_run(cur, row, "trial", nct_id, "why_stopped")
        upsert_classification(
            cur,
            row,
            run_id,
            "trial",
            nct_id,
            "why_stopped",
            row.get("cat") or "unclear",
            row.get("rationale"),
        )
        return 1, 0

    if task == "silent-kill":
        drug_key = str(row.get("drug_key") or "").strip()
        if not drug_key:
            raise ValueError("silent-kill row has no drug_key")
        run_id = insert_run(cur, row, "drug", drug_key, "silent_kill_verify")
        upsert_classification(
            cur,
            row,
            run_id,
            "drug",
            drug_key,
            "silent_kill_verify",
            row.get("cat") or "unclear",
            row.get("evidence"),
        )
        return 1, 0

    if task == "target-literature":
        if row.get("_no_abstracts"):
            raise ValueError("no-abstract sentinel is not an LLM run and cannot be ingested")
        gene = str(row.get("gene") or "").strip()
        if not gene:
            raise ValueError("target-literature row has no gene")
        try:
            input_count = int(row["_n_abstracts_provided"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("target-literature row has no valid _n_abstracts_provided") from exc
        require_source_inputs(row, task, expected_count=input_count)
        target_id = resolve_target(cur, gene)
        run_id = insert_run(cur, row, "target", gene, "target_literature_score")
        facts = 0
        for key, dimension, category in TARGET_DIMENSIONS:
            if row.get(key) is None:
                continue
            upsert_evidence(
                cur,
                subject_type="target",
                subject_id=target_id,
                dimension=dimension,
                category=category,
                source="pubmed_haiku",
                version=row["_prompt_version"],
                model=row["_model"],
                run_id=run_id,
                value_numeric=float(row[key]),
                citation_pmids=(row.get("notable_pmids") or [])[:10],
            )
            facts += 1
        return 0, facts

    if task == "nelson-tier":
        if row.get("schema_version") != "nelson_tier_result_v5":
            raise ValueError("nelson-tier row must use nelson_tier_result_v5")
        tier = str(row.get("tier") or "").upper()
        if tier not in {"T0", "T1", "T2", "T3"}:
            raise ValueError(f"invalid Nelson tier: {tier!r}")
        try:
            target_id = int(row["target_id"])
            indication_id = int(row["indication_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("nelson-tier row needs target_id and indication_id") from exc
        cur.execute(
            """
            SELECT t.symbol, i.display_name
            FROM public.targets t
            CROSS JOIN preclin.indication i
            WHERE t.id = %s AND t.ip_type != 'Genomic'
              AND i.indication_id = %s
            """,
            (target_id, indication_id),
        )
        resolved = cur.fetchone()
        if not resolved:
            raise ValueError(
                f"unknown target-indication IDs: {target_id}:{indication_id}"
            )
        gene, indication = resolved
        if row.get("gene") and str(row["gene"]).upper() != str(gene).upper():
            raise ValueError(f"target_id {target_id} does not resolve to {row['gene']}")
        pair_key = f"{target_id}:{indication_id}"
        if row.get("pair_key") != pair_key:
            raise ValueError(f"nelson-tier pair_key must be {pair_key}")
        sources = row.get("_source_documents")
        if not isinstance(sources, list):
            raise ValueError("nelson-tier row must include _source_documents")
        if sources:
            require_source_inputs(row, task)
        try:
            dossier_document_id = int(row["dossier_source_document_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "nelson-tier row needs dossier_source_document_id"
            ) from exc
        dossier_links = [
            source
            for source in sources
            if source.get("relationship") == "dossier_snapshot"
            and source.get("source_document_id") == dossier_document_id
        ]
        if len(dossier_links) != 1:
            raise ValueError(
                "nelson-tier row must link its one canonical dossier snapshot"
            )
        cur.execute(
            """
            SELECT source_name, external_id, content_sha256
            FROM preclin.source_document
            WHERE source_document_id = %s
            """,
            (dossier_document_id,),
        )
        dossier_source = cur.fetchone()
        expected_dossier_source = (
            "nelson_dossier",
            pair_key,
            row.get("dossier_sha256"),
        )
        if dossier_source != expected_dossier_source:
            raise ValueError(
                "dossier source document does not match pair_key/content hash"
            )
        run_id = insert_run(cur, row, "target_indication", pair_key, "nelson_tier")
        details = {
            "schema_version": row["schema_version"],
            "pair_key": pair_key,
            "gene": gene,
            "indication": indication,
            "genetic_effect_direction": row.get("genetic_effect_direction"),
            "disease_match": row.get("disease_match"),
            "supporting_evidence": row.get("supporting_evidence") or [],
            "evidence_variants": row.get("evidence_variants") or [],
            "evidence_url": row.get("evidence_url") or "",
            "dossier_sha256": row.get("dossier_sha256"),
            "dossier_source_document_id": dossier_document_id,
            "dossier_file": row.get("_dossier_file"),
            "evidence_counts": row.get("evidence_counts") or {},
            "prompt_selection": row.get("prompt_selection") or {},
            "deterministic_validation": row.get("deterministic_validation") or {},
        }
        upsert_evidence(
            cur,
            subject_type="target_indication",
            subject_id=target_id,
            subject_id2=indication_id,
            dimension="nelson_tier",
            category="A_genetics",
            source="nelson_llm",
            version=row["_prompt_version"],
            model=row["_model"],
            run_id=run_id,
            value_text=tier,
            value_json=details,
            citation_pmids=row.get("supporting_pmids") or [],
            citation_details={"dossier_sha256": row.get("dossier_sha256")},
            notes=str(row.get("rationale") or "")[:2000],
        )
        return 0, 1

    drug_name = str(row.get("drug") or "").strip()
    if not drug_name:
        raise ValueError("drug-evidence row has no drug")
    require_source_inputs(row, task)
    drug_id, drug_key = resolve_drug(cur, drug_name)
    run_id = insert_run(cur, row, "drug", drug_key, "drug_evidence_extract")
    facts = 0
    for key, dimension, category in DRUG_NUMERIC_DIMENSIONS:
        if row.get(key) is None:
            continue
        upsert_evidence(
            cur,
            subject_type="drug",
            subject_id=drug_id,
            dimension=dimension,
            category=category,
            source="pubmed_sonnet",
            version=row["_prompt_version"],
            model=row["_model"],
            run_id=run_id,
            value_numeric=float(row[key]),
            confidence=row.get("confidence"),
        )
        facts += 1
    for key, dimension, category in DRUG_TEXT_DIMENSIONS:
        if row.get(key) is None:
            continue
        upsert_evidence(
            cur,
            subject_type="drug",
            subject_id=drug_id,
            dimension=dimension,
            category=category,
            source="pubmed_sonnet",
            version=row["_prompt_version"],
            model=row["_model"],
            run_id=run_id,
            value_text=str(row[key])[:100],
            confidence=row.get("confidence"),
        )
        facts += 1
    return 0, facts


def log_ingest(cur, path: Path, task: str, rows_read: int, affected: int) -> None:
    target_table = (
        "preclin.classification"
        if task in ("why-stopped", "silent-kill")
        else "preclin.evidence_score"
    )
    cur.execute(
        """
        INSERT INTO preclin.ingest_log
          (source_file, target_table, rows_read, rows_inserted,
           rows_skipped, rows_updated, finished_at, status, notes)
        VALUES (%s, %s, %s, %s, 0, 0, now(), 'ok', %s)
        """,
        (path.name, target_table, rows_read, affected, f"incremental task={task}"),
    )


def main() -> None:
    args = parse_args()
    if args.preflight and args.dry_run:
        raise SystemExit("choose either --preflight or --dry-run, not both")
    if args.preflight and args.task != "nelson-tier":
        raise SystemExit("--preflight currently supports only --task nelson-tier")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set")

    if args.direct_db:
        database_url = direct_database_url(database_url)
    conn = psycopg2.connect(database_url)
    if args.preflight:
        try:
            nelson_preflight(conn, args.inputs)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return

    try:
        if args.task == "nelson-tier":
            # Validate compact identities/references first, then use set-based
            # writes for the multi-gigabyte audited cohort.
            nelson_preflight(conn, args.inputs)
            counts = nelson_bulk_ingest(conn, args.inputs)
            cur = conn.cursor()
        else:
            counts = {
                path: {"runs": 0, "classifications": 0, "facts": 0}
                for path in args.inputs
            }
            cur = conn.cursor()
            require_schema(cur)
            for path, line_number, row in read_jsonl(args.inputs):
                validate_audit_row(path, line_number, row)
                try:
                    classifications, facts = ingest_row(cur, args.task, row)
                except Exception as exc:
                    raise ValueError(f"{path}:{line_number}: {exc}") from exc
                counts[path]["runs"] += 1
                counts[path]["classifications"] += classifications
                counts[path]["facts"] += facts

        for path, count in counts.items():
            affected = count["classifications"] + count["facts"]
            log_ingest(cur, path, args.task, count["runs"], affected)

        if args.dry_run:
            conn.rollback()
            disposition = "validated; rolled back"
        else:
            conn.commit()
            disposition = "committed"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    for path, count in counts.items():
        print(
            f"{path}: runs={count['runs']} classifications={count['classifications']} "
            f"evidence_facts={count['facts']}"
        )
    print(disposition)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, psycopg2.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
