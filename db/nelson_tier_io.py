"""Parsing, validation, and idempotent ingestion for Nelson-tier results."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Iterator


RESULT_SCHEMA_VERSION = "nelson_tier_result_v2"
VALID_TIERS = frozenset({"T0", "T1", "T2", "T3", "T4"})


def result_files(directory: Path) -> list[Path]:
    """Return result files, deliberately excluding full dossier sidecars."""
    return sorted(
        path
        for path in directory.glob("nelson_tiers_*.jsonl")
        if ".dossiers." not in path.name
    )


def normalize_pmids(values) -> list[str]:
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def iter_tier_results(paths: Iterable[Path]) -> Iterator[dict]:
    """Yield valid v2 rows and reject malformed scored rows loudly."""
    for path in paths:
        with path.open() as fh:
            for line_number, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
                if row.get("schema_version") != RESULT_SCHEMA_VERSION:
                    raise ValueError(
                        f"{path}:{line_number}: expected schema_version "
                        f"{RESULT_SCHEMA_VERSION!r}"
                    )
                tier = str(row.get("tier") or "").upper()
                if tier not in VALID_TIERS:
                    raise ValueError(f"{path}:{line_number}: invalid tier {tier!r}")
                try:
                    target_id = int(row["target_id"])
                    indication_id = int(row["indication_id"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(
                        f"{path}:{line_number}: target_id and indication_id are required"
                    ) from exc
                normalized = dict(row)
                normalized["target_id"] = target_id
                normalized["indication_id"] = indication_id
                normalized["tier"] = tier
                normalized["supporting_pmids"] = normalize_pmids(
                    row.get("supporting_pmids")
                )
                normalized["_source_file"] = path.name
                yield normalized


def prepare_database_rows(cur, directory: Path) -> tuple[list[Path], list[tuple]]:
    """Validate IDs and convert every discovered v2 result to a DB row."""
    from psycopg2.extras import Json

    paths = result_files(directory) if directory.exists() else []
    cur.execute("SELECT id FROM public.targets WHERE ip_type != 'Genomic'")
    valid_target_ids = {row[0] for row in cur.fetchall()}
    cur.execute("SELECT indication_id FROM preclin.indication")
    valid_indication_ids = {row[0] for row in cur.fetchall()}

    rows = []
    for result in iter_tier_results(paths):
        if result["target_id"] not in valid_target_ids:
            raise ValueError(
                f"{result['_source_file']}: unknown target_id {result['target_id']}"
            )
        if result["indication_id"] not in valid_indication_ids:
            raise ValueError(
                f"{result['_source_file']}: unknown indication_id "
                f"{result['indication_id']}"
            )
        model = str(result.get("_model") or "unknown")
        prompt_version = str(result.get("_prompt_version") or "unknown")
        source_version = f"{prompt_version}:{model}"
        details = {
            "schema_version": result.get("schema_version"),
            "pair_key": result.get("pair_key"),
            "gene": result.get("gene"),
            "indication": result.get("indication"),
            "direction_concordance": result.get("direction_concordance"),
            "disease_match": result.get("disease_match"),
            "evidence_variants": result.get("evidence_variants") or [],
            "evidence_url": result.get("evidence_url") or "",
            "dossier_sha256": result.get("dossier_sha256"),
            "dossier_file": result.get("_dossier_file"),
            "evidence_counts": result.get("evidence_counts") or {},
            "result_file": result.get("_source_file"),
        }
        rows.append((
            "target_indication", result["target_id"], result["indication_id"],
            "nelson_tier", "A_genetics", None, result["tier"], None,
            "nelson_llm", source_version, None, result["supporting_pmids"],
            Json(details), model, str(result.get("rationale") or "")[:2000],
        ))
    return paths, rows


def upsert_database_rows(cur, rows: list[tuple]) -> None:
    if not rows:
        return
    from psycopg2.extras import execute_values

    execute_values(cur, """
        INSERT INTO preclin.evidence_score
          (subject_type, subject_id, subject_id2, dimension, category,
           value_numeric, value_text, value_boolean, source, source_version,
           confidence, citation_pmids, citation_details, extracted_by, notes)
        VALUES %s
        ON CONFLICT
          (subject_type, subject_id, subject_id2, dimension, source, source_version)
        DO UPDATE SET
          category = EXCLUDED.category,
          value_text = EXCLUDED.value_text,
          confidence = EXCLUDED.confidence,
          citation_pmids = EXCLUDED.citation_pmids,
          citation_details = EXCLUDED.citation_details,
          extracted_by = EXCLUDED.extracted_by,
          notes = EXCLUDED.notes,
          extracted_at = now()
    """, rows, page_size=1000)


def ingest_directory(cur, directory: Path) -> tuple[list[Path], int]:
    paths, rows = prepare_database_rows(cur, directory)
    upsert_database_rows(cur, rows)
    return paths, len(rows)
