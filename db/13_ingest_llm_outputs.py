"""Incrementally ingest audited classifier JSONL outputs.

This is the narrow JSONL -> Postgres bridge for new paid LLM runs. It does
not retrieve sources or call a model, and it deliberately does not rerun the
big-bang ``02_ingest.py`` loader.

Examples:
    .venv/bin/dotenv run -- .venv/bin/python db/13_ingest_llm_outputs.py \
      --task why-stopped data/clinical_trials/why_stopped_2026.jsonl

    .venv/bin/dotenv run -- .venv/bin/python db/13_ingest_llm_outputs.py \
      --task target-literature data/target_evidence/literature_scores_2026.jsonl

Migration ``10_clinical_trial_source_audit.sql`` must be applied first. New
rows are required to contain the exact audit metadata written by
``analyses/classifiers/common.py``. The whole invocation is transactional;
``--dry-run`` validates and then rolls back.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Iterable

import psycopg2
from psycopg2.extras import Json


TASKS = ("why-stopped", "target-literature", "silent-kill", "drug-evidence")
REQUIRED_AUDIT_FIELDS = (
    "_run_id",
    "_model",
    "_prompt_version",
    "_system_prompt",
    "_user_prompt",
    "_raw_response",
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
    dimension: str,
    category: str,
    source: str,
    version: str,
    model: str,
    value_numeric=None,
    value_text=None,
    confidence=None,
    citation_pmids=None,
) -> dict:
    """Return the immutable fact value produced by one extraction run."""
    return {
        "subject_type": subject_type,
        "subject_id": subject_id,
        "subject_id2": None,
        "dimension": dimension,
        "category": category,
        "value_numeric": value_numeric,
        "value_text": value_text,
        "value_boolean": None,
        "value_json": None,
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
        or not isinstance(source.get("excerpt_text"), str)
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
            Json(row),
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
    dimension: str,
    category: str,
    source: str,
    version: str,
    model: str,
    run_id: str,
    value_numeric=None,
    value_text=None,
    confidence=None,
    citation_pmids=None,
) -> int:
    snapshot = evidence_snapshot(
        subject_type=subject_type,
        subject_id=subject_id,
        dimension=dimension,
        category=category,
        source=source,
        version=version,
        model=model,
        value_numeric=value_numeric,
        value_text=value_text,
        confidence=confidence,
        citation_pmids=citation_pmids,
    )
    cur.execute(
        """
        SELECT evidence_id
        FROM preclin.evidence_score
        WHERE subject_type = %s AND subject_id = %s AND subject_id2 IS NULL
          AND dimension = %s AND source = %s
          AND source_version IS NOT DISTINCT FROM %s
        ORDER BY evidence_id
        LIMIT 1
        """,
        (subject_type, subject_id, dimension, source, version),
    )
    existing = cur.fetchone()
    citations = [str(value) for value in (citation_pmids or [])]
    if existing:
        evidence_id = existing[0]
        cur.execute(
            """
            UPDATE preclin.evidence_score
            SET category = %s, value_numeric = %s, value_text = %s,
                value_boolean = NULL, confidence = %s, citation_pmids = %s,
                extracted_by = %s, extracted_at = now()
            WHERE evidence_id = %s
            """,
            (
                category,
                value_numeric,
                value_text,
                confidence,
                citations,
                model,
                evidence_id,
            ),
        )
    else:
        cur.execute(
            """
            INSERT INTO preclin.evidence_score
              (subject_type, subject_id, subject_id2, dimension, category,
               value_numeric, value_text, source, source_version, confidence,
               citation_pmids, extracted_by)
            VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING evidence_id
            """,
            (
                subject_type,
                subject_id,
                dimension,
                category,
                value_numeric,
                value_text,
                source,
                version,
                confidence,
                citations,
                model,
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
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set")

    conn = psycopg2.connect(database_url)
    counts = {path: {"runs": 0, "classifications": 0, "facts": 0} for path in args.inputs}
    try:
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
