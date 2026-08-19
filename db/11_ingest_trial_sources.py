"""Ingest immutable ClinicalTrials.gov records and linked PubMed abstracts.

The default scope is deliberately small: trials that already have an LLM
classification. Use ``--scope program`` for the benchmark's program trials or
``--scope all`` only when a full registry mirror is actually intended.

This script does not call an LLM. Run it after 10_clinical_trial_source_audit.sql.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import psycopg2
from psycopg2.extras import Json


CTG_STUDY_URL = "https://clinicaltrials.gov/api/v2/studies/{nct_id}"
PUBMED_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
USER_AGENT = "predictive-validity-trial-source-ingest/1.0"
CTG_REQUEST_INTERVAL_SECONDS = 0.2  # Globally cap request starts at 5/second.
_ctg_rate_lock = threading.Lock()
_ctg_next_request = 0.0


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def text_content(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def flatten_json(value: object, path: str = "") -> list[str]:
    """Render a deterministic, field-labelled text copy of a JSON record."""
    lines: list[str] = []
    if isinstance(value, dict):
        for key in sorted(value):
            child = f"{path}.{key}" if path else key
            lines.extend(flatten_json(value[key], child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            lines.extend(flatten_json(item, f"{path}[{index}]"))
    elif value is not None:
        rendered = str(value).strip()
        if rendered:
            lines.append(f"{path}: {rendered}")
    return lines


def fetch_json(url: str, timeout: int = 60) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def fetch_ctgov_record(nct_id: str) -> dict:
    return fetch_json(CTG_STUDY_URL.format(nct_id=urllib.parse.quote(nct_id)))


def fetch_ctgov_result(nct_id: str) -> tuple[str, dict | None, Exception | None]:
    """Fetch one record with bounded request starts and transient-error retries."""
    global _ctg_next_request
    transient_statuses = {429, 500, 502, 503, 504}
    for attempt in range(5):
        with _ctg_rate_lock:
            delay = max(0.0, _ctg_next_request - time.monotonic())
            if delay:
                time.sleep(delay)
            _ctg_next_request = time.monotonic() + CTG_REQUEST_INTERVAL_SECONDS
        try:
            return nct_id, fetch_ctgov_record(nct_id), None
        except urllib.error.HTTPError as exc:
            if exc.code not in transient_statuses or attempt == 4:
                return nct_id, None, exc
            retry_after = exc.headers.get("Retry-After")
            try:
                wait = float(retry_after) if retry_after else 2 ** attempt
            except ValueError:
                wait = 2 ** attempt
            time.sleep(min(wait, 30.0))
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == 4:
                return nct_id, None, exc
            time.sleep(2 ** attempt)
        except (ValueError, ET.ParseError) as exc:
            return nct_id, None, exc
    raise AssertionError("unreachable")


def collect_pmids(record: object) -> dict[str, str]:
    """Collect PMIDs and the strongest relationship stated in a CT.gov record."""
    found: dict[str, str] = {}

    def visit(value: object) -> None:
        if isinstance(value, dict):
            pmid = value.get("pmid")
            if pmid is not None and str(pmid).isdigit():
                reference_type = str(value.get("type") or value.get("referenceType") or "").upper()
                relationship = (
                    "results_publication" if "RESULT" in reference_type else "background_publication"
                )
                old = found.get(str(pmid))
                if old != "results_publication":
                    found[str(pmid)] = relationship
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(record)
    return found


def ctgov_document(record: dict, nct_id: str) -> dict:
    protocol = record.get("protocolSection", {})
    identification = protocol.get("identificationModule", {})
    status = protocol.get("statusModule", {})
    raw = canonical_json_bytes(record)
    return {
        "source_type": "registry_record",
        "source_name": "clinicaltrials.gov",
        "external_id": nct_id,
        "source_version": (status.get("lastUpdatePostDateStruct") or {}).get("date"),
        "source_url": f"https://clinicaltrials.gov/study/{nct_id}",
        "title": identification.get("officialTitle") or identification.get("briefTitle"),
        "abstract_text": (protocol.get("descriptionModule") or {}).get("briefSummary"),
        "body_text": "\n".join(flatten_json(record)),
        "raw_content": record,
        "raw_content_text": None,
        "media_type": "application/json",
        "language": "en",
        "content_sha256": sha256(raw),
        "source_updated_at": None,
        "retrieval_method": "clinicaltrials.gov_api_v2",
        "attribution": "ClinicalTrials.gov",
        "rights_notice": "https://clinicaltrials.gov/about-site/terms-conditions",
        "metadata": {"has_results": bool(record.get("hasResults"))},
    }


def pubmed_request(pmids: list[str], email: str, api_key: str | None) -> bytes:
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "tool": "predictive_validity",
        "email": email,
    }
    if api_key:
        params["api_key"] = api_key
    data = urllib.parse.urlencode(params).encode()
    request = urllib.request.Request(
        PUBMED_EFETCH_URL,
        data=data,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


def pubmed_documents(payload: bytes) -> list[dict]:
    root = ET.fromstring(payload)
    documents: list[dict] = []
    for article in root.findall(".//PubmedArticle"):
        pmid = text_content(article.find("./MedlineCitation/PMID"))
        if not pmid:
            continue
        article_node = article.find("./MedlineCitation/Article")
        title = text_content(article_node.find("./ArticleTitle") if article_node is not None else None)
        sections = []
        if article_node is not None:
            for section in article_node.findall("./Abstract/AbstractText"):
                content = text_content(section)
                label = section.attrib.get("Label") or section.attrib.get("NlmCategory")
                if content:
                    sections.append(f"{label}: {content}" if label else content)
        abstract = "\n".join(sections) or None
        raw_text = ET.tostring(article, encoding="unicode")
        ids = {
            node.attrib.get("IdType", "unknown"): text_content(node)
            for node in article.findall("./PubmedData/ArticleIdList/ArticleId")
        }
        journal = text_content(article.find("./MedlineCitation/Article/Journal/Title"))
        authors = []
        for author in article.findall("./MedlineCitation/Article/AuthorList/Author"):
            name = " ".join(filter(None, [text_content(author.find("ForeName")), text_content(author.find("LastName"))]))
            if name:
                authors.append(name)
        revised = article.find("./MedlineCitation/DateRevised")
        revised_parts = []
        if revised is not None:
            revised_parts = [text_content(revised.find(part)) for part in ("Year", "Month", "Day")]
        version = "-".join(part for part in revised_parts if part) or None
        documents.append(
            {
                "source_type": "journal_abstract",
                "source_name": "pubmed",
                "external_id": pmid,
                "source_version": version,
                "source_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "title": title or None,
                "abstract_text": abstract,
                "body_text": None,
                "raw_content": None,
                "raw_content_text": raw_text,
                "media_type": "application/xml",
                "language": "en",
                "content_sha256": sha256(raw_text.encode()),
                "source_updated_at": None,
                "retrieval_method": "ncbi_eutils_efetch_pubmed_xml",
                "attribution": "PubMed / National Library of Medicine",
                "rights_notice": "PubMed abstracts may contain third-party copyrighted material.",
                "metadata": {"article_ids": ids, "journal": journal or None, "authors": authors},
            }
        )
    return documents


def upsert_document(cur, document: dict) -> int:
    cur.execute(
        """
        INSERT INTO preclin.source_document (
          source_type, source_name, external_id, source_version, source_url,
          title, abstract_text, body_text, raw_content, raw_content_text,
          media_type, language, content_sha256, source_updated_at,
          retrieval_method, attribution, rights_notice, metadata
        ) VALUES (
          %(source_type)s, %(source_name)s, %(external_id)s, %(source_version)s,
          %(source_url)s, %(title)s, %(abstract_text)s, %(body_text)s,
          %(raw_content)s, %(raw_content_text)s, %(media_type)s, %(language)s,
          %(content_sha256)s, %(source_updated_at)s, %(retrieval_method)s,
          %(attribution)s, %(rights_notice)s, %(metadata)s
        )
        ON CONFLICT (source_name, external_id, content_sha256)
        DO UPDATE SET last_seen_at = now()
        RETURNING source_document_id
        """,
        {**document, "raw_content": Json(document["raw_content"]), "metadata": Json(document["metadata"])},
    )
    return cur.fetchone()[0]


def link_subject(
    cur, subject_type: str, subject_key: str, document_id: int,
    relationship: str, discovered_from: str,
) -> None:
    cur.execute(
        """
        INSERT INTO preclin.source_document_subject
          (subject_type, subject_key, source_document_id, relationship, discovered_from)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        (subject_type, subject_key, document_id, relationship, discovered_from),
    )


