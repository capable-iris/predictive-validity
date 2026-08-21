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
    # structured evidence row and canonical PubMed document retrieved.
    .venv/bin/dotenv run -- .venv/bin/python \
      analyses/classifiers/nelson_tier_classify.py \
      --all-clinical --prepare-only \
      --out data/target_evidence/nelson_tiers_all_v3.jsonl

    # Paid scoring pass over the already prepared dossiers. This requires
    # explicit approval before it is run.
    .venv/bin/dotenv run -- .venv/bin/python \
      analyses/classifiers/nelson_tier_classify.py \
      --all-clinical \
      --out data/target_evidence/nelson_tiers_all_v3.jsonl

    # Score selected pairs instead.
    .venv/bin/dotenv run -- .venv/bin/python \
      analyses/classifiers/nelson_tier_classify.py \
      --pair UNC13A:ALS --pair NTRK2:Alzheimer \
      --out data/target_evidence/nelson_tiers_selected_v3.jsonl

By default, dossiers are written beside ``--out`` as
``<stem>.dossiers.jsonl``. PubMed records are read from the immutable
``preclin.source_document`` store populated by
``db/12_ingest_evidence_abstracts.py``. Dossiers preserve every row; normally
all evidence is sent to the model, with deterministic overflow selection only
for the unusually large tail.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
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


PROMPT_VERSION = "v3"
DOSSIER_SCHEMA_VERSION = "nelson_evidence_dossier_v3"
RESULT_SCHEMA_VERSION = "nelson_tier_result_v3"
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_EVIDENCE_CHARS = 400_000
VALID_TIERS = frozenset({"T0", "T1", "T2", "T3", "T4"})
VALID_DIRECTIONS = frozenset({"concordant", "discordant", "unclear"})
VALID_DISEASE_MATCHES = frozenset({"exact", "related", "unmatched", "unclear"})

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


def fetch_pubmed_documents(
    cur,
    gene: str,
    structured_citations: list[str],
) -> list[dict[str, Any]]:
    """Load the latest immutable PubMed snapshots linked or cited here.

    Target-linked documents cover imported per-gene caches. The PMID predicate
    also picks up a canonical document cited by GWAS or Open Targets even when
    an older import omitted its target-subject link.
    """
    cur.execute(
        """
        SELECT to_regclass('preclin.source_document'),
               to_regclass('preclin.source_document_subject')
        """
    )
    if any(value is None for value in cur.fetchone()):
        raise RuntimeError(
            "apply db/10_clinical_trial_source_audit.sql and import PubMed "
            "records with db/12_ingest_evidence_abstracts.py first"
        )
    cur.execute(
        """
        SELECT DISTINCT ON (sd.external_id)
               sd.source_document_id,
               sd.external_id AS pmid,
               sd.title,
               sd.abstract_text AS abstract,
               sd.source_version,
               sd.source_url,
               sd.content_sha256,
               sd.source_updated_at,
               sd.retrieved_at,
               EXISTS (
                 SELECT 1
                 FROM preclin.source_document_subject linked
                 WHERE linked.source_document_id = sd.source_document_id
                   AND linked.subject_type = 'target'
                   AND upper(linked.subject_key) = upper(%s)
               ) AS linked_to_target,
               sd.external_id = ANY(%s) AS cited_by_structured_evidence
        FROM preclin.source_document sd
        WHERE sd.source_name = 'pubmed'
          AND sd.abstract_text IS NOT NULL
          AND (
            sd.external_id = ANY(%s)
            OR EXISTS (
              SELECT 1
              FROM preclin.source_document_subject linked
              WHERE linked.source_document_id = sd.source_document_id
                AND linked.subject_type = 'target'
                AND upper(linked.subject_key) = upper(%s)
            )
          )
        ORDER BY sd.external_id,
                 sd.source_updated_at DESC NULLS LAST,
                 sd.retrieved_at DESC,
                 sd.source_document_id DESC
        """,
        (gene, structured_citations, structured_citations, gene),
    )
    return _rows_as_dicts(cur)


