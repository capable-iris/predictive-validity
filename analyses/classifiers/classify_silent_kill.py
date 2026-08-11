"""Verify whether a drug that reached Phase 3 without an approval is a real
"silent kill" (efficacy fail) or an alive-but-slow program.

Reads drugs from `preclin.drug` where the associated programs reached Phase 3
but never appear in `preclin.approval`. Queries the LLM with all known trial
metadata (title, phase, why_stopped, dates) plus optional press-release
context (if a companion cache is supplied) and returns a category.

Writes JSONL matching the schema `db/02_ingest.py` reads at
`data/silent_kill_verified.jsonl` (BASE / silent_kill_verified.jsonl).

Output row schema:
    {
      "drug_key": str,        # normalized drug name
      "cat": "efficacy_fail" | "safety_fail" | "commercial_fail" |
             "alive_no_approval" | "approved_ex_us" | "unclear",
      "confidence": "high" | "medium" | "low",
      "evidence": str,        # supporting text
      "_cost_usd": float,
      ...
    }

Cost: ~$0.05-0.15 per drug (Sonnet, longer context). 500 drugs ≈ $50-75.
"""
from __future__ import annotations

import argparse
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
DEFAULT_MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are verifying whether a specific drug program that reached Phase 3 was actually killed for efficacy, killed for safety, or is still alive. You are careful about the distinction between "approved somewhere outside the US" and "silently discontinued." When context is ambiguous, assign 'unclear' rather than guessing."""

USER_TEMPLATE = """Drug: {drug_name}
Modality: {modality}
Primary target(s): {targets}
Sponsor(s) history: {sponsors}
Indications targeted: {indications}

Trial history (chronological):
{trial_history}

External evidence (if supplied):
{external_evidence}

The drug reached Phase {highest_phase} but no US FDA approval is on record in our snapshot.
Classify the program's real state:

- **efficacy_fail** — a Phase 3 trial missed its endpoint or the sponsor discontinued citing lack of benefit
- **safety_fail** — discontinued for on-target or off-target tox at Phase 2/3
- **commercial_fail** — discontinued for clear non-scientific reasons (company bankruptcy, licensing withdrawal, IP loss, strategic exit from therapeutic area)
- **approved_ex_us** — approved by EMA / PMDA / NMPA / other non-US agency, not FDA. Common for older Japanese and Chinese assets.
- **alive_no_approval** — still in active clinical dev (Phase 3 ongoing, Phase 4 conducting)
- **unclear** — insufficient evidence

Return JSON:
  cat: label
  confidence: "high" | "medium" | "low"
  evidence: 1-3 sentences citing the trial ID(s) and any external source
"""


DRUGS_SQL = """
    SELECT d.drug_id, d.display_name AS drug_name, d.modality,
           array_to_string(ARRAY(
             SELECT t.symbol FROM preclin.drug_target dt
             JOIN public.targets t ON t.id = dt.target_id
             WHERE dt.drug_id = d.drug_id
             LIMIT 5
           ), ', ') AS targets,
           array_to_string(ARRAY(
             SELECT DISTINCT p.sponsor_name FROM preclin.program p
             WHERE p.drug_id = d.drug_id LIMIT 5
           ), ', ') AS sponsors,
           array_to_string(ARRAY(
             SELECT DISTINCT i.display_name FROM preclin.program p
             JOIN preclin.indication i ON i.indication_id = p.indication_id
             WHERE p.drug_id = d.drug_id LIMIT 10
           ), '; ') AS indications,
           MAX(po.highest_phase) AS highest_phase,
           d.normalized_name AS drug_key
    FROM preclin.drug d
    JOIN preclin.program p ON p.drug_id = d.drug_id
    JOIN preclin.program_outcome po ON po.program_id = p.program_id
    WHERE po.highest_phase >= 3
      AND d.is_placebo = FALSE
      AND NOT EXISTS (
        SELECT 1 FROM preclin.approval a WHERE a.drug_id = d.drug_id
      )
    GROUP BY d.drug_id, d.display_name, d.modality, d.normalized_name
    ORDER BY d.drug_id
    LIMIT %s
"""


TRIAL_HISTORY_SQL = """
    SELECT t.nct_id, t.phase, t.status,
           COALESCE(t.brief_title, '') AS title,
           COALESCE(t.why_stopped, '') AS why_stopped,
           t.start_date::text AS start_date,
           t.completion_date::text AS completion_date
    FROM preclin.program p
    JOIN preclin.program_trial pt ON pt.program_id = p.program_id
    JOIN public.trials t ON t.nct_id = pt.nct_id
    WHERE p.drug_id = %s
    ORDER BY t.start_date NULLS LAST, t.nct_id
"""


def format_trial_history(rows) -> str:
    lines = []
    for r in rows:
        wh = f' — why_stopped: "{r[4][:200]}"' if r[4] else ""
        lines.append(f"  {r[0]}  {r[1]}  {r[2]}  start={r[5]} end={r[6]}{wh}")
    return "\n".join(lines[:40]) if lines else "  (no trials found)"


def load_external_evidence(cache_dir: Path, drug_key: str) -> str:
    """Optional: load pre-scraped press-release / news snippets from a cache dir."""
    if not cache_dir:
        return ""
    f = cache_dir / f"{drug_key}.txt"
    if not f.exists():
        return ""
    return f.read_text()[:4000]


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--evidence-cache-dir", type=Path, default=None,
                    help="Directory of per-drug press-release cache files")
    ap.add_argument("--limit", type=int, default=5000)
    ap.add_argument("--model", type=str, default=DEFAULT_MODEL)
    return ap.parse_args()


def main():
    args = parse_args()
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(DRUGS_SQL, (args.limit,))
    drugs = cur.fetchall()
    print(f"Candidate drugs (Ph3+ without US approval): {len(drugs)}")

    already = load_processed_keys(args.out, "drug_key")
    print(f"Already classified: {len(already)}")

    client = get_client()
    total_cost = 0.0
    idx = 0
    for row in drugs:
        drug_id, drug_name, modality, targets, sponsors, indications, highest_phase, drug_key = row
        if drug_key in already:
            continue
        idx += 1

        cur.execute(TRIAL_HISTORY_SQL, (drug_id,))
        trials = cur.fetchall()

        ctx = {
            "drug_name": drug_name or "",
            "modality": modality or "",
            "targets": targets or "",
            "sponsors": sponsors or "",
            "indications": indications or "",
            "trial_history": format_trial_history(trials),
            "external_evidence": load_external_evidence(args.evidence_cache_dir, drug_key) or "  (none supplied)",
            "highest_phase": highest_phase,
        }
        user = USER_TEMPLATE.format(**ctx)
        try:
            result = call_with_retry(client, args.model, SYSTEM_PROMPT, user, max_tokens=768)
            parsed = extract_json_block(result.text)
        except Exception as e:
            print(f"  [{idx}] {drug_key} FAILED: {e}", file=sys.stderr, flush=True)
            continue
        parsed["drug_key"] = drug_key
        annotate(parsed, result, PROMPT_VERSION)
        append_jsonl(args.out, parsed)
        total_cost += result.cost_usd
        print(
            f"  [{idx}] {drug_key[:24]:<24s} phase{highest_phase} "
            f"→ cat={parsed.get('cat','?'):<20s} conf={parsed.get('confidence','?'):<6s} "
            f"cost=${result.cost_usd:.4f} cum=${total_cost:.2f}",
            flush=True,
        )

    conn.close()
    print(f"\nDone. Total cost: ${total_cost:.4f}")


if __name__ == "__main__":
    main()
