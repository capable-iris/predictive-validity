"""Adjudicate a genetics-support tier for target-indication pairs.

The classifier can enumerate every drug-associated target-indication pair in
``preclin.program`` without consulting approval or outcome tables. It saves a
complete, immutable evidence dossier before each optional LLM call so the
adjudication can be reproduced and audited later.

The stored tier is currently for descriptive/audit use only. It remains
excluded from predictive models until cohort-wide coverage and temporal
validation are complete.

Examples::

    # Free/read-only preparation: enumerate all clinical pairs and save every
    # structured evidence row and cached PubMed abstract retrieved.
    .venv/bin/dotenv run -- .venv/bin/python \
      analyses/classifiers/nelson_tier_classify.py \
      --all-clinical --prepare-only \
      --out data/target_evidence/nelson_tiers_all_v2.jsonl

    # Paid scoring pass over the already prepared dossiers. This requires
    # explicit approval before it is run.
    .venv/bin/dotenv run -- .venv/bin/python \
      analyses/classifiers/nelson_tier_classify.py \
      --all-clinical \
      --out data/target_evidence/nelson_tiers_all_v2.jsonl

    # Score selected pairs instead.
    .venv/bin/dotenv run -- .venv/bin/python \
      analyses/classifiers/nelson_tier_classify.py \
      --pair UNC13A:ALS --pair NTRK2:Alzheimer \
      --out data/target_evidence/nelson_tiers_selected_v2.jsonl

By default, dossiers are written beside ``--out`` as
``<stem>.dossiers.jsonl``. If ``--abstracts-cache-dir`` is supplied, every
valid abstract in ``<cache>/<GENE>.jsonl`` is preserved verbatim in the
dossier; only a bounded subset is sent to the LLM.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import (  # noqa: E402
    annotate,
    append_jsonl,
    call_with_retry,
    db_conn,
    extract_json_block,
    get_client,
    read_jsonl,
)


PROMPT_VERSION = "v2"
DOSSIER_SCHEMA_VERSION = "nelson_evidence_dossier_v2"
RESULT_SCHEMA_VERSION = "nelson_tier_result_v2"
DEFAULT_MODEL = "claude-sonnet-4-6"
VALID_TIERS = frozenset({"T0", "T1", "T2", "T3", "T4"})
VALID_DIRECTIONS = frozenset({"concordant", "discordant", "unclear"})

# This remains the repository's historical T0-T4 convention. The prompt no
# longer claims that Nelson et al. published this exact ordinal ladder.
SYSTEM_PROMPT = """You are a human-genetics evidence adjudicator. Use only the evidence supplied in the prompt. Do not use drug approval status, development phase, or outside knowledge. Assign the repository's Nelson-derived T0-T4 genetics-support tier conservatively and cite only PMIDs present in the supplied evidence."""

USER_TEMPLATE = """Target-indication pair:
  Gene: {gene}
  Indication: {indication}

Repository genetics-support rubric (version 2):
- T0 — no reproducible indication-matched human genetic association
- T1 — GWAS association only, without confident target resolution or direction
- T2 — replicated common-variant evidence resolved to this target (coding,
       fine-mapped, or colocalized); therapeutic direction may be unclear
- T3 — matched Mendelian, rare-variant burden, or ClinGen Strong/Definitive
       gene-disease evidence; therapeutic direction may be unclear
- T4 — T3-level evidence plus explicit human genetic direction concordance
       with the proposed intervention direction

Important:
- Missing/unavailable evidence is not positive evidence.
- Do not infer T4 from a drug's clinical use or approval.
- If intervention direction is absent, the highest permissible tier is T3.
- Disease/trait similarity must be justified explicitly.

Structured evidence follows. It may contain gene-level records for traits that
do not match this indication; reject those rather than treating them as support.

{evidence_json}

Return one JSON object with:
  tier: "T0" | "T1" | "T2" | "T3" | "T4"
  direction_concordance: "concordant" | "discordant" | "unclear"
  disease_match: "exact" | "related" | "unmatched" | "unclear"
  evidence_variants: array of strings
  supporting_pmids: array of PMID strings present above
  rationale: 1-3 sentences
  evidence_url: canonical evidence URL if one is supplied, otherwise ""
"""