def build_dossier(
    pair: Pair,
    target_evidence: dict[str, list[dict[str, Any]]],
    pubmed_documents: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence = dict(target_evidence)
    evidence["pubmed_documents"] = pubmed_documents
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


def _compact_size(value: Any) -> int:
    return len(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _overflow_rank(record: dict[str, Any], tokens: set[str]) -> tuple[int, int]:
    """Rank only when a dossier exceeds the explicit prompt budget."""
    if not tokens:
        return (2, 0)
    text = " ".join(
        str(record.get(key) or "")
        for key in (
            "phenotype_name", "disease_name", "trait", "evidence_detail",
            "title", "abstract",
        )
    ).lower()
    words = set(re.findall(r"[a-z0-9]+", text))
    overlap = len(tokens & words)
    if overlap == len(tokens):
        return (0, -overlap)
    if overlap:
        return (1, -overlap)
    return (2, 0)


def prompt_evidence(
    dossier: dict[str, Any],
    max_chars: int = DEFAULT_MAX_EVIDENCE_CHARS,
) -> dict[str, Any]:
    """Send all evidence when it fits; trim only the oversized tail."""
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    tokens = indication_tokens(dossier["pair"]["indication"])
    evidence = dossier["evidence"]
    selected = {name: list(rows) for name, rows in evidence.items()}
    full_size = _compact_size(selected)
    if full_size <= max_chars:
        selected["selection_summary"] = {
            "overflow": False,
            "full_evidence_chars": full_size,
            "max_evidence_chars": max_chars,
            "saved": dossier["evidence_counts"],
            "sent": dossier["evidence_counts"],
            "dropped": {name: 0 for name in evidence},
        }
        return selected

    # Preserve the smaller/high-specificity sources first. GWAS and Open
    # Targets are the sources responsible for the observed long tail.
    priority_names = (
        "mendelian_associations",
        "clingen_validity",
        "pubmed_documents",
    )
    tail_names = ("gwas_associations", "open_targets_evidence")
    ordered_names = priority_names + tail_names
    selected = {name: [] for name in ordered_names}
    used = _compact_size(selected)
    for name in priority_names:
        ranked = sorted(
            enumerate(evidence.get(name, [])),
            key=lambda item: (*_overflow_rank(item[1], tokens), item[0]),
        )
        for _, record in ranked:
            record_size = _compact_size(record) + 2
            if used + record_size > max_chars:
                continue
            selected[name].append(record)
            used += record_size
    ranked_tail = {
        name: sorted(
            enumerate(evidence.get(name, [])),
            key=lambda item: (*_overflow_rank(item[1], tokens), item[0]),
        )
        for name in tail_names
    }
    positions = {name: 0 for name in tail_names}
    while any(positions[name] < len(ranked_tail[name]) for name in tail_names):
        for name in tail_names:
            position = positions[name]
            if position >= len(ranked_tail[name]):
                continue
            _, record = ranked_tail[name][position]
            positions[name] += 1
            record_size = _compact_size(record) + 2
            if used + record_size <= max_chars:
                selected[name].append(record)
                used += record_size
    sent = {name: len(selected.get(name, [])) for name in evidence}
    selected["selection_summary"] = {
        "overflow": True,
        "overflow_ordering": "indication-term overlap; model adjudicates disease match",
        "full_evidence_chars": full_size,
        "max_evidence_chars": max_chars,
        "saved": dossier["evidence_counts"],
        "sent": sent,
        "dropped": {
            name: dossier["evidence_counts"][name] - sent[name]
            for name in evidence
        },
    }
    return selected


def score_one_pair(
    client,
    dossier: dict[str, Any],
    model: str,
    max_evidence_chars: int = DEFAULT_MAX_EVIDENCE_CHARS,
) -> dict[str, Any]:
    pair = dossier["pair"]
    selected = prompt_evidence(dossier, max_chars=max_evidence_chars)
    selected_documents = selected.get("pubmed_documents", [])
    missing_source_ids = [
        str(document.get("pmid") or "<unknown>")
        for document in selected_documents
        if not isinstance(document.get("source_document_id"), int)
    ]
    if missing_source_ids:
        raise ValueError(
            "refusing to call a paid model with non-canonical PubMed records; "
            "import them with db/12_ingest_evidence_abstracts.py first "
            f"(missing source_document_id for {', '.join(missing_source_ids[:5])})"
        )
    user = USER_TEMPLATE.format(
        gene=pair["gene"],
        indication=pair["indication"],
        evidence_json=json.dumps(selected, indent=2, sort_keys=True),
    )
    result = call_with_retry(client, model, SYSTEM_PROMPT, user, max_tokens=1024)
    row = extract_json_block(result.text)
    tier = str(row.get("tier", "")).upper()
    direction = str(row.get("direction_concordance", "")).lower()
    disease_match = str(row.get("disease_match", "")).lower()
    if tier not in VALID_TIERS:
        raise ValueError(f"invalid tier returned: {tier!r}")
    if direction not in VALID_DIRECTIONS:
        raise ValueError(f"invalid direction_concordance returned: {direction!r}")
    if disease_match not in VALID_DISEASE_MATCHES:
        raise ValueError(f"invalid disease_match returned: {disease_match!r}")
    if tier == "T4" and direction != "concordant":
        raise ValueError("T4 requires concordant intervention direction")
    reported_pmids = {
        match.group(0)
        for value in (row.get("supporting_pmids") or [])
        for match in [re.search(r"\d+", str(value))]
        if match
    }
    allowed_pmids = set(cited_pmids(selected)) | {
        str(record.get("pmid"))
        for record in selected_documents
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
            "disease_match": disease_match,
            "dossier_sha256": dossier_sha256(dossier),
            "evidence_counts": dossier["evidence_counts"],
            "prompt_selection": selected["selection_summary"],
        }
    )
    annotate(row, result, PROMPT_VERSION)
    row["_source_documents"] = [
        {
            "source_document_id": document["source_document_id"],
            "relationship": "pubmed_abstract_input",
            "ordinal": ordinal,
            "excerpt_text": str(document.get("abstract") or ""),
        }
        for ordinal, document in enumerate(selected_documents)
    ]
    return row


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
        "--prepare-only",
        action="store_true",
        help="Save all evidence dossiers without making paid LLM calls",
    )
    ap.add_argument("--limit", type=int, default=None, help="Cap pairs for a batch")
    ap.add_argument(
        "--max-evidence-chars",
        type=int,
        default=DEFAULT_MAX_EVIDENCE_CHARS,
        help="Overflow budget for serialized evidence (default: 400000)",
    )
    ap.add_argument("--model", default=DEFAULT_MODEL)
    return ap.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv)
    dossiers_path = args.dossiers_out or default_dossiers_path(args.out)

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
    cached_documents: list[dict[str, Any]] = []
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
                cached_documents = fetch_pubmed_documents(
                    cur,
                    pair.gene,
                    cited_pmids(cached_target_evidence),
                )
            dossier = build_dossier(
                pair, cached_target_evidence, cached_documents
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
            row = score_one_pair(
                client,
                dossier,
                args.model,
                max_evidence_chars=args.max_evidence_chars,
            )
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