def link_trial(cur, nct_id: str, document_id: int, relationship: str, discovered_from: str) -> None:
    link_subject(cur, "trial", nct_id, document_id, relationship, discovered_from)


def candidate_nct_ids(cur, scope: str, explicit: list[str]) -> list[str]:
    if explicit:
        return sorted(set(explicit))
    query = {
        "classified": "SELECT DISTINCT subject_key AS nct_id FROM preclin.classification WHERE subject_type = 'trial'",
        "program": "SELECT DISTINCT nct_id FROM preclin.program_trial",
        "all": "SELECT DISTINCT nct_id FROM public.trials",
    }[scope]
    query += " ORDER BY nct_id"
    cur.execute(query)
    return [row[0] for row in cur.fetchall()]


def recently_fetched(cur, nct_ids: list[str], refresh_days: int) -> set[str]:
    if refresh_days <= 0 or not nct_ids:
        return set()
    cur.execute(
        """
        SELECT DISTINCT external_id
        FROM preclin.source_document
        WHERE source_name = 'clinicaltrials.gov'
          AND external_id = ANY(%s)
          AND last_seen_at >= now() - %s
        """,
        (nct_ids, timedelta(days=refresh_days)),
    )
    return {row[0] for row in cur.fetchall()}


def latest_documents(
    cur, source_name: str, external_ids: list[str], refresh_days: int | None = None
) -> dict[str, tuple[int, object]]:
    """Return latest document id/content per external id, optionally only fresh rows."""
    if not external_ids:
        return {}
    freshness = ""
    params: list[object] = [source_name, external_ids]
    if refresh_days is not None:
        if refresh_days <= 0:
            return {}
        freshness = "AND last_seen_at >= now() - %s"
        params.append(timedelta(days=refresh_days))
    cur.execute(
        f"""
        SELECT DISTINCT ON (external_id)
               external_id, source_document_id, raw_content
        FROM preclin.source_document
        WHERE source_name = %s
          AND external_id = ANY(%s)
          {freshness}
        ORDER BY external_id, retrieved_at DESC, source_document_id DESC
        """,
        params,
    )
    return {row[0]: (row[1], row[2]) for row in cur.fetchall()}