@dataclass(frozen=True)
class Pair:
    target_id: int | None
    gene: str
    indication_id: int | None
    indication: str
    drugs: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def key(self) -> str:
        if self.target_id is not None and self.indication_id is not None:
            return f"{self.target_id}:{self.indication_id}"
        return f"{self.gene.upper()}:{normalize_text(self.indication)}"


def normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def json_safe(value: Any) -> Any:
    """Recursively convert database values into stable JSON values."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(json_safe(value), sort_keys=True, separators=(",", ":"))


def dossier_sha256(dossier: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dossier).encode("utf-8")).hexdigest()


def jsonl_offsets(path: Path, key_field: str) -> dict[str, int]:
    """Build a small key-to-byte-offset index without retaining large rows."""
    offsets: dict[str, int] = {}
    if not path.exists():
        return offsets
    with path.open("rb") as fh:
        while True:
            offset = fh.tell()
            line = fh.readline()
            if not line:
                break
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = row.get(key_field)
            if key is not None:
                offsets[str(key)] = offset
    return offsets


def read_jsonl_at(path: Path, offset: int) -> dict[str, Any]:
    with path.open("rb") as fh:
        fh.seek(offset)
        return json.loads(fh.readline())


def _rows_as_dicts(cur) -> list[dict[str, Any]]:
    columns = [d[0] for d in cur.description]
    return [json_safe(dict(zip(columns, row))) for row in cur.fetchall()]


def all_clinical_pairs(cur, limit: int | None = None) -> list[Pair]:
    """Enumerate all human, non-placebo drug T-I pairs without outcome data."""
    sql = """
        SELECT DISTINCT
          t.id AS target_id, t.symbol AS gene,
          i.indication_id, i.display_name AS indication,
          d.drug_id, d.display_name AS drug_name, d.modality
        FROM preclin.program p
        JOIN preclin.v_drug_target dt
          ON dt.drug_id = p.drug_id AND dt.role = 'primary'
        JOIN preclin.drug d ON d.drug_id = p.drug_id
        JOIN public.targets t ON t.id = dt.target_id
        JOIN preclin.indication i ON i.indication_id = p.indication_id
        WHERE d.is_placebo IS NOT TRUE
          AND (t.pathogen_type IS NULL OR t.pathogen_type = '')
        ORDER BY t.id, i.indication_id, d.drug_id
    """
    cur.execute(sql)
    grouped: dict[tuple[int, int], dict[str, Any]] = {}
    for target_id, gene, indication_id, indication, drug_id, drug_name, modality in cur.fetchall():
        key = (target_id, indication_id)
        item = grouped.setdefault(
            key,
            {
                "target_id": target_id,
                "gene": gene,
                "indication_id": indication_id,
                "indication": indication,
                "drugs": [],
            },
        )
        item["drugs"].append(
            {"drug_id": drug_id, "drug_name": drug_name, "modality": modality}
        )
    pairs = [
        Pair(
            target_id=v["target_id"],
            gene=v["gene"],
            indication_id=v["indication_id"],
            indication=v["indication"],
            drugs=tuple(v["drugs"]),
        )
        for v in grouped.values()
    ]
    return pairs[:limit] if limit is not None else pairs


def resolve_pair_ids(cur, gene: str, indication: str) -> Pair:
    cur.execute(
        "SELECT id, symbol FROM public.targets WHERE upper(symbol) = upper(%s) LIMIT 1",
        (gene,),
    )
    target = cur.fetchone()
    cur.execute(
        """
        SELECT indication_id, display_name
        FROM preclin.indication
        WHERE normalized_name = %s
           OR lower(display_name) = lower(%s)
        ORDER BY (lower(display_name) = lower(%s)) DESC, indication_id
        LIMIT 1
        """,
        (normalize_text(indication), indication, indication),
    )
    ind = cur.fetchone()
    return Pair(
        target_id=target[0] if target else None,
        gene=target[1] if target else gene.strip(),
        indication_id=ind[0] if ind else None,
        indication=ind[1] if ind else indication.strip(),
    )


def load_pairs_from_csv(cur, path: Path) -> list[Pair]:
    pairs: list[Pair] = []
    with path.open() as fh:
        for row in csv.DictReader(fh):
            gene = (row.get("gene") or row.get("target") or "").strip()
            indication = (
                row.get("indication") or row.get("indication_keyword") or ""
            ).strip()
            if gene and indication:
                pairs.append(resolve_pair_ids(cur, gene, indication))
    return pairs


def load_requested_pairs(cur, args) -> list[Pair]:
    if args.all_clinical:
        return all_clinical_pairs(cur, args.limit)
    if args.pairs:
        pairs = load_pairs_from_csv(cur, args.pairs)
    else:
        pairs = []
        for raw in args.pair or []:
            if ":" not in raw:
                raise SystemExit(f"--pair '{raw}' expects GENE:INDICATION")
            gene, indication = raw.split(":", 1)
            pairs.append(resolve_pair_ids(cur, gene.strip(), indication.strip()))
    return pairs[: args.limit] if args.limit is not None else pairs


def fetch_target_evidence(cur, target_id: int | None) -> dict[str, list[dict[str, Any]]]:
    if target_id is None:
        return {
            "mendelian_associations": [],
            "clingen_validity": [],
            "gwas_associations": [],
            "open_targets_evidence": [],
        }

    cur.execute(
        """
        SELECT id, source, source_id, phenotype_name, inheritance, association_type
        FROM public.mendelian_associations
        WHERE target_id = %s
        ORDER BY source, source_id, phenotype_name
        """,
        (target_id,),
    )
    mendelian = _rows_as_dicts(cur)

    cur.execute(
        """
        SELECT disease_name, disease_mondo, classification, mode_of_inheritance,
               sop_version, classified_date
        FROM public.clingen_validity
        WHERE target_id = %s
        ORDER BY disease_name, classification
        """,
        (target_id,),
    )
    clingen = _rows_as_dicts(cur)

    cur.execute(
        """
        SELECT id, rsid, chromosome, position, effect_allele, risk_allele_freq,
               p_value, p_value_mlog, or_or_beta, ci_text, trait,
               mapped_trait_uri, study_accession, study_pmid, context
        FROM public.gwas_associations
        WHERE target_id = %s
        ORDER BY p_value ASC NULLS LAST, id
        """,
        (target_id,),
    )
    gwas = _rows_as_dicts(cur)

    cur.execute(
        """
        SELECT te.id, te.disease_id, d.name AS disease_name, d.efo_id, d.mondo_id,
               te.overall_score, te.genetic_score, te.somatic_score,
               te.literature_score, te.l2g_score, te.key_pmids,
               te.evidence_type, te.evidence_detail, te.variant_count,
               te.how_identified, te.is_mendelian, te.intervention_direction,
               te.intervention_direction_source, te.confidence, te.source,
               te.created_at, te.updated_at
        FROM public.target_evidence te
        LEFT JOIN public.diseases d ON d.id = te.disease_id
        WHERE te.target_id = %s
        ORDER BY te.genetic_score DESC NULLS LAST, te.id
        """,
        (target_id,),
    )
    open_targets = _rows_as_dicts(cur)

    return {
        "mendelian_associations": mendelian,
        "clingen_validity": clingen,
        "gwas_associations": gwas,
        "open_targets_evidence": open_targets,
    }


def load_cached_abstracts(cache_dir: Path | None, gene: str) -> list[dict[str, Any]]:
    """Load and preserve every valid cached abstract for a gene."""
    if cache_dir is None:
        return []
    path = cache_dir / f"{gene}.jsonl"
    if not path.exists():
        return []
    abstracts: list[dict[str, Any]] = []
    for row in read_jsonl(path):
        if row.get("pmid") and (row.get("title") or row.get("abstract")):
            abstracts.append(json_safe(row))
    return abstracts


def cited_pmids(evidence: dict[str, list[dict[str, Any]]]) -> list[str]:
    """Collect every PubMed identifier referenced by structured evidence."""
    values: list[Any] = []
    for row in evidence.get("gwas_associations", []):
        values.append(row.get("study_pmid"))
    for row in evidence.get("open_targets_evidence", []):
        key_pmids = row.get("key_pmids") or []
        values.extend(key_pmids if isinstance(key_pmids, list) else [key_pmids])
    return list(
        dict.fromkeys(
            match.group(0)
            for value in values
            if value is not None
            for match in [re.search(r"\d+", str(value))]
            if match
        )
    )


def _element_text(element) -> str:
    if element is None:
        return ""
    return "".join(element.itertext()).strip()


def parse_pubmed_xml(payload: bytes) -> list[dict[str, Any]]:
    """Parse full PubMed citation metadata and abstracts returned by EFetch."""
    root = ET.fromstring(payload)
    records: list[dict[str, Any]] = []
    for item in root.findall(".//PubmedArticle"):
        citation = item.find("MedlineCitation")
        article = citation.find("Article") if citation is not None else None
        pmid = _element_text(citation.find("PMID") if citation is not None else None)
        if not pmid:
            continue
        abstract_sections = []
        if article is not None:
            for abstract in article.findall("./Abstract/AbstractText"):
                text = _element_text(abstract)
                label = abstract.attrib.get("Label") or abstract.attrib.get("NlmCategory")
                abstract_sections.append(f"{label}: {text}" if label and text else text)
        article_ids = {}
        for article_id in item.findall("./PubmedData/ArticleIdList/ArticleId"):
            id_type = article_id.attrib.get("IdType")
            if id_type:
                article_ids[id_type] = _element_text(article_id)
        journal = article.find("Journal") if article is not None else None
        records.append(
            {
                "pmid": pmid,
                "title": _element_text(article.find("ArticleTitle") if article is not None else None),
                "abstract": "\n".join(section for section in abstract_sections if section),
                "abstract_copyright": _element_text(
                    article.find("./Abstract/CopyrightInformation")
                    if article is not None else None
                ),
                "journal": _element_text(journal.find("Title") if journal is not None else None),
                "publication_date": _element_text(
                    journal.find("./JournalIssue/PubDate") if journal is not None else None
                ),
                "publication_types": [
                    _element_text(value)
                    for value in (article.findall("./PublicationTypeList/PublicationType") if article is not None else [])
                ],
                "article_ids": article_ids,
                "source_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "retrieved_via": "NCBI EFetch",
            }
        )
    return records


def fetch_pubmed_records(
    pmids: list[str],
    email: str,
    api_key: str | None = None,
    batch_size: int = 200,
) -> list[dict[str, Any]]:
    """Fetch cited PubMed records in policy-compliant EFetch batches."""
    records: list[dict[str, Any]] = []
    delay = 0.11 if api_key else 0.34
    for start in range(0, len(pmids), batch_size):
        batch = pmids[start : start + batch_size]
        params = {
            "db": "pubmed",
            "id": ",".join(batch),
            "retmode": "xml",
            "tool": "predictive_validity_nelson_tiers",
            "email": email,
        }
        if api_key:
            params["api_key"] = api_key
        url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?"
            + urllib.parse.urlencode(params)
        )
        with urllib.request.urlopen(url, timeout=60) as response:
            records.extend(parse_pubmed_xml(response.read()))
        if start + batch_size < len(pmids):
            time.sleep(delay)
    return records


def merge_abstracts(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate literature by PMID while preserving the first full record."""
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for row in group:
            pmid = str(row.get("pmid") or "").strip()
            if pmid and pmid not in merged:
                merged[pmid] = json_safe(row)
    return list(merged.values())


