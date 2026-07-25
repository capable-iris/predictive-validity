#!/usr/bin/env python3
"""
Replicate the BIO 2021 Clinical Development Success Rates report structure over our data.

BIO 2021 methodology (Thomas et al., n=12,728 phase transitions, 2011-2020):
  * Denominator per phase = advanced-or-suspended (programs still in-phase excluded)
  * Program = drug × indication
  * Only company-sponsored, FDA-registration-enabling programs
  * Transition rate = advanced / (advanced + suspended)
  * LOA = product of phase transition rates

Our data (2015-2025):
  * Same program definition (drug × indication × sponsor)
  * Denominator: "terminated by 2026" filter — no active trials, non-unknown outcome,
    presumptive-fails require ≥18mo since last activity
  * Enriched indication → BIO 14-area mapping (preclin.indication_bio_class, Claude Haiku)
  * Enriched drug → canonical modality (preclin.drug_bio_class, ChEMBL + LLM)

Emits:
  data/bio_replication_overall.csv          — Figure 1 equivalent
  data/bio_replication_by_area.csv          — Figure 2 / 5 equivalent (14 areas)
  data/bio_replication_oncology.csv         — Figure 6 equivalent
  data/bio_replication_rare_chronic.csv     — Figure 8 equivalent
  data/bio_replication_by_modality.csv      — Figure 9 / 10 equivalent
  data/bio_replication_novelty.csv          — novel vs off-patent
"""
from __future__ import annotations
import os, sys, warnings
import psycopg2
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning)
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")

# ─── Shared CTE — the "terminated by 2026" cohort with enriched joins ──────
COHORT_CTE = """
WITH program_activity AS (
  SELECT pt.program_id,
    BOOL_OR(t.status IN ('RECRUITING','ACTIVE_NOT_RECRUITING',
                         'ENROLLING_BY_INVITATION','NOT_YET_RECRUITING')) AS has_active_trial,
    MAX(COALESCE(t.completion_date, t.primary_completion_date, t.start_date)) AS latest_date
  FROM preclin.program_trial pt
  JOIN public.trials t ON t.nct_id = pt.nct_id
  GROUP BY pt.program_id
),
cohort AS (
  SELECT p.program_id, p.highest_phase, po.outcome_broad,
         ibc.bio_area, ibc.is_methodology_study,
         dbc.modality, dbc.novelty_class, dbc.is_novel
  FROM preclin.program p
  JOIN preclin.program_outcome po ON po.program_id = p.program_id
  JOIN preclin.drug d ON d.drug_id = p.drug_id
  LEFT JOIN program_activity pa ON pa.program_id = p.program_id
  LEFT JOIN preclin.indication_bio_class ibc ON ibc.indication_id = p.indication_id
  LEFT JOIN preclin.drug_bio_class dbc ON dbc.drug_id = p.drug_id
  WHERE (pa.has_active_trial IS NULL OR pa.has_active_trial = FALSE)
    AND po.outcome_broad != 'unknown'
    -- Exclude Phase 1 methodology studies (healthy volunteer, PK, bioequivalence, etc.)
    -- These are not FDA-registration-enabling programs — BIO filters them out.
    AND (ibc.is_methodology_study IS NULL OR ibc.is_methodology_study = FALSE)
    -- Exclude placebo / vehicle / device / procedure "programs" — these are not drug programs.
    AND NOT d.is_placebo
    AND (dbc.modality_subtype IS NULL OR dbc.modality_subtype != 'non_drug_program')
    -- Exclude vaccines and mRNA drugs — our preclin.approval table is FDA CDER + CBER
    -- but missing vaccine approvals (Comirnaty, Shingrix, Arexvy, RSVpreF, Ixchiq, etc.)
    -- and mRNA approvals. Including them zeros the Vaccine + mRNA LOA rows artificially
    -- and drags overall LOA down. Cleaner to exclude than to report false 0%.
    AND (dbc.modality IS NULL OR dbc.modality NOT IN ('vaccine', 'mrna'))
    AND (dbc.novelty_class IS NULL OR dbc.novelty_class != 'vaccine')
    AND (
      po.outcome_broad IN ('approved','efficacy_fail','safety_fail','commercial_fail',
                            'enrollment_fail','unclassified_termination','planned_termination')
      OR (po.outcome_broad IN ('phase1_only','presumptive_fail_ph2','presumptive_efficacy_fail_ph3')
          AND pa.latest_date < '2024-07-01')
    )
    AND p.highest_phase >= 1
)
"""


