"""Classify why an industry-sponsored clinical trial was terminated.

Two modes:

- **First-pass** (default): read all terminated Ph1+ trials from public.trials,
  classify each with the model into one of six categories.

- **Verify** (`--verify-from PATH`): read a prior first-pass output, filter
  to labels that need a second look (default: commercial_strategic + unclear
  at less-than-high confidence), and re-classify with the current model,
  showing the prior classification as context.

The model is a plain `--model` argument. Verify mode defaults to Sonnet;
first-pass mode defaults to Haiku. Override either explicitly.

Output row schema (per `db/02_ingest.py:ingest_classifications`):
    {
      "nct_id": str,
      "cat": one of {"efficacy","safety","commercial_strategic",
                     "enrollment_fail","other","unclear"},
      "confidence": "high"|"medium"|"low",
      "rationale": str,
      # verify-mode only:
      "agrees_with_prior": bool,
      "reclassification_reason": str,
      # audit trail (added by wrapper, not the LLM):
      "_input_tokens": int,
      "_output_tokens": int,
      "_cost_usd": float,
      "_model": str,
      "_prompt_version": str,
    }

Usage:
    # First-pass over every terminated Ph1+ trial
    python3 analyses/classifiers/classify_why_stopped.py \\
        --out data/clinical_trials/why_stopped_haiku.jsonl

    # Verify Haiku's uncertain labels with Sonnet
    python3 analyses/classifiers/classify_why_stopped.py \\
        --verify-from data/clinical_trials/why_stopped_haiku.jsonl \\
        --out data/clinical_trials/why_stopped_sonnet.jsonl

    # Verify a different subset (e.g. everything Haiku labeled safety at low confidence)
    python3 analyses/classifiers/classify_why_stopped.py \\
        --verify-from data/clinical_trials/why_stopped_haiku.jsonl \\
        --targets safety --confidence-below high \\
        --out /tmp/why_stopped_safety_verified.jsonl

Cost: ~$0.005-0.01 per trial with Haiku (5,000 trials ≈ $25-50);
      ~$0.02-0.04 per trial with Sonnet.
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
    read_jsonl,
)

PROMPT_VERSION = "v1"
HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-6"

# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------

FIRST_PASS_SYSTEM = """You classify why an industry-sponsored clinical trial was terminated. Categories are exhaustive and mutually exclusive. Sponsors euphemize — a "strategic reconsideration of the portfolio" often masks efficacy failure. Be conservative: prefer "unclear" over over-confident guessing."""

FIRST_PASS_USER = """Trial: {nct_id}
Phase: {phase}
Condition(s): {conditions}
Intervention: {intervention}
Overall status: {status}
Start / completion: {start_date} / {completion_date}
Enrollment (actual / target): {enrollment}
Sponsor: {sponsor}

why_stopped free-text (verbatim from CT.gov):
"{why_stopped}"

Classify the termination reason into ONE category:

- **efficacy** — trial stopped because primary/secondary endpoint was missed OR interim analysis showed futility. Includes "insufficient effect size", "not clinically meaningful", "did not meet endpoint".
- **safety** — trial stopped because of adverse events, tolerability, or a mechanism-based tox signal. Includes DSMB-recommended stops for safety.
- **commercial_strategic** — trial stopped because of portfolio decision, company acquisition, licensing change, IP dispute, funding withdrawal, or strategic pipeline reprioritization. This bucket contains ~60% disguised efficacy failures per Cook 2014; assign confidence accordingly.
- **enrollment_fail** — trial stopped because it could not recruit enough patients (slow accrual, competing trials, protocol issues that limited eligibility).
- **other** — administrative reasons (change of investigator, protocol amendment cascaded to closure), or reasons that don't fit above.
- **unclear** — the free-text is empty, generic ("study terminated"), or does not permit confident categorization.

Return a JSON object with:
  cat: one of the labels above
  confidence: "high" | "medium" | "low"
  rationale: one short sentence explaining the classification, quoting from the why_stopped text where possible
"""


VERIFY_SYSTEM = """You are re-verifying a first-pass classification of a clinical trial's termination reason. Prior classifier used a weaker model. Your job: independently classify from the primary evidence (why_stopped text + metadata) and flag any disagreement with the prior labels. Sponsors euphemize — assume "portfolio decision" often masks efficacy failure unless the evidence positively excludes efficacy."""