def build_dossier(
    pair: Pair,
    target_evidence: dict[str, list[dict[str, Any]]],
    abstracts: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence = dict(target_evidence)
    evidence["pubmed_abstracts"] = abstracts
    return {
        "schema_version": DOSSIER_SCHEMA_VERSION,
        "pair_key": pair.key,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "pair": json_safe(asdict(pair)),
        "evidence": evidence,
        "evidence_counts": {name: len(rows) for name, rows in evidence.items()},
    }


def indication_tokens(indication: str) -> set[str]:
    stop = {
        "and", "or", "of", "the", "for", "with", "without", "in", "to",
        "adult", "adults", "patients", "disease", "syndrome", "disorder",
        "advanced", "recurrent", "metastatic", "treatment",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9]+", indication.lower())
        if len(token) >= 3 and token not in stop
    }


def _record_matches_indication(record: dict[str, Any], tokens: set[str]) -> bool:
    if not tokens:
        return False
    text = " ".join(
        str(record.get(k) or "")
        for k in (
            "phenotype_name", "disease_name", "trait", "evidence_detail",
            "title", "abstract",
        )
    ).lower()
    return bool(tokens & set(re.findall(r"[a-z0-9]+", text)))


def prompt_evidence(dossier: dict[str, Any]) -> dict[str, Any]:
    """Bound prompt size without discarding anything from the saved dossier."""
    tokens = indication_tokens(dossier["pair"]["indication"])
    evidence = dossier["evidence"]
    limits = {
        "mendelian_associations": 40,
        "clingen_validity": 30,
        "gwas_associations": 50,
        "open_targets_evidence": 40,
    }
    selected: dict[str, Any] = {}
    for name, limit in limits.items():
        rows = evidence.get(name, [])
        matched = [r for r in rows if _record_matches_indication(r, tokens)]
        unmatched = [r for r in rows if r not in matched]
        selected[name] = (matched + unmatched)[:limit]

    # Full abstracts remain in the dossier. A bounded subset goes to the model;
    # no abstract is truncated in the audit artifact.
    abstracts = evidence.get("pubmed_abstracts", [])
    matched_abstracts = [
        row for row in abstracts if _record_matches_indication(row, tokens)
    ]
    unmatched_abstracts = [row for row in abstracts if row not in matched_abstracts]
    selected["pubmed_abstracts"] = []
    for row in (matched_abstracts + unmatched_abstracts)[:20]:
        prompt_row = dict(row)
        prompt_row["abstract"] = str(prompt_row.get("abstract") or "")[:2500]
        selected["pubmed_abstracts"].append(prompt_row)
    selected["saved_evidence_counts"] = dossier["evidence_counts"]
    return selected