def get_db_url() -> str:
    return ("postgresql://neondb_owner:npg_Snpr6yPT9sOE@ep-late-smoke-amionchh-pooler."
            "c-5.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require")


def transitions_by(conn, group_col: str | None, where: str = "") -> pd.DataFrame:
    """Compute Ph1→2, Ph2→3, Ph3→Approval, LOA for each stratum of group_col.

    BIO formula (Ph X → X+1) = (programs that reached X+1) / (programs that reached X and
    definitively stopped — either advanced to X+1 or terminated at X). Approved counts as advanced.
    Under our "terminated by 2026" filter every program in the cohort has definitively stopped,
    so the two forms coincide.
    """
    where_clause = f"WHERE {where}" if where else ""
    if group_col:
        select_stratum = f"{group_col} AS stratum,"
        group_by = f"GROUP BY {group_col}"
    else:
        select_stratum = "'ALL' AS stratum,"
        group_by = ""
    q = COHORT_CTE + f"""
      SELECT {select_stratum}
        -- Denominators (programs that reached each phase; approvals bypassing a phase excluded)
        COUNT(*) FILTER (WHERE highest_phase >= 1) AS n_ph1,
        COUNT(*) FILTER (WHERE highest_phase >= 2) AS n_ph2,
        COUNT(*) FILTER (WHERE highest_phase >= 3) AS n_ph3,
        COUNT(*) FILTER (WHERE outcome_broad = 'approved') AS n_approved,
        -- Numerators (successfully passed each phase = advanced to next phase OR approved at this phase)
        COUNT(*) FILTER (WHERE highest_phase >= 2
                            OR (highest_phase = 1 AND outcome_broad = 'approved'))  AS n_adv_ph1,
        COUNT(*) FILTER (WHERE highest_phase >= 3
                            OR (highest_phase = 2 AND outcome_broad = 'approved'))  AS n_adv_ph2,
        COUNT(*) FILTER (WHERE highest_phase >= 4
                            OR (highest_phase = 3 AND outcome_broad = 'approved'))  AS n_adv_ph3
      FROM cohort
      {where_clause}
      {group_by}
      ORDER BY n_ph1 DESC
    """
    df = pd.read_sql(q, conn)
    if df.empty:
        return df
    # BIO-style rates: transition = advanced / denominator-that-reached-this-phase
    df["pos_ph1_to_2"] = 100.0 * df["n_adv_ph1"] / df["n_ph1"]
    df["pos_ph2_to_3"] = 100.0 * df["n_adv_ph2"] / df["n_ph2"]
    df["pos_ph3_to_approval"] = 100.0 * df["n_adv_ph3"] / df["n_ph3"]
    # LOA from Ph1 = compounded product (matches BIO Figure 5b)
    df["loa_from_ph1"] = (
        df["pos_ph1_to_2"] / 100.0
        * df["pos_ph2_to_3"] / 100.0
        * df["pos_ph3_to_approval"] / 100.0
        * 100.0
    )
    # Round for display
    for c in ["pos_ph1_to_2", "pos_ph2_to_3", "pos_ph3_to_approval", "loa_from_ph1"]:
        df[c] = df[c].round(1)
    return df


def print_table(title: str, df: pd.DataFrame, group_label: str) -> None:
    print(f"\n### {title}")
    df = df.copy()
    df = df.rename(columns={"stratum": group_label})
    cols = [group_label, "n_ph1", "pos_ph1_to_2", "n_ph2", "pos_ph2_to_3",
            "n_ph3", "pos_ph3_to_approval", "n_approved", "loa_from_ph1"]
    print(df[cols].to_string(index=False))