VERIFY_USER = """Trial: {nct_id}
Phase: {phase}
Condition(s): {conditions}
Intervention: {intervention}
Sponsor: {sponsor}

why_stopped text (verbatim):
"{why_stopped}"

Prior classification: cat={prior_cat}, confidence={prior_confidence}
Prior rationale: {prior_rationale}

Classify independently into ONE of:
- efficacy
- safety
- commercial_strategic
- enrollment_fail
- other
- unclear

For "commercial_strategic": specifically consider whether the language ("strategic reconsideration", "portfolio decision", "sponsor decision") is the sponsor's euphemism for a hidden efficacy fail. If the evidence supports a disguised efficacy fail, use "efficacy" and note the reasoning.

Return JSON:
  cat: label
  confidence: "high" | "medium" | "low"
  rationale: 1-2 sentences
  agrees_with_prior: bool
  reclassification_reason: string (empty if you agree)
"""


# --------------------------------------------------------------------------
# DB queries
# --------------------------------------------------------------------------

TRIALS_SQL = """
    SELECT t.nct_id,
           t.phase,
           COALESCE(t.status, '') AS status,
           COALESCE(t.why_stopped, '') AS why_stopped,
           COALESCE(t.brief_title, '') AS title,
           COALESCE(t.conditions, '') AS conditions,
           COALESCE(t.interventions, '') AS intervention,
           COALESCE(t.sponsor_name, '') AS sponsor,
           t.start_date::text AS start_date,
           t.completion_date::text AS completion_date,
           COALESCE(t.enrollment_actual::text, '') || ' / ' ||
             COALESCE(t.enrollment_target::text, '')  AS enrollment
    FROM public.trials t
    WHERE t.why_stopped IS NOT NULL
      AND t.why_stopped != ''
      AND t.status IN ('TERMINATED','WITHDRAWN','SUSPENDED')
      AND t.phase IN ('Phase 1','Phase 1/Phase 2','Phase 2','Phase 2/Phase 3','Phase 3','Phase 4')
    ORDER BY t.nct_id
    LIMIT %s
"""


def fetch_trials(cur, limit: int) -> list[dict]:
    cur.execute(TRIALS_SQL, (limit,))
    return [dict(zip((
        "nct_id","phase","status","why_stopped","title","conditions",
        "intervention","sponsor","start_date","completion_date","enrollment"
    ), r)) for r in cur.fetchall()]


def fetch_trial_meta(cur, nct_ids: list[str]) -> dict:
    if not nct_ids:
        return {}
    cur.execute(
        """
        SELECT nct_id, phase, COALESCE(why_stopped,'') AS why_stopped,
               COALESCE(conditions,'') AS conditions,
               COALESCE(interventions,'') AS intervention,
               COALESCE(sponsor_name,'') AS sponsor
        FROM public.trials
        WHERE nct_id = ANY(%s)
        """,
        (list(nct_ids),),
    )
    return {
        r[0]: dict(zip(("nct_id","phase","why_stopped","conditions","intervention","sponsor"), r))
        for r in cur.fetchall()
    }


# --------------------------------------------------------------------------
# Classification helpers
# --------------------------------------------------------------------------

def confidence_rank(c: str) -> int:
    return {"low": 0, "medium": 1, "high": 2}.get(c, 0)


def classify_first_pass(client, trial: dict, model: str) -> dict:
    user = FIRST_PASS_USER.format(**trial)
    result = call_with_retry(client, model, FIRST_PASS_SYSTEM, user, max_tokens=512)
    row = extract_json_block(result.text)
    row["nct_id"] = trial["nct_id"]
    return annotate(row, result, PROMPT_VERSION)