def score_one_pair(client, dossier: dict[str, Any], model: str) -> dict[str, Any]:
    pair = dossier["pair"]
    user = USER_TEMPLATE.format(
        gene=pair["gene"],
        indication=pair["indication"],
        evidence_json=json.dumps(prompt_evidence(dossier), indent=2, sort_keys=True),
    )
    result = call_with_retry(client, model, SYSTEM_PROMPT, user, max_tokens=1024)
    row = extract_json_block(result.text)
    tier = str(row.get("tier", "")).upper()
    direction = str(row.get("direction_concordance", "")).lower()
    if tier not in VALID_TIERS:
        raise ValueError(f"invalid tier returned: {tier!r}")
    if direction not in VALID_DIRECTIONS:
        raise ValueError(f"invalid direction_concordance returned: {direction!r}")
    reported_pmids = {
        match.group(0)
        for value in (row.get("supporting_pmids") or [])
        for match in [re.search(r"\d+", str(value))]
        if match
    }
    allowed_pmids = set(cited_pmids(dossier["evidence"])) | {
        str(record.get("pmid"))
        for record in dossier["evidence"].get("pubmed_abstracts", [])
        if record.get("pmid")
    }
    unsupported = reported_pmids - allowed_pmids
    if unsupported:
        raise ValueError(f"model cited PMIDs absent from dossier: {sorted(unsupported)}")
    row["supporting_pmids"] = sorted(reported_pmids)
    row.update(
        {
            "schema_version": RESULT_SCHEMA_VERSION,
            "pair_key": dossier["pair_key"],
            "target_id": pair["target_id"],
            "gene": pair["gene"],
            "indication_id": pair["indication_id"],
            "indication": pair["indication"],
            "tier": tier,
            "direction_concordance": direction,
            "dossier_sha256": dossier_sha256(dossier),
            "evidence_counts": dossier["evidence_counts"],
        }
    )
    return annotate(row, result, PROMPT_VERSION)


