"""Assign Nelson tier (T0-T4) to a target-indication pair from Mendelian +
GWAS + ClinGen evidence.

Consumes the evidence that already lives in `public.*`:
- public.mendelian_associations
- public.gwas_associations
- public.clingen_validity
- public.target_evidence (Open Targets)

Feeds those to the LLM as structured context and gets back a tier assignment
+ direction concordance + supporting variants + rationale + PMIDs.

Writes CSV compatible with the batch imports (`nelson_tiers_batch_*.csv`)
consumed by `db/02_ingest.py` (row schema: gene, indication, tier, evidence_url).

The Nelson (2015) tier framework:
- T0 — no reproducible human genetic association
- T1 — GWAS association only (no fine-mapping to target)
- T2 — replicated common variant fine-mapped to target with matched direction
       OR replicated GWAS coding variant in the target gene
- T3 — Mendelian LoF/GoF with defined phenotype, or ClinGen Definitive/Strong,
       with direction concordant to therapeutic hypothesis
- T4 — Mendelian direct match + drug direction validation in humans

Usage:
    python3 analyses/classifiers/nelson_tier_classify.py \\
        --pairs data/target_indication_pairs_to_score.csv \\
        --out   data/target_evidence/nelson_tiers_batch_20260810.csv

    # Or score specific pairs on the CLI:
    python3 analyses/classifiers/nelson_tier_classify.py \\
        --pair UNC13A:ALS --pair NTRK2:AD --out /tmp/tiers.csv

Cost: ~$0.05 per pair with Sonnet. 100 T-I pairs ≈ $5.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import (
    annotate,
    call_with_retry,
    db_conn,
    extract_json_block,
    get_client,
)

PROMPT_VERSION = "v1"
DEFAULT_MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a human-genetics evidence adjudicator using the Nelson 2015 tier framework. You assign a tier (T0 through T4) to a (gene, indication) pair given structured evidence. You are conservative: T3 requires a Mendelian variant with defined phenotype AND matched direction; a "possibly relevant" common variant does not count. You state direction concordance explicitly and cite specific PMIDs."""

USER_TEMPLATE = """(Gene, indication) pair to score:
  Gene: {gene}
  Indication: {indication}

Nelson tier definitions:
- T0 — no reproducible human genetic association
- T1 — GWAS association only (no fine-mapping to the target gene, or direction unclear)
- T2 — replicated common variant fine-mapped to this target with matched effect direction,
       OR replicated GWAS coding variant in this gene
- T3 — Mendelian LoF/GoF with defined phenotype (from OMIM/Orphanet or ClinGen Definitive/Strong),
       with direction concordant to a plausible therapeutic hypothesis
- T4 — Mendelian direct match + drug direction validation in humans

Evidence available (from our DB):

Mendelian associations for {gene}:
{mendelian_ev}

ClinGen validity for {gene}:
{clingen_ev}

GWAS associations for {gene} in indications overlapping "{indication}":
{gwas_ev}

Open Targets genetic score (max for this target): {ot_genetic_max}

Assign tier. Return JSON with:
  gene: string
  indication: string
  tier: "T0" | "T1" | "T2" | "T3" | "T4"
  direction_concordance: "concordant" | "discordant" | "unclear"
  evidence_variants: array of strings (rsID / HGVS / OMIM id)
  supporting_pmids: array of PMIDs
  rationale: 1-3 sentences
  evidence_url: canonical URL (OMIM entry, GWAS Catalog, or ClinGen)
"""


def fetch_mendelian(cur, gene: str) -> str:
    cur.execute(
        """
        SELECT m.disease_name, m.inheritance, m.mim_id
        FROM public.mendelian_associations m
        JOIN public.targets t ON t.id = m.target_id
        WHERE t.symbol = %s
        LIMIT 30
        """,
        (gene,),
    )
    rows = cur.fetchall()
    if not rows:
        return "  (none in DB)"
    return "\n".join(
        f"  {r[0][:80]} | inheritance={r[1] or '?'} | OMIM {r[2] or '?'}"
        for r in rows
    )


def fetch_clingen(cur, gene: str) -> str:
    try:
        cur.execute(
            """
            SELECT c.disease_name, c.classification
            FROM public.clingen_validity c
            JOIN public.targets t ON t.id = c.target_id
            WHERE t.symbol = %s
            LIMIT 20
            """,
            (gene,),
        )
        rows = cur.fetchall()
    except Exception:
        return "  (clingen table not available)"
    if not rows:
        return "  (none in DB)"
    return "\n".join(f"  {r[0][:80]} — {r[1]}" for r in rows)