def classify_verify(client, trial: dict, prior: dict, model: str) -> dict:
    ctx = {
        **trial,
        "prior_cat": prior.get("cat", "unknown"),
        "prior_confidence": prior.get("confidence", "unknown"),
        "prior_rationale": prior.get("rationale", ""),
    }
    user = VERIFY_USER.format(**ctx)
    result = call_with_retry(client, model, VERIFY_SYSTEM, user, max_tokens=512)
    row = extract_json_block(result.text)
    row["nct_id"] = trial["nct_id"]
    return annotate(row, result, PROMPT_VERSION)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_args():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", type=str, default=None,
                    help="Model to use. Defaults to Haiku for first-pass, "
                         "Sonnet when --verify-from is set.")
    ap.add_argument("--limit", type=int, default=10000)
    ap.add_argument("--verify-from", type=Path, default=None,
                    help="Prior first-pass JSONL. When set, script runs in verify mode.")
    ap.add_argument("--targets", type=str, default="commercial_strategic,unclear",
                    help="Comma-separated cats to re-verify (verify mode only)")
    ap.add_argument("--confidence-below", type=str, default="high",
                    choices=["high", "medium", "low"],
                    help="Only re-verify when prior confidence is below this (verify mode only)")
    args = ap.parse_args()
    if args.model is None:
        args.model = SONNET_MODEL if args.verify_from else HAIKU_MODEL
    return args


def run_first_pass(args) -> None:
    conn = db_conn()
    cur = conn.cursor()
    trials = fetch_trials(cur, args.limit)
    conn.close()
    print(f"Candidate trials: {len(trials)}")

    already = load_processed_keys(args.out, "nct_id")
    todo = [t for t in trials if t["nct_id"] not in already]
    print(f"Already classified: {len(already)}; to do: {len(todo)}")

    client = get_client()
    total_cost = 0.0
    for i, t in enumerate(todo, start=1):
        try:
            row = classify_first_pass(client, t, args.model)
        except Exception as e:
            print(f"  [{i}] {t['nct_id']} FAILED: {e}", file=sys.stderr, flush=True)
            continue
        append_jsonl(args.out, row)
        total_cost += row["_cost_usd"]
        print(
            f"  [{i}/{len(todo)}] {row['nct_id']:<12s} "
            f"cat={row.get('cat','?'):<20s} conf={row.get('confidence','?'):<7s} "
            f"cost=${row['_cost_usd']:.4f} cum=${total_cost:.2f}",
            flush=True,
        )
    print(f"\nDone. Total cost: ${total_cost:.4f}")


def run_verify(args) -> None:
    targets_set = {t.strip() for t in args.targets.split(",") if t.strip()}
    max_conf = confidence_rank(args.confidence_below)

    to_verify = []
    for row in read_jsonl(args.verify_from):
        if row.get("cat") not in targets_set:
            continue
        if confidence_rank(row.get("confidence", "low")) >= max_conf:
            continue
        to_verify.append(row)
    to_verify = to_verify[: args.limit]
    print(f"Prior rows meeting verify filter: {len(to_verify)}")

    conn = db_conn()
    cur = conn.cursor()
    trial_meta = fetch_trial_meta(cur, [r["nct_id"] for r in to_verify])
    conn.close()

    already = load_processed_keys(args.out, "nct_id")
    todo = [r for r in to_verify if r["nct_id"] not in already and r["nct_id"] in trial_meta]
    print(f"Already re-classified: {len(already)}; to do: {len(todo)}")

    client = get_client()
    total_cost = 0.0
    for i, prior in enumerate(todo, start=1):
        nct = prior["nct_id"]
        try:
            row = classify_verify(client, trial_meta[nct], prior, args.model)
        except Exception as e:
            print(f"  [{i}] {nct} FAILED: {e}", file=sys.stderr, flush=True)
            continue
        append_jsonl(args.out, row)
        total_cost += row["_cost_usd"]
        agrees = "agree" if row.get("agrees_with_prior") else "DISAGREE"
        print(
            f"  [{i}/{len(todo)}] {nct:<12s} prior={prior['cat']:<20s} "
            f"→ new={row.get('cat','?'):<20s} conf={row.get('confidence','?'):<6s} "
            f"{agrees}  cost=${row['_cost_usd']:.4f}  cum=${total_cost:.2f}",
            flush=True,
        )
    print(f"\nDone. Total cost: ${total_cost:.4f}")


def main():
    args = parse_args()
    if args.verify_from:
        run_verify(args)
    else:
        run_first_pass(args)


if __name__ == "__main__":
    main()