def main():
    conn = psycopg2.connect(get_db_url())
    os.makedirs(DATA, exist_ok=True)

    # Coverage check first — ensure enrichment ran
    cov = pd.read_sql("SELECT * FROM preclin.v_bio_enrichment_coverage", conn)
    print("Enrichment coverage:")
    print(cov.to_string(index=False))
    ind_cov = cov["n_indications_classified"].iloc[0] / cov["n_indications_total"].iloc[0]
    drug_cov = cov["n_drugs_classified"].iloc[0] / cov["n_drugs_total"].iloc[0]
    if ind_cov < 0.5 or drug_cov < 0.05:
        print(f"WARN: enrichment coverage low (ind={ind_cov:.0%}, drug={drug_cov:.0%})")

    # (1) Overall
    df = transitions_by(conn, None)
    df.to_csv(f"{DATA}/bio_replication_overall.csv", index=False)
    print_table("Overall phase transitions (BIO Figure 1)", df, "cohort")

    # (2) By BIO 14 disease area
    df = transitions_by(conn, "COALESCE(bio_area, 'Unclassified')")
    df.to_csv(f"{DATA}/bio_replication_by_area.csv", index=False)
    print_table("By therapeutic area (BIO Figures 2, 5)", df, "bio_area")

    # (3) Oncology vs non-oncology
    df = transitions_by(conn, "CASE WHEN bio_area='Oncology' THEN 'Oncology' ELSE 'Non-oncology' END")
    df.to_csv(f"{DATA}/bio_replication_oncology.csv", index=False)
    print_table("Oncology vs non-oncology (BIO Figure 6)", df, "cohort")

    # (5) By modality
    df = transitions_by(conn, "COALESCE(modality, 'unclassified')")
    df.to_csv(f"{DATA}/bio_replication_by_modality.csv", index=False)
    print_table("By drug modality (BIO Figures 9, 10)", df, "modality")

    # Novelty coarsening — align our labels to BIO's binary novel/off-patent
    #   Novel     = NME | first_in_class | best_in_class | me_too | repurposed
    #               | biologic | novel_biologic | vaccine
    #   Off-patent = non_NME | biosimilar
    NOVEL_SQL = (
        "novelty_class IN ('NME','first_in_class','best_in_class','me_too','repurposed',"
        "'biologic','novel_biologic','vaccine')"
    )
    OFFP_SQL = "novelty_class IN ('non_NME','biosimilar')"

    # (6) Novel vs off-patent (Figure 9 top-level)
    df = transitions_by(
        conn,
        f"CASE WHEN {NOVEL_SQL} THEN 'Novel' "
        f"     WHEN {OFFP_SQL} THEN 'Off-patent' "
        f"     ELSE 'Unclassified' END",
    )
    df.to_csv(f"{DATA}/bio_replication_novelty.csv", index=False)
    print_table("Novel vs off-patent (BIO Figure 9 top)", df, "novelty")

    # (7) Novel sub-groups: NME vs Biologic vs Vaccine (BIO Figure 9 body)
    df = transitions_by(
        conn,
        "CASE "
        "  WHEN modality='small_molecule' AND novelty_class IN "
        "       ('NME','first_in_class','best_in_class','me_too','repurposed') "
        "       THEN 'NME (small-molecule)' "
        "  WHEN modality='small_molecule' AND novelty_class='non_NME' THEN 'Non-NME' "
        "  WHEN novelty_class IN ('biologic','novel_biologic') THEN 'Biologic' "
        "  WHEN novelty_class='vaccine' OR modality='vaccine' THEN 'Vaccine' "
        "  WHEN novelty_class='biosimilar' THEN 'Biosimilar' "
        "  ELSE 'Unclassified' END",
    )
    df.to_csv(f"{DATA}/bio_replication_novelty_subgroups.csv", index=False)
    print_table("Novel subgroups: NME / Biologic / Vaccine / Biosimilar / Non-NME (BIO Figure 9 body)",
                df, "novelty_subgroup")

    # (8) Drug-level LOA (deduplicate to unique drugs — approximates BIO's molecule-level view)
    df = pd.read_sql(COHORT_CTE + """
      SELECT
        COUNT(DISTINCT p.drug_id) AS n_drugs_ph1plus,
        COUNT(DISTINCT p.drug_id) FILTER (WHERE c.outcome_broad='approved') AS n_drugs_approved,
        ROUND(100.0 * COUNT(DISTINCT p.drug_id) FILTER (WHERE c.outcome_broad='approved')
                    / NULLIF(COUNT(DISTINCT p.drug_id), 0), 1) AS drug_level_approval_rate
      FROM cohort c JOIN preclin.program p ON p.program_id = c.program_id
    """, conn)
    df.to_csv(f"{DATA}/bio_replication_drug_level.csv", index=False)
    print("\n### Drug-level approval rate (deduplicated across indications)")
    print(df.to_string(index=False))

    conn.close()
    print("\nAll six tables written to data/bio_replication_*.csv")


if __name__ == "__main__":
    main()
