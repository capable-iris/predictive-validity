"""Target-level literature scorer for evidence lines B, C, D, E.

Consumes: PubMed abstracts (via `analyses/classifiers/pubmed_fetch.py`) OR a
supplied list of abstracts + PMIDs. Produces one JSON object per target with
scores 0-3 for each evidence line, matching the schema `db/02_ingest.py`
expects at `data/target_evidence/literature_scores.jsonl`.

Score rubric (from db/01_schema.sql evidence_dimension registry):
- line_b (mechanistic): 0=none, 1=partial, 2=solid, 3=deep multi-line
- line_c (cell):        0=none, 1=cell-line, 2=primary human, 3=iPSC/organoid rescue
- line_d (animal):      0=none, 1=single rodent, 2=solid rodent, 3=multi-species replicated
- line_e (human PD):    0=none, 1=PK only, 2=biomarker moved, 3=biomarker + dose-response

Usage:
    # Score a specific set of targets, fetching PubMed on the fly
    python3 analyses/classifiers/score_target_literature.py \\
        --targets UNC13A,NTRK2,ADCYAP1R1,KL,GALR1,NPY1R,GHSR,VIPR2,APLNR,VGF,CORT \\
        --out data/target_evidence/literature_scores_neuro_candidates.jsonl

    # Score every target in the cohort that lacks a Line C score
    python3 analyses/classifiers/score_target_literature.py \\
        --missing-only line_c_lit \\
        --out data/target_evidence/literature_scores_backfill.jsonl

Output rows are appended (JSONL); the script is resumable — targets already
in the output file are skipped on rerun.

Cost per target: ~$0.03-0.06 with claude-haiku-4-5. 1,000 targets ≈ $30-60.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import (
    annotate,
    append_jsonl,
    call_with_retry,
    db_conn,
    extract_json_block,
    get_client,
    load_processed_keys,
)

PROMPT_VERSION = "v1"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """You are a biomedical evidence extractor. Given PubMed abstracts about a specific gene/target, you assign a 0-3 score on each of four preclinical evidence dimensions and return a single JSON object. You are conservative — do not inflate scores beyond what the abstracts actually support."""

USER_TEMPLATE = """Target gene: {gene}

You are scoring the target-level preclinical evidence for this gene. Use ONLY what is directly supported by the abstracts below. Do not draw on outside knowledge.

Score each dimension 0-3:

**line_b — Mechanistic biology**
  0 = target function not characterized
  1 = general pathway role known
  2 = solid structural / binding / mechanism data
  3 = deep multi-line mechanism (structure + binding + PTM + pathway)

**line_c — Cell-pathway validation**
  0 = no cell data
  1 = cell-line pharmacology (immortalized lines)
  2 = primary human cells respond
  3 = iPSC or organoid disease-model rescue demonstrated

**line_d — Animal in vivo**
  0 = no animal data
  1 = single rodent model, single lab
  2 = solid rodent efficacy (multi-model or replicated)
  3 = multi-species (rodent + non-rodent) or independently replicated

**line_e — Human PD engagement**
  0 = no human data
  1 = pharmacokinetics only
  2 = biomarker moves in expected direction
  3 = biomarker + dose-response + independent replication

Return a JSON object with fields:
  gene: string
  line_b: integer 0-3
  line_c: integer 0-3
  line_d: integer 0-3
  line_e: integer 0-3
  rationale: one short sentence per line explaining the score
  notable_pmids: array of up to 10 PMIDs (strings) most load-bearing for the scores

Abstracts follow. Each item is: PMID | Title | Abstract text.

