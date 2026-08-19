"""Store PubMed abstracts used by target/drug evidence extraction.

Two sources are supported:

* ``--from-citations`` refetches PMIDs retained in evidence_score.citation_pmids.
* ``--cache-dir`` imports per-subject JSONL caches, preserving every abstract
  rather than only the load-bearing citations selected by an old model run.

Run after 10_clinical_trial_source_audit.sql. This script never calls an LLM.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import psycopg2


HERE = Path(__file__).resolve().parent


def load_source_helpers():
    path = HERE / "11_ingest_trial_sources.py"
    spec = importlib.util.spec_from_file_location("trial_source_helpers", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


helpers = load_source_helpers()


def cache_document(row: dict, pmid: str) -> dict:
    """Convert a cached abstract row into the canonical source-document shape."""
    title = row.get("title") or None
    abstract = row.get("abstract") or None
    raw = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        "source_type": "journal_abstract",
        "source_name": "pubmed",
        "external_id": pmid,
        "source_version": row.get("source_version") or row.get("updated_at"),
        "source_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "title": title,
        "abstract_text": abstract,
        "body_text": None,
        "raw_content": row,
        "raw_content_text": None,
        "media_type": "application/json",
        "language": row.get("language") or "en",
        "content_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "source_updated_at": None,
        "retrieval_method": "local_abstract_cache_import",
        "attribution": "PubMed / National Library of Medicine",
        "rights_notice": "PubMed abstracts may contain third-party copyrighted material.",
        "metadata": {"cache_fields": sorted(row)},
    }


def citation_links(cur) -> dict[str, set[tuple[str, str]]]:
    """Return PMID -> {(subject_type, stable subject key)} from existing facts."""
    cur.execute(
        """
        SELECT es.subject_type,
               CASE es.subject_type
                 WHEN 'target' THEN t.symbol
                 WHEN 'drug' THEN d.normalized_name
               END AS subject_key,
               unnest(es.citation_pmids) AS pmid
        FROM preclin.evidence_score es
        LEFT JOIN public.targets t
          ON es.subject_type = 'target' AND t.id = es.subject_id
        LEFT JOIN preclin.drug d
          ON es.subject_type = 'drug' AND d.drug_id = es.subject_id
        WHERE es.subject_type IN ('target', 'drug')
          AND cardinality(es.citation_pmids) > 0
        """
    )
    links: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for subject_type, subject_key, pmid in cur.fetchall():
        if subject_key and pmid and str(pmid).isdigit():
            links[str(pmid)].add((subject_type, subject_key))
    return links


def link_reported_citations_to_runs(cur) -> int:
    """Attach recovered PMID documents to legacy runs without claiming exact input."""
    cur.execute(
        """
        INSERT INTO preclin.llm_run_source
          (run_id, source_document_id, relationship, ordinal)
        SELECT DISTINCT run_fact.run_id, sd.source_document_id,
               'reported_citation', 0
        FROM preclin.llm_run_evidence_score run_fact
        JOIN preclin.evidence_score es USING (evidence_id)
        CROSS JOIN LATERAL unnest(es.citation_pmids) cited(pmid)
        JOIN preclin.source_document sd
          ON sd.source_name = 'pubmed' AND sd.external_id = cited.pmid
        ON CONFLICT DO NOTHING
        """
    )
    return cur.rowcount


def ingest_from_citations(cur, conn, args) -> tuple[int, int]:
    links = citation_links(cur)
    pmids = sorted(links)
    fresh = helpers.latest_documents(cur, "pubmed", pmids, args.refresh_days)
    for pmid, (document_id, _) in fresh.items():
        for subject_type, subject_key in links[pmid]:
            if not args.dry_run:
                helpers.link_subject(
                    cur, subject_type, subject_key, document_id,
                    "pubmed_abstract", "evidence_score.citation_pmids",
                )
    todo = [pmid for pmid in pmids if pmid not in fresh]
    if args.limit:
        todo = todo[: args.limit]
    if todo and not args.ncbi_email:
        raise SystemExit("NCBI_EMAIL or --ncbi-email is required for PubMed EFetch")

    fetched = 0
    api_key = os.environ.get("NCBI_API_KEY")
    for batch in helpers.chunks(todo, 200):
        payload = helpers.pubmed_request(batch, args.ncbi_email, api_key)
        for document in helpers.pubmed_documents(payload):
            pmid = document["external_id"]
            if not args.dry_run:
                document_id = helpers.upsert_document(cur, document)
                for subject_type, subject_key in links[pmid]:
                    helpers.link_subject(
                        cur, subject_type, subject_key, document_id,
                        "pubmed_abstract", "evidence_score.citation_pmids",
                    )
            fetched += 1
        if not args.dry_run:
            conn.commit()
        time.sleep(0.11 if api_key else 0.34)
    return fetched, len(fresh)


def ingest_cache(cur, cache_dir: Path, subject_type: str, subject_field: str | None,
                 dry_run: bool) -> tuple[int, int]:
    documents = 0
    links = 0
    for path in sorted(cache_dir.glob("*.jsonl")):
        default_key = path.stem
        with path.open() as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                pmid = str(row.get("pmid") or "")
                subject_key = str(row.get(subject_field) or "") if subject_field else default_key
                if not pmid.isdigit() or not subject_key or not row.get("abstract"):
                    continue
                if not dry_run:
                    document_id = helpers.upsert_document(cur, cache_document(row, pmid))
                    helpers.link_subject(
                        cur, subject_type, subject_key, document_id,
                        "pubmed_abstract", f"cache:{path.name}",
                    )
                documents += 1
                links += 1
    return documents, links


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--from-citations", action="store_true")
    source.add_argument("--cache-dir", type=Path)
    parser.add_argument("--subject-type", choices=("target", "drug"), default="target")
    parser.add_argument(
        "--subject-field",
        help="JSON field containing the subject key; default uses each filename stem",
    )
    parser.add_argument("--limit", type=int, default=100, help="For EFetch; 0 means all missing PMIDs")
    parser.add_argument("--refresh-days", type=int, default=30)
    parser.add_argument("--ncbi-email", default=os.environ.get("NCBI_EMAIL"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    if args.from_citations:
        fetched, fresh = ingest_from_citations(cur, conn, args)
        run_links = 0 if args.dry_run else link_reported_citations_to_runs(cur)
        result = f"fetched={fetched} already_fresh={fresh} legacy_run_links={run_links}"
    else:
        documents, links = ingest_cache(
            cur, args.cache_dir, args.subject_type, args.subject_field, args.dry_run
        )
        result = f"cache_documents={documents} subject_links={links}"
    if args.dry_run:
        conn.rollback()
    else:
        conn.commit()
    conn.close()
    print(f"Done: {result} dry_run={args.dry_run}")


if __name__ == "__main__":
    main()