def default_dossiers_path(out: Path) -> Path:
    suffix = out.suffix or ".jsonl"
    return out.with_name(f"{out.stem}.dossiers{suffix}")


def parse_args(argv: Iterable[str] | None = None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--all-clinical",
        action="store_true",
        help="Score all non-placebo human target-indication pairs in programs",
    )
    source.add_argument("--pairs", type=Path, help="CSV with gene/target and indication")
    source.add_argument(
        "--pair", action="append", help="GENE:INDICATION; may be repeated"
    )
    ap.add_argument("--out", type=Path, required=True, help="Result JSONL path")
    ap.add_argument(
        "--dossiers-out",
        type=Path,
        default=None,
        help="Full evidence-dossier JSONL (default: beside --out)",
    )
    ap.add_argument(
        "--abstracts-cache-dir",
        type=Path,
        default=None,
        help="Optional directory of <GENE>.jsonl PubMed abstract caches",
    )
    ap.add_argument(
        "--fetch-cited-pubmed",
        action="store_true",
        help="Fetch and save PubMed records cited by GWAS/Open Targets evidence",
    )
    ap.add_argument(
        "--ncbi-email",
        default=None,
        help="Contact email required by NCBI when --fetch-cited-pubmed is used",
    )
    ap.add_argument(
        "--prepare-only",
        action="store_true",
        help="Save all evidence dossiers without making paid LLM calls",
    )
    ap.add_argument("--limit", type=int, default=None, help="Cap pairs for a batch")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    return ap.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv)
    dossiers_path = args.dossiers_out or default_dossiers_path(args.out)
    ncbi_email = args.ncbi_email or os.environ.get("NCBI_EMAIL")
    if args.fetch_cited_pubmed and not ncbi_email:
        raise SystemExit(
            "--fetch-cited-pubmed requires --ncbi-email or NCBI_EMAIL"
        )

    conn = db_conn()
    cur = conn.cursor()
    pairs = load_requested_pairs(cur, args)
    if not pairs:
        conn.close()
        raise SystemExit("no target-indication pairs found")

    scored = {row.get("pair_key") for row in read_jsonl(args.out)}
    saved_dossiers = jsonl_offsets(dossiers_path, "pair_key")
    todo = [pair for pair in pairs if pair.key not in scored]
    print(
        f"Total pairs: {len(pairs)}; already scored: {len(pairs) - len(todo)}; "
        f"to do: {len(todo)}"
    )

    client = None if args.prepare_only else get_client()
    cached_target_id: int | None = None
    cached_target_evidence: dict[str, list[dict[str, Any]]] | None = None
    cached_gene: str | None = None
    cached_abstracts: list[dict[str, Any]] = []
    total_cost = 0.0

    for index, pair in enumerate(todo, start=1):
        dossier_offset = saved_dossiers.get(pair.key)
        dossier = (
            read_jsonl_at(dossiers_path, dossier_offset)
            if dossier_offset is not None else None
        )
        if dossier is None:
            if pair.target_id != cached_target_id or cached_target_evidence is None:
                cached_target_id = pair.target_id
                cached_target_evidence = fetch_target_evidence(cur, pair.target_id)
            if pair.gene != cached_gene:
                cached_gene = pair.gene
                cached = load_cached_abstracts(
                    args.abstracts_cache_dir, pair.gene
                )
                fetched: list[dict[str, Any]] = []
                if args.fetch_cited_pubmed:
                    present = {str(row.get("pmid")) for row in cached}
                    missing = [
                        pmid for pmid in cited_pmids(cached_target_evidence)
                        if pmid not in present
                    ]
                    fetched = fetch_pubmed_records(
                        missing,
                        email=ncbi_email,
                        api_key=os.environ.get("NCBI_API_KEY"),
                    )
                cached_abstracts = merge_abstracts(cached, fetched)
            dossier = build_dossier(
                pair, cached_target_evidence, cached_abstracts
            )
            append_jsonl(dossiers_path, dossier)

        counts = dossier["evidence_counts"]
        if args.prepare_only:
            print(
                f"  [{index}/{len(todo)}] prepared {pair.gene:<12s} × "
                f"{pair.indication[:40]:<40s} evidence={sum(counts.values())}",
                flush=True,
            )
            continue

        try:
            row = score_one_pair(client, dossier, args.model)
        except Exception as exc:
            print(
                f"  [{index}/{len(todo)}] {pair.gene}:{pair.indication} FAILED: {exc}",
                file=sys.stderr,
                flush=True,
            )
            continue
        row["_dossier_file"] = dossiers_path.name
        append_jsonl(args.out, row)
        total_cost += float(row.get("_cost_usd") or 0.0)
        print(
            f"  [{index}/{len(todo)}] {pair.gene:<12s} × "
            f"{pair.indication[:32]:<32s} -> {row['tier']} "
            f"({row['direction_concordance']}) cost=${row['_cost_usd']:.4f} "
            f"cum=${total_cost:.2f}",
            flush=True,
        )

    conn.close()
    if args.prepare_only:
        print(f"\nPrepared dossiers at {dossiers_path}; no LLM calls were made.")
    else:
        print(
            f"\nDone. Results: {args.out}; dossiers: {dossiers_path}; "
            f"total cost=${total_cost:.4f}"
        )


if __name__ == "__main__":
    main()