{abstracts}
"""


def score_one_target(client, gene: str, abstracts: list[dict], model: str) -> dict:
    """Call the LLM to score one target. Emits a `_no_abstracts` sentinel row
    when the abstract cache is empty, so downstream ingest can distinguish
    "never scored" from "scored and found no evidence"."""
    if not abstracts:
        return {
            "gene": gene,
            "line_b": 0, "line_c": 0, "line_d": 0, "line_e": 0,
            "notable_pmids": [],
            "_no_abstracts": True,
            "_model": model,
            "_prompt_version": PROMPT_VERSION,
        }
    abs_blob = "\n\n".join(
        f"PMID {a['pmid']} | {a.get('title','')} | {a.get('abstract','')[:1500]}"
        for a in abstracts[:60]
    )
    user = USER_TEMPLATE.format(gene=gene, abstracts=abs_blob)
    result = call_with_retry(client, model, SYSTEM_PROMPT, user, max_tokens=1024)
    row = extract_json_block(result.text)
    row["gene"] = gene
    row["_n_abstracts_provided"] = len(abstracts)
    return annotate(row, result, PROMPT_VERSION)


def fetch_target_abstracts(cur, gene: str, limit: int = 60) -> list[dict]:
    """Try to load abstracts from a preclin.pubmed_abstract-style cache if present.

    Convention: `preclin.pubmed_target_abstract(gene, pmid, title, abstract)`.
    If the table doesn't exist, fall back to entrez fetch (not implemented here;
    users must supply a --abstracts-cache-dir).
    """
    try:
        cur.execute(
            """
            SELECT pmid, title, abstract
            FROM preclin.pubmed_target_abstract
            WHERE gene = %s
            ORDER BY pmid
            LIMIT %s
            """,
            (gene, limit),
        )
        return [dict(zip(("pmid", "title", "abstract"), r)) for r in cur.fetchall()]
    except Exception:
        return []


def load_abstracts_from_dir(cache_dir: Path, gene: str) -> list[dict]:
    """Fallback: read abstracts from files named {gene}.jsonl in a cache dir."""
    f = cache_dir / f"{gene}.jsonl"
    if not f.exists():
        return []
    out = []
    with f.open() as fh:
        for line in fh:
            try:
                d = json.loads(line)
                if d.get("pmid") and (d.get("title") or d.get("abstract")):
                    out.append(d)
            except Exception:
                pass
    return out


def genes_missing_dimension(cur, dimension: str, source_version: str, limit: int) -> list[str]:
    """Return target symbols that have no evidence_score row for the given dimension."""
    cur.execute(
        f"""
        SELECT DISTINCT t.symbol
        FROM public.targets t
        WHERE NOT EXISTS (
          SELECT 1 FROM preclin.evidence_score es
          WHERE es.subject_type = 'target'
            AND es.subject_id = t.id
            AND es.dimension = %s
            AND es.source_version = %s
        )
          AND (t.pathogen_type IS NULL OR t.pathogen_type = '')
        LIMIT %s
        """,
        (dimension, source_version, limit),
    )
    return [r[0] for r in cur.fetchall()]


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--targets", type=str, default=None,
                    help="Comma-separated gene symbols to score")
    ap.add_argument("--missing-only", type=str, default=None,
                    metavar="DIMENSION",
                    help="Score every gene missing this evidence_score dimension "
                         "(e.g. line_c_lit)")
    ap.add_argument("--limit", type=int, default=1000,
                    help="Cap on genes scored per run (default 1000)")
    ap.add_argument("--abstracts-cache-dir", type=Path, default=None,
                    help="Directory with per-gene JSONL abstract files")
    ap.add_argument("--out", type=Path, required=True,
                    help="Output JSONL path")
    ap.add_argument("--model", type=str, default=DEFAULT_MODEL)
    return ap.parse_args()


def main():
    args = parse_args()

    conn = db_conn()
    cur = conn.cursor()

    if args.targets:
        genes = [g.strip() for g in args.targets.split(",") if g.strip()]
    elif args.missing_only:
        genes = genes_missing_dimension(cur, args.missing_only, PROMPT_VERSION, args.limit)
        print(f"{len(genes)} genes missing {args.missing_only} at version {PROMPT_VERSION}")
    else:
        raise SystemExit("must pass --targets or --missing-only")

    already = load_processed_keys(args.out, "gene")
    todo = [g for g in genes if g not in already]
    print(f"Total genes: {len(genes)}; already scored: {len(already)}; to do: {len(todo)}")

    client = get_client()
    total_cost = 0.0
    for i, gene in enumerate(todo, start=1):
        abstracts = fetch_target_abstracts(cur, gene, limit=60)
        if not abstracts and args.abstracts_cache_dir:
            abstracts = load_abstracts_from_dir(args.abstracts_cache_dir, gene)

        row = score_one_target(client, gene, abstracts, args.model)
        append_jsonl(args.out, row)
        total_cost += row.get("_cost_usd", 0.0)
        prefix = f"[{i}/{len(todo)}]"
        print(
            f"  {prefix} {gene:<12s} "
            f"b={row.get('line_b')} c={row.get('line_c')} "
            f"d={row.get('line_d')} e={row.get('line_e')} "
            f"n_abs={row.get('_n_abstracts_provided',0)} "
            f"cost=${row.get('_cost_usd',0):.4f} cum=${total_cost:.2f}",
            flush=True,
        )

    conn.close()
    print(f"\nDone. Total cost: ${total_cost:.4f}")


if __name__ == "__main__":
    main()