def fetch_gwas(cur, gene: str, indication: str) -> str:
    like = f"%{indication.split()[0]}%"
    try:
        cur.execute(
            """
            SELECT g.trait, g.p_value, g.rs_id, g.effect_allele
            FROM public.gwas_associations g
            JOIN public.targets t ON t.id = g.target_id
            WHERE t.symbol = %s
              AND g.trait ILIKE %s
            ORDER BY g.p_value ASC
            LIMIT 20
            """,
            (gene, like),
        )
        rows = cur.fetchall()
    except Exception:
        return "  (gwas table query failed)"
    if not rows:
        return "  (no matching-trait GWAS hits in DB)"
    return "\n".join(
        f"  {r[2] or '?'} | {r[0][:60]} | p={r[1]} | effect_allele={r[3] or '?'}"
        for r in rows
    )


def fetch_ot_genetic(cur, gene: str) -> str:
    try:
        cur.execute(
            """
            SELECT te.genetic_score
            FROM public.target_evidence te
            JOIN public.targets t ON t.id = te.target_id
            WHERE t.symbol = %s
            ORDER BY te.genetic_score DESC NULLS LAST
            LIMIT 1
            """,
            (gene,),
        )
        r = cur.fetchone()
        return str(r[0]) if r else "n/a"
    except Exception:
        return "n/a"


def score_one_pair(client, cur, gene: str, indication: str, model: str) -> dict:
    ctx = {
        "gene": gene,
        "indication": indication,
        "mendelian_ev": fetch_mendelian(cur, gene),
        "clingen_ev": fetch_clingen(cur, gene),
        "gwas_ev": fetch_gwas(cur, gene, indication),
        "ot_genetic_max": fetch_ot_genetic(cur, gene),
    }
    user = USER_TEMPLATE.format(**ctx)
    result = call_with_retry(client, model, SYSTEM_PROMPT, user, max_tokens=768)
    row = extract_json_block(result.text)
    row["gene"] = gene
    row["indication"] = indication
    return annotate(row, result, PROMPT_VERSION)


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--pairs", type=Path,
                     help="CSV with columns gene,indication")
    src.add_argument("--pair", action="append",
                     help="GENE:INDICATION, can be passed multiple times")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", type=str, default=DEFAULT_MODEL)
    return ap.parse_args()


def load_pairs(args) -> list[tuple[str, str]]:
    pairs = []
    if args.pairs:
        with args.pairs.open() as fh:
            rdr = csv.DictReader(fh)
            for row in rdr:
                g = (row.get("gene") or "").strip()
                i = (row.get("indication") or "").strip()
                if g and i:
                    pairs.append((g, i))
    elif args.pair:
        for p in args.pair:
            if ":" not in p:
                raise SystemExit(f"--pair '{p}' expects GENE:INDICATION")
            g, i = p.split(":", 1)
            pairs.append((g.strip(), i.strip()))
    return pairs


def main():
    args = parse_args()
    pairs = load_pairs(args)
    if not pairs:
        raise SystemExit("no pairs to score")

    # Resume: skip pairs already in the output CSV
    already = set()
    if args.out.exists():
        with args.out.open() as fh:
            for row in csv.DictReader(fh):
                already.add((row["gene"], row["indication"]))

    todo = [(g, i) for g, i in pairs if (g, i) not in already]
    print(f"Total pairs: {len(pairs)}; already scored: {len(already)}; to do: {len(todo)}")

    conn = db_conn()
    cur = conn.cursor()
    client = get_client()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_header = not args.out.exists()
    with args.out.open("a", newline="") as fh:
        wr = csv.writer(fh)
        if write_header:
            wr.writerow(["gene", "indication", "tier", "direction_concordance",
                         "evidence_variants", "supporting_pmids", "rationale",
                         "evidence_url", "cost_usd", "model", "prompt_version"])

        total_cost = 0.0
        for i, (gene, indication) in enumerate(todo, start=1):
            try:
                row = score_one_pair(client, cur, gene, indication, args.model)
            except Exception as e:
                print(f"  [{i}] {gene}:{indication} FAILED: {e}", file=sys.stderr, flush=True)
                continue
            wr.writerow([
                row["gene"], row["indication"], row.get("tier", ""),
                row.get("direction_concordance", ""),
                "|".join(row.get("evidence_variants", []) or []),
                "|".join(str(x) for x in (row.get("supporting_pmids") or [])),
                (row.get("rationale") or "")[:500],
                row.get("evidence_url", ""),
                row.get("_cost_usd", 0.0),
                row.get("_model", ""),
                row.get("_prompt_version", ""),
            ])
            fh.flush()
            total_cost += row.get("_cost_usd", 0.0)
            print(
                f"  [{i}/{len(todo)}] {gene:<12s} × {indication[:32]:<32s} "
                f"→ {row.get('tier','?')} "
                f"({row.get('direction_concordance','?')}) "
                f"cost=${row.get('_cost_usd',0):.4f} cum=${total_cost:.2f}",
                flush=True,
            )

    conn.close()
    print(f"\nDone. Wrote to {args.out}. Total cost: ${total_cost:.4f}")


if __name__ == "__main__":
    main()