def chunks(values: list[str], size: int):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--scope", choices=("classified", "program", "all"), default="classified")
    parser.add_argument("--nct-id", action="append", default=[], help="Explicit NCT id; repeatable")
    parser.add_argument("--limit", type=int, default=100, help="0 means no limit")
    parser.add_argument("--refresh-days", type=int, default=30, help="0 refetches every selected trial")
    parser.add_argument(
        "--workers", type=int, default=4,
        help="Concurrent ClinicalTrials.gov fetches; starts are capped at 5/second (default 4)",
    )
    parser.add_argument("--skip-pubmed", action="store_true")
    parser.add_argument("--ncbi-email", default=os.environ.get("NCBI_EMAIL"))
    parser.add_argument("--dry-run", action="store_true", help="Fetch and parse without writing")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 1 <= args.workers <= 32:
        raise SystemExit("--workers must be between 1 and 32")
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    selected = candidate_nct_ids(cur, args.scope, args.nct_id)
    fresh = recently_fetched(cur, selected, args.refresh_days)
    todo = [nct_id for nct_id in selected if nct_id not in fresh]
    if args.limit:
        todo = todo[: args.limit]
    print(f"Selected {len(selected)} trials; {len(fresh)} fresh; fetching {len(todo)}")

    pmid_trials: dict[str, list[tuple[str, str]]] = defaultdict(list)
    # Publication discovery remains available when registry snapshots are still
    # inside their freshness window (for example, after NCBI_EMAIL is added).
    for nct_id, (_, record) in latest_documents(
        cur, "clinicaltrials.gov", sorted(fresh)
    ).items():
        for pmid, relationship in collect_pmids(record).items():
            pmid_trials[pmid].append((nct_id, relationship))

    registry_count = 0
    failures = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        results = executor.map(fetch_ctgov_result, todo)
        for index, (nct_id, record, fetch_error) in enumerate(results, start=1):
            try:
                if fetch_error is not None:
                    raise fetch_error
                assert record is not None
                document = ctgov_document(record, nct_id)
                for pmid, relationship in collect_pmids(record).items():
                    pmid_trials[pmid].append((nct_id, relationship))
                if not args.dry_run:
                    document_id = upsert_document(cur, document)
                    link_trial(
                        cur, nct_id, document_id, "registry_record",
                        "clinicaltrials.gov_api_v2",
                    )
                    if index % 100 == 0:
                        conn.commit()
                registry_count += 1
            except (urllib.error.URLError, TimeoutError, ValueError, ET.ParseError) as exc:
                failures += 1
                print(f"  {nct_id}: {type(exc).__name__}", file=sys.stderr)
            if index % 25 == 0 or index == len(todo):
                print(
                    f"  registry {index}/{len(todo)}; stored={registry_count}; "
                    f"failures={failures}"
                )

    abstract_count = 0
    existing_abstract_count = 0
    if pmid_trials and not args.skip_pubmed:
        fresh_abstracts = latest_documents(
            cur, "pubmed", sorted(pmid_trials), args.refresh_days
        )
        if not args.dry_run:
            for pmid, (document_id, _) in fresh_abstracts.items():
                for nct_id, relationship in pmid_trials[pmid]:
                    link_trial(cur, nct_id, document_id, relationship, "clinicaltrials.gov_reference")
            conn.commit()
        existing_abstract_count = len(fresh_abstracts)
        fetch_pmids = sorted(set(pmid_trials) - set(fresh_abstracts))
        if fetch_pmids and not args.ncbi_email:
            print("Skipping PubMed fetch: set NCBI_EMAIL or pass --ncbi-email (required by NCBI).", file=sys.stderr)
        elif fetch_pmids:
            api_key = os.environ.get("NCBI_API_KEY")
            for batch in chunks(fetch_pmids, 200):
                try:
                    payload = pubmed_request(batch, args.ncbi_email, api_key)
                    for document in pubmed_documents(payload):
                        pmid = document["external_id"]
                        if not args.dry_run:
                            document_id = upsert_document(cur, document)
                            for nct_id, relationship in pmid_trials[pmid]:
                                link_trial(cur, nct_id, document_id, relationship, "clinicaltrials.gov_reference")
                        abstract_count += 1
                    if not args.dry_run:
                        conn.commit()
                except (urllib.error.URLError, TimeoutError, ValueError, ET.ParseError) as exc:
                    failures += 1
                    print(f"  PubMed batch failed: {type(exc).__name__}", file=sys.stderr)
                time.sleep(0.11 if api_key else 0.34)

    if args.dry_run:
        conn.rollback()
    else:
        conn.commit()
    conn.close()
    print(
        f"Done: registry_documents={registry_count} pubmed_abstracts={abstract_count} "
        f"pubmed_fresh={existing_abstract_count} references={len(pmid_trials)} "
        f"failures={failures} dry_run={args.dry_run}"
    )


if __name__ == "__main__":
    main()
