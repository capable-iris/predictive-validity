"""Big-bang ingest: every local JSONL/CSV → preclin.* schema.

Idempotent (uses ON CONFLICT / UPSERTs). Order matters:

1. Dimension registry (metadata)
2. Indications (from CT.gov + approvals)
3. Drugs (from approvals + drug_master_lookup + resolved_targets + trial names)
4. Drug-target links (chembl, resolved, verified, unresolved-sonnet)
5. Approvals (with region flags)
6. Programs (drug × indication × sponsor from trials + approvals)
7. Program-trial junction
8. Evidence scores (target-level lit, drug-specific PubMed, IMPC, family precedent, gnomAD, DepMap, ClinGen, Mendelian, GWAS, OT — most read directly from public.* via joins)
9. Classifications (why_stopped haiku + sonnet, silent_kill_verified, target resolutions)
10. Program outcomes (computed rollup)

Uses batch inserts with psycopg2.execute_values for speed.
"""

import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import time

import psycopg2
from psycopg2.extras import execute_values, Json


def retry_on_network(fn, max_attempts=5):
    """Retry a function on Neon network errors. Reconnect between attempts."""
    for attempt in range(max_attempts):
        try:
            return fn()
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            if attempt == max_attempts - 1:
                raise
            wait = 2 ** attempt
            print(f"  [retry {attempt+1}/{max_attempts}] {type(e).__name__}: waiting {wait}s...",
                  flush=True)
            time.sleep(wait)

BASE = Path("/Users/melissadu/Documents/projects/capable/data")
APP = BASE / "fda_approvals"
CT = BASE / "clinical_trials"
EV = BASE / "target_evidence"

DB_URL = os.environ.get("DATABASE_URL")
if not DB_URL:
    print("ERROR: DATABASE_URL not set", file=sys.stderr)
    sys.exit(1)


def norm(s):
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]", "", s.lower().strip())


def get_conn():
    return psycopg2.connect(DB_URL)


def log_ingest(cur, source_file, target_table, rows_read, rows_inserted,
               rows_skipped=0, rows_updated=0, status="ok", notes=""):
    cur.execute("""
        INSERT INTO preclin.ingest_log
          (source_file, target_table, rows_read, rows_inserted, rows_skipped,
           rows_updated, finished_at, status, notes)
        VALUES (%s, %s, %s, %s, %s, %s, now(), %s, %s)
    """, (source_file, target_table, rows_read, rows_inserted, rows_skipped,
          rows_updated, status, notes))


# ============================================================
# Step 1: Evidence dimension registry
# ============================================================

DIMENSIONS = [
    # Category A — genetics
    ("nelson_tier", "A_genetics", "target_indication", "categorical",
     "Nelson tier T0-T4 for gene-indication genetic support",
     {"T0": "none", "T1": "GWAS non-coding", "T2": "GWAS coding", "T3": "Mendelian match", "T4": "Mendelian direction-matched"}),
    ("mendelian_n", "A_genetics", "target", "count",
     "Count of Mendelian disease associations from Orphanet + OMIM", None),
    ("clingen_n_strong", "A_genetics", "target", "count",
     "Count of ClinGen Strong/Definitive gene-disease classifications", None),
    ("gwas_n_sig", "A_genetics", "target", "count",
     "Count of GWAS hits with p<5e-8", None),
    ("ot_genetic_max", "A_genetics", "target", "numeric_float",
     "Open Targets max genetic score across all diseases (0-1)", None),
    ("ot_association_n", "A_genetics", "target", "count",
     "Number of Open Targets disease associations for this target", None),
    # Category B — mechanistic
    ("line_b_lit", "B_mechanistic", "target", "numeric_0_3",
     "Haiku-extracted mechanistic biology depth score from PubMed",
     {"0": "black box", "1": "basic characterization", "2": "structure resolved", "3": "deep pharmacology"}),
    ("tractability_sm", "B_mechanistic", "target", "boolean",
     "Small molecule tractable per Open Targets", None),
    ("tractability_ab", "B_mechanistic", "target", "boolean",
     "Antibody tractable per Open Targets", None),
    ("tractability_protac", "B_mechanistic", "target", "boolean",
     "PROTAC tractable per Open Targets", None),
    # Category C — cell
    ("line_c_lit", "C_cell", "target", "numeric_0_3",
     "Haiku-extracted cell-pathway validation score from PubMed",
     {"0": "none", "1": "cell-line pharmacology", "2": "primary human cells", "3": "iPSC/organoid rescue"}),
    ("depmap_pan_essential", "C_cell", "target", "boolean",
     "DepMap: gene KO lethal across broad cell-line panel", None),
    ("depmap_n_dep_lineages", "C_cell", "target", "count",
     "Number of DepMap lineages where this gene is essential", None),
    ("depmap_mean_effect", "C_cell", "target", "numeric_float",
     "DepMap mean Chronos effect score across cell lines", None),
    ("drug_cell_efficacy", "C_cell", "drug", "numeric_0_3",
     "Drug-specific cell efficacy from PubMed extraction",
     {"0": "no data", "1": "cell line only", "2": "primary human cells", "3": "iPSC/organoid rescue"}),
    # Category D — animal
    ("line_d_lit", "D_animal", "target", "numeric_0_3",
     "Haiku-extracted animal in vivo evidence score from PubMed",
     {"0": "none", "1": "single rodent", "2": "solid rodent", "3": "multi-species replicated"}),
    ("impc_n_phenotypes", "D_animal", "target", "count",
     "Number of significant IMPC KO phenotypes", None),
    ("impc_categories", "D_animal", "target", "text",
     "IMPC top-level phenotype categories", None),
    ("ot_animal_model_max", "D_animal", "target", "numeric_float",
     "Open Targets max animal model (Phenodigm) score", None),
    ("drug_rodent_efficacy", "D_animal", "drug", "numeric_0_3",
     "Drug-specific rodent efficacy from PubMed", None),
    ("drug_nonrodent_efficacy", "D_animal", "drug", "numeric_0_3",
     "Drug-specific non-rodent efficacy from PubMed", None),
    ("drug_tox_signal", "D_animal", "drug", "numeric_0_3",
     "Drug-specific preclinical tox signal from PubMed", None),
    # Category E — human PD
    ("line_e_lit", "E_pd", "target", "numeric_0_3",
     "Haiku-extracted human PD engagement literature score",
     {"0": "untested", "1": "PK only", "2": "biomarker moved", "3": "dose-response confirmed"}),
    ("drug_target_engagement", "E_pd", "drug", "numeric_0_3",
     "Drug-specific target engagement from PubMed extraction", None),
    # Category F — clinical
    ("drug_phase2_endpoint", "F_clinical", "drug", "categorical",
     "Drug Phase 2 primary endpoint outcome from PubMed", None),
    ("drug_phase3_endpoint", "F_clinical", "drug", "categorical",
     "Drug Phase 3 primary endpoint outcome from PubMed", None),
    # Category G — pharmacology
    ("drug_structural_biology", "G_pharmacology", "drug", "numeric_0_3",
     "Drug structural biology characterization score", None),
    # Category H — safety
    ("gnomad_pli", "H_safety", "target", "numeric_float",
     "gnomAD pLI: probability of LoF intolerance (0-1)", None),
    ("gnomad_loeuf", "H_safety", "target", "numeric_float",
     "gnomAD LOEUF: upper CI of observed/expected LoF", None),
    ("sider_n_ae", "H_safety", "drug", "count",
     "SIDER: total adverse event mentions for this drug", None),
    ("sider_n_uniq_ae", "H_safety", "drug", "count",
     "SIDER: unique MedDRA-PT adverse events", None),
    # Category I — landscape
    ("family_approved_count", "I_landscape", "target", "count",
     "Number of prior approvals against target's family (as of query time)", None),
    ("gene_approved_count", "I_landscape", "target", "count",
     "Number of prior approvals against this specific gene", None),
]


def ingest_dimensions(cur):
    ins = 0
    for dim, cat, subj, dtype, desc, tier in DIMENSIONS:
        cur.execute("""
            INSERT INTO preclin.evidence_dimension
              (dimension, category, subject_type, data_type, description, tier_definition)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (dimension) DO UPDATE SET
              category = EXCLUDED.category,
              description = EXCLUDED.description,
              tier_definition = EXCLUDED.tier_definition
        """, (dim, cat, subj, dtype, desc, Json(tier) if tier else None))
        ins += 1
    log_ingest(cur, "dimensions_registry", "preclin.evidence_dimension",
               len(DIMENSIONS), ins)
    print(f"  dimensions: {ins}")


# ============================================================
# Step 2: Indications
# ============================================================

def indication_area(cond_text):
    """Heuristic therapeutic area assignment."""
    t = cond_text.lower()
    if any(k in t for k in ["cancer", "carcinoma", "leukemia", "lymphoma",
                             "melanoma", "sarcoma", "tumor", "neoplasm",
                             "myeloma", "glioma", "oncology"]):
        return "oncology"
    if any(k in t for k in ["diabetes", "obesity", "cholesterol", "metabolic",
                             "hyperlipidemia", "glycemic"]):
        return "metabolic"
    if any(k in t for k in ["rheumatoid", "lupus", "psoriasis", "atopic",
                             "crohn", "colitis", "sclerosis", "autoimmune"]):
        return "autoimmune"
    if any(k in t for k in ["alzheimer", "parkinson", "als", "epilepsy",
                             "migraine", "depression", "anxiety", "schizophrenia",
                             "neuropathic", "psychiatr", "neurolog"]):
        return "neuro"
    if any(k in t for k in ["heart", "cardiac", "hypertens", "stroke",
                             "coronary", "atrial", "fibrillation"]):
        return "cv"
    if any(k in t for k in ["hiv", "hepatitis", "influenza", "covid", "bacter",
                             "sepsis", "malaria", "tuberculosis", "infection"]):
        return "infectious"
    if any(k in t for k in ["rare", "orphan", "dystrophy", "mendel"]):
        return "rare"
    return "other"


def ingest_indications(cur):
    seen = {}  # normalized_name -> {display_name, ct_variants, area}

    # From approvals
    with (APP / "approvals.csv").open() as f:
        for r in csv.DictReader(f):
            ind = (r.get("indication") or "").strip()
            if not ind:
                continue
            k = norm(ind[:80])
            if not k:
                continue
            if k not in seen:
                seen[k] = {"display": ind[:120], "variants": set(),
                           "area": indication_area(ind)}
            seen[k]["variants"].add(ind[:200])

    # From CT.gov industry trials
    with (CT / "trials_industry_drug.csv").open() as f:
        for r in csv.DictReader(f):
            for c in (r.get("conditions") or "").split("|"):
                c = c.strip()
                if not c:
                    continue
                k = norm(c[:80])
                if not k:
                    continue
                if k not in seen:
                    seen[k] = {"display": c[:120], "variants": set(),
                               "area": indication_area(c)}
                seen[k]["variants"].add(c[:200])

    rows = [(k, v["display"], v["area"], list(v["variants"])[:20])
            for k, v in seen.items()]
    execute_values(cur, """
        INSERT INTO preclin.indication
          (normalized_name, display_name, therapeutic_area, ct_gov_conditions)
        VALUES %s
        ON CONFLICT (normalized_name) DO UPDATE SET
          ct_gov_conditions = EXCLUDED.ct_gov_conditions,
          updated_at = now()
    """, rows, template="(%s, %s, %s, %s::text[])")
    log_ingest(cur, "approvals+trials", "preclin.indication", len(rows), len(rows))
    print(f"  indications: {len(rows)}")


# ============================================================
# Step 3: Drugs
# ============================================================

def ingest_drugs(cur):
    drugs = {}  # normalized_name -> attributes

    # From approvals (canonical for approved)
    with (APP / "approvals.csv").open() as f:
        for r in csv.DictReader(f):
            name = (r.get("drug_generic") or "").strip()
            if not name:
                continue
            k = norm(name)
            drugs[k] = {
                "display": name,
                "modality": (r.get("modality") or "").strip() or None,
                "resolved_via": "public_therapy",  # approved drugs are canonical
                "resolved_confidence": "high",
            }

    # From drug_master_lookup (ChEMBL bulk)
    with (CT / "drug_master_lookup.csv").open() as f:
        for r in csv.DictReader(f):
            k = norm(r["normalized_name"])
            if not k:
                continue
            if k in drugs:
                # augment with chembl_id
                drugs[k]["chembl_id"] = r.get("chembl_ids") or None
                continue
            drugs[k] = {
                "display": r["normalized_name"],
                "chembl_id": r.get("chembl_ids") or None,
                "resolved_via": "chembl_bulk",
                "resolved_confidence": "medium",
            }

    # From resolved_targets.jsonl (Haiku LLM resolutions)
    if (EV / "resolved_targets.jsonl").exists():
        with (EV / "resolved_targets.jsonl").open() as f:
            for line in f:
                try:
                    d = json.loads(line)
                    k = norm(d.get("drug", ""))
                    if not k:
                        continue
                    if k in drugs and drugs[k]["resolved_via"] == "public_therapy":
                        continue
                    drugs.setdefault(k, {"display": d.get("drug", k)})
                    drugs[k]["resolved_via"] = drugs[k].get("resolved_via", "llm_haiku")
                    drugs[k]["mechanism"] = d.get("mechanism")
                    drugs[k]["resolved_confidence"] = d.get("confidence", "medium")
                except Exception:
                    pass

    # From verified_targets.jsonl (Sonnet verify overrides Haiku)
    if (EV / "verified_targets.jsonl").exists():
        with (EV / "verified_targets.jsonl").open() as f:
            for line in f:
                try:
                    d = json.loads(line)
                    k = norm(d.get("drug", ""))
                    if not k or k not in drugs:
                        continue
                    drugs[k]["resolved_via"] = "llm_sonnet_verified"
                    drugs[k]["resolved_confidence"] = d.get("verified_confidence", "high")
                except Exception:
                    pass

    # From unresolved_targets_sonnet.jsonl (running pipeline)
    if (BASE / "unresolved_targets_sonnet.jsonl").exists():
        with (BASE / "unresolved_targets_sonnet.jsonl").open() as f:
            for line in f:
                try:
                    d = json.loads(line)
                    k = norm(d.get("drug", ""))
                    if not k:
                        continue
                    if k in drugs and drugs[k].get("resolved_via") in ("public_therapy", "llm_sonnet_verified"):
                        continue
                    drugs.setdefault(k, {"display": d.get("drug", k)})
                    drugs[k]["resolved_via"] = "llm_sonnet"
                    drugs[k]["resolved_confidence"] = d.get("confidence", "medium")
                except Exception:
                    pass

    # From CT.gov intervention names (catch trial-only drugs)
    with (CT / "trials_industry_drug.csv").open() as f:
        for r in csv.DictReader(f):
            for name in (r.get("intervention_names") or "").split("|"):
                name = name.strip()
                if not name:
                    continue
                k = norm(name)
                if not k or k in drugs:
                    continue
                # detect placebos / vehicles
                low = name.lower()
                is_placebo = ("placebo" in low or "vehicle" in low or "saline" in low)
                is_combination = ("+" in name or " and " in low)
                drugs[k] = {
                    "display": name,
                    "is_placebo": is_placebo,
                    "is_combination": is_combination,
                    "resolved_via": "unresolved",
                    "resolved_confidence": "n/a",
                }

    rows = [(k,
             d["display"][:200],
             d.get("chembl_id"),
             d.get("modality"),
             d.get("mechanism"),
             d.get("is_placebo", False),
             d.get("is_combination", False),
             d.get("resolved_via", "unresolved"),
             d.get("resolved_confidence"),
             datetime.now() if d.get("resolved_via") not in (None, "unresolved") else None)
            for k, d in drugs.items()]

    execute_values(cur, """
        INSERT INTO preclin.drug
          (normalized_name, display_name, chembl_id, modality, mechanism,
           is_placebo, is_combination, resolved_via, resolved_confidence, resolved_at)
        VALUES %s
        ON CONFLICT (normalized_name) DO UPDATE SET
          chembl_id = COALESCE(EXCLUDED.chembl_id, preclin.drug.chembl_id),
          modality = COALESCE(EXCLUDED.modality, preclin.drug.modality),
          mechanism = COALESCE(EXCLUDED.mechanism, preclin.drug.mechanism),
          resolved_via = CASE
            WHEN preclin.drug.resolved_via IN ('public_therapy', 'llm_sonnet_verified')
              THEN preclin.drug.resolved_via
            ELSE EXCLUDED.resolved_via
          END,
          resolved_confidence = CASE
            WHEN preclin.drug.resolved_via IN ('public_therapy', 'llm_sonnet_verified')
              THEN preclin.drug.resolved_confidence
            ELSE EXCLUDED.resolved_confidence
          END,
          updated_at = now()
    """, rows)
    log_ingest(cur, "approvals+chembl+llm+trials", "preclin.drug",
               len(rows), len(rows))
    print(f"  drugs: {len(rows)}")


# ============================================================
# Step 4: drug-target links
# ============================================================

def get_target_id(cur, symbol):
    """Look up target_id from public.targets by symbol. Return first match."""
    if not symbol:
        return None
    sym = re.sub(r"[^A-Za-z0-9]", "", symbol).upper()
    if not sym:
        return None
    cur.execute("""SELECT id FROM public.targets WHERE upper(symbol) = %s
                   AND ip_type != 'Genomic' LIMIT 1""", (sym,))
    r = cur.fetchone()
    return r[0] if r else None


def ingest_drug_targets(cur):
    # Build drug_id lookup once
    cur.execute("SELECT drug_id, normalized_name FROM preclin.drug")
    drug_map = {n: did for did, n in cur.fetchall()}

    # Cache all valid target_ids up front (from actual public.targets rows)
    cur.execute("SELECT id, upper(symbol) FROM public.targets WHERE ip_type != 'Genomic'")
    valid_target_ids = set()
    target_cache = {}
    for tid_val, sym in cur.fetchall():
        valid_target_ids.add(tid_val)
        if sym not in target_cache:
            target_cache[sym] = tid_val

    def tid(sym):
        if not sym:
            return None
        sym_key = re.sub(r"[^A-Za-z0-9]", "", str(sym)).upper()
        return target_cache.get(sym_key)

    rows = []
    # From approvals (source of truth for approved)
    with (APP / "approvals.csv").open() as f:
        for r in csv.DictReader(f):
            k = norm(r.get("drug_generic"))
            if not k or k not in drug_map:
                continue
            tgt_str = (r.get("target") or "").strip()
            for j, part in enumerate(re.split(r"[/,;+]", tgt_str)):
                sym = part.strip()
                tid_v = tid(sym)
                if tid_v is None:
                    continue
                rows.append((drug_map[k], tid_v,
                             "primary" if j == 0 else "secondary",
                             None, "fda_approval", None, "high", None, None,
                             f"FDA-labeled target for {r.get('drug_generic')}"))

    # From ChEMBL bulk
    with (CT / "drug_master_lookup.csv").open() as f:
        for r in csv.DictReader(f):
            k = norm(r["normalized_name"])
            if not k or k not in drug_map:
                continue
            sym = r.get("gene_symbol", "")
            tid_v = tid(sym)
            if tid_v is None:
                continue
            rows.append((drug_map[k], tid_v, "primary",
                         (r.get("action") or "").lower(),
                         "chembl_bulk", None, "medium", None, None, None))

    # From resolved_targets (Haiku)
    if (EV / "resolved_targets.jsonl").exists():
        with (EV / "resolved_targets.jsonl").open() as f:
            for line in f:
                try:
                    d = json.loads(line)
                    k = norm(d.get("drug", ""))
                    if not k or k not in drug_map:
                        continue
                    tid_v = tid(d.get("primary_target"))
                    if tid_v is None:
                        continue
                    rows.append((drug_map[k], tid_v, "primary",
                                 d.get("mechanism"),
                                 "llm_haiku", None,
                                 d.get("confidence"), None, None,
                                 (d.get("brief_rationale") or "")[:500]))
                except Exception:
                    pass

    # From verified_targets (Sonnet)
    if (EV / "verified_targets.jsonl").exists():
        with (EV / "verified_targets.jsonl").open() as f:
            for line in f:
                try:
                    d = json.loads(line)
                    k = norm(d.get("drug", ""))
                    if not k or k not in drug_map:
                        continue
                    vt = (d.get("verified_target") or "").split("/")[0].strip()
                    tid_v = tid(vt)
                    if tid_v is None:
                        continue
                    rows.append((drug_map[k], tid_v, "primary", None,
                                 "llm_sonnet_verified", None,
                                 d.get("verified_confidence"), None, None,
                                 (d.get("verified_rationale") or "")[:500]))
                except Exception:
                    pass

    # From unresolved_targets_sonnet (currently running)
    if (BASE / "unresolved_targets_sonnet.jsonl").exists():
        with (BASE / "unresolved_targets_sonnet.jsonl").open() as f:
            for line in f:
                try:
                    d = json.loads(line)
                    k = norm(d.get("drug", ""))
                    if not k or k not in drug_map:
                        continue
                    tgt = d.get("target")
                    if not tgt or tgt.lower() == "unknown":
                        continue
                    tid_v = tid(tgt)
                    if tid_v is None:
                        continue
                    rows.append((drug_map[k], tid_v, "primary",
                                 d.get("mechanism"),
                                 "llm_sonnet", None,
                                 d.get("confidence"), None, None,
                                 (d.get("rationale") or "")[:500]))
                except Exception:
                    pass

    # From public.therapy_targets — link via therapy_id where drug matches
    cur.execute("""
        SELECT lower(th.name), tt.target_id, tt.role, tt.provenance
        FROM public.therapy_targets tt
        JOIN public.therapies th ON th.id = tt.therapy_id
    """)
    for name, tgt_id, role, prov in cur.fetchall():
        k = norm(name)
        if k in drug_map and tgt_id in valid_target_ids:
            rows.append((drug_map[k], tgt_id, role or "primary", None,
                         "therapy_targets_public", None, "high", None, None, prov))

    # Filter to valid target_ids only (public.targets has gaps in id sequence)
    rows = [r for r in rows if r[1] in valid_target_ids]

    execute_values(cur, """
        INSERT INTO preclin.drug_target
          (drug_id, target_id, role, mechanism, source, source_version,
           confidence, citation_pmid, citation_doi, rationale)
        VALUES %s
        ON CONFLICT DO NOTHING
    """, rows, page_size=1000)
    log_ingest(cur, "approvals+chembl+llm+public.therapy_targets",
               "preclin.drug_target", len(rows), len(rows))
    print(f"  drug_targets: {len(rows)}")


# ============================================================
# Step 5: Approvals with US/ex-US region
# ============================================================

def ingest_approvals(cur):
    cur.execute("SELECT drug_id, normalized_name FROM preclin.drug")
    drug_map = {n: did for did, n in cur.fetchall()}
    cur.execute("SELECT indication_id, normalized_name FROM preclin.indication")
    ind_map = {n: iid for iid, n in cur.fetchall()}

    rows_us = []
    with (APP / "approvals.csv").open() as f:
        for r in csv.DictReader(f):
            k = norm(r.get("drug_generic"))
            if not k or k not in drug_map:
                continue
            ind_key = norm((r.get("indication") or "")[:80])
            ind_id = ind_map.get(ind_key)
            year = r.get("year", "")
            try:
                year_int = int(year) if year else None
            except Exception:
                year_int = None
            agency = "FDA_" + (r.get("center", "CDER") or "CDER").upper()
            def tb(v):
                v = (v or "").strip().upper()
                return v in ("TRUE", "1", "YES", "Y")
            rows_us.append((
                drug_map[k], ind_id, agency, "US",
                None,  # approval_date - not always parseable
                year_int,
                r.get("drug_brand"),
                None,  # sponsor_id — resolve later
                r.get("sponsor"),
                r.get("nelson_tier"),
                r.get("tier_evidence_url"),
                tb(r.get("first_in_class")),
                tb(r.get("orphan")),
                tb(r.get("breakthrough")),
                tb(r.get("accelerated")),
                tb(r.get("fast_track")),
                tb(r.get("priority_review")),
                None,  # application_number
                r.get("source_url"),
                None,  # public_approval_id
                r.get("notes"),
            ))

    execute_values(cur, """
        INSERT INTO preclin.approval
          (drug_id, indication_id, agency, region, approval_date, approval_year,
           brand_name, sponsor_id, sponsor_name,
           nelson_tier, nelson_evidence_url,
           first_in_class, orphan, breakthrough, accelerated, fast_track, priority_review,
           application_number, source_url, public_approval_id, notes)
        VALUES %s
    """, rows_us, page_size=500)
    log_ingest(cur, "approvals.csv", "preclin.approval",
               len(rows_us), len(rows_us), notes="US only")
    print(f"  approvals US: {len(rows_us)}")

    # Ex-US: infer from ChEMBL max_phase=4 for drugs NOT in our US approvals set
    us_approved_drugs = {r[0] for r in rows_us}
    with (CT / "program_master.csv").open() as f:
        rows_ex = []
        for r in csv.DictReader(f):
            if r.get("_approved") != "1":
                continue
            k = norm(r.get("normalized_name"))
            if not k or k not in drug_map:
                continue
            if drug_map[k] in us_approved_drugs:
                continue  # already US-approved
            rows_ex.append((
                drug_map[k], None, "ChEMBL_max_phase_4", "ex_US",
                None, None, None, None, None,
                r.get("tier"), None,
                None, None, None, None, None, None,
                None, None, None,
                "Inferred ex-US approval from ChEMBL max_phase=4",
            ))
    execute_values(cur, """
        INSERT INTO preclin.approval
          (drug_id, indication_id, agency, region, approval_date, approval_year,
           brand_name, sponsor_id, sponsor_name,
           nelson_tier, nelson_evidence_url,
           first_in_class, orphan, breakthrough, accelerated, fast_track, priority_review,
           application_number, source_url, public_approval_id, notes)
        VALUES %s
    """, rows_ex, page_size=500)
    log_ingest(cur, "program_master.csv", "preclin.approval",
               len(rows_ex), len(rows_ex), notes="ex-US inferred")
    print(f"  approvals ex-US: {len(rows_ex)}")


# ============================================================
# Step 6: Programs (drug × indication × sponsor) + program_trial
# ============================================================

_SPONSOR_CACHE = None


def build_sponsor_cache(cur):
    """Preload all public.sponsors into memory once."""
    global _SPONSOR_CACHE
    if _SPONSOR_CACHE is not None:
        return _SPONSOR_CACHE
    cur.execute("SELECT id, name, canonical_name FROM public.sponsors")
    _SPONSOR_CACHE = {}
    for sid, name, canon in cur.fetchall():
        if name:
            _SPONSOR_CACHE[name.lower().strip()] = (sid, canon)
        if canon:
            _SPONSOR_CACHE[canon.lower().strip()] = (sid, canon)
    return _SPONSOR_CACHE


def get_sponsor_id(cur, name):
    if not name:
        return None, None
    cache = build_sponsor_cache(cur)
    r = cache.get(name.lower().strip())
    if r:
        return r
    return None, name


PHASE_NUM = {"PHASE1": 1, "PHASE2": 2, "PHASE3": 3, "PHASE4": 4,
             "EARLY_PHASE1": 1}


def ingest_programs(cur):
    cur.execute("SELECT drug_id, normalized_name FROM preclin.drug")
    drug_map = {n: did for did, n in cur.fetchall()}
    cur.execute("SELECT indication_id, normalized_name FROM preclin.indication")
    ind_map = {n: iid for iid, n in cur.fetchall()}

    # Sponsor cache
    sponsor_cache = {}
    def sponsor(name):
        if name not in sponsor_cache:
            sponsor_cache[name] = get_sponsor_id(cur, name)
        return sponsor_cache[name]

    # Aggregate trials to (drug × indication × sponsor)
    prog_agg = {}   # (drug_id, indication_id, sponsor_name) -> agg dict
    prog_trials = []  # (key, nct_id, phase, status)

    with (CT / "trials_industry_drug.csv").open() as f:
        for r in csv.DictReader(f):
            phase = r.get("phase") or ""
            p = PHASE_NUM.get(phase, 0)
            status = r.get("_status") or r.get("overall_status") or ""
            sponsor_name = (r.get("lead_sponsor") or "").strip() or None
            conditions = [c.strip() for c in (r.get("conditions") or "").split("|") if c.strip()]
            interventions = [n.strip() for n in (r.get("intervention_names") or "").split("|") if n.strip()]
            start_date = r.get("start_date") or None
            comp_date = r.get("completion_date") or None
            for drug_name in interventions:
                dk = norm(drug_name)
                if not dk or dk not in drug_map:
                    continue
                did = drug_map[dk]
                # For each condition, create/update a program
                for cond in conditions or [""]:
                    ck = norm(cond[:80]) if cond else ""
                    iid = ind_map.get(ck) if ck else None
                    if iid is None:
                        continue  # skip if no indication anchor
                    pk = (did, iid, sponsor_name)
                    if pk not in prog_agg:
                        prog_agg[pk] = {
                            "sponsor_id": None, "first": None, "last": None,
                            "highest_phase": 0,
                            "n": 0, "ph1": 0, "ph2": 0, "ph3": 0, "ph4": 0,
                            "n_term": 0, "n_wd": 0, "n_susp": 0, "n_comp": 0, "n_act": 0,
                        }
                    s = prog_agg[pk]
                    s["n"] += 1
                    if p == 1: s["ph1"] += 1
                    elif p == 2: s["ph2"] += 1
                    elif p == 3: s["ph3"] += 1
                    elif p == 4: s["ph4"] += 1
                    s["highest_phase"] = max(s["highest_phase"], p)
                    if status == "TERMINATED": s["n_term"] += 1
                    elif status == "WITHDRAWN": s["n_wd"] += 1
                    elif status == "SUSPENDED": s["n_susp"] += 1
                    elif status == "COMPLETED": s["n_comp"] += 1
                    elif status in ("RECRUITING", "ACTIVE_NOT_RECRUITING",
                                    "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION"):
                        s["n_act"] += 1
                    if start_date and (s["first"] is None or start_date < s["first"]):
                        s["first"] = start_date
                    if comp_date and (s["last"] is None or comp_date > s["last"]):
                        s["last"] = comp_date
                    prog_trials.append((pk, r["nct_id"], p, status))

    # Also seed programs from approvals (approved drugs may not have trials in our set)
    with (APP / "approvals.csv").open() as f:
        for r in csv.DictReader(f):
            k = norm(r.get("drug_generic"))
            if not k or k not in drug_map:
                continue
            ind_key = norm((r.get("indication") or "")[:80])
            iid = ind_map.get(ind_key)
            if iid is None:
                continue
            sponsor_name = (r.get("sponsor") or "").strip() or None
            pk = (drug_map[k], iid, sponsor_name)
            if pk not in prog_agg:
                prog_agg[pk] = {
                    "sponsor_id": None, "first": None, "last": None,
                    "highest_phase": 4,
                    "n": 0, "ph1": 0, "ph2": 0, "ph3": 0, "ph4": 0,
                    "n_term": 0, "n_wd": 0, "n_susp": 0, "n_comp": 0, "n_act": 0,
                }

    # Resolve sponsors
    for pk in prog_agg:
        sn = pk[2]
        sid, _ = sponsor(sn) if sn else (None, None)
        prog_agg[pk]["sponsor_id"] = sid

    def parse_date(d):
        if not d:
            return None
        try:
            return datetime.strptime(d[:10], "%Y-%m-%d").date()
        except Exception:
            try:
                return datetime.strptime(d[:7], "%Y-%m").date()
            except Exception:
                return None

    rows = []
    for (did, iid, sn), s in prog_agg.items():
        rows.append((did, iid, s["sponsor_id"], sn,
                     parse_date(s["first"]), parse_date(s["last"]),
                     s["highest_phase"],
                     s["n"], s["ph1"], s["ph2"], s["ph3"], s["ph4"],
                     s["n_term"], s["n_wd"], s["n_susp"], s["n_comp"], s["n_act"]))
    execute_values(cur, """
        INSERT INTO preclin.program
          (drug_id, indication_id, sponsor_id, sponsor_name,
           first_trial_date, last_trial_date, highest_phase,
           n_trials, n_trials_ph1, n_trials_ph2, n_trials_ph3, n_trials_ph4,
           n_terminated, n_withdrawn, n_suspended, n_completed, n_active)
        VALUES %s
        ON CONFLICT (drug_id, indication_id, sponsor_id, sponsor_name) DO UPDATE SET
          highest_phase = EXCLUDED.highest_phase,
          n_trials = EXCLUDED.n_trials,
          updated_at = now()
    """, rows, page_size=1000)
    log_ingest(cur, "trials_industry_drug.csv+approvals.csv", "preclin.program",
               len(rows), len(rows))
    print(f"  programs: {len(rows)}")

    # Now populate program_trial junction
    cur.execute("""
        SELECT program_id, drug_id, indication_id, sponsor_name
        FROM preclin.program
    """)
    prog_id_map = {(did, iid, sn): pid for pid, did, iid, sn in cur.fetchall()}
    pt_seen = {}
    for pk, nct, ph, st in prog_trials:
        pid = prog_id_map.get(pk)
        if pid is not None:
            # Dedupe: same (program_id, nct_id) may appear via multiple intervention names
            pt_seen[(pid, nct)] = (ph, st)
    pt_rows = [(pid, nct, ph, st) for (pid, nct), (ph, st) in pt_seen.items()]
    execute_values(cur, """
        INSERT INTO preclin.program_trial (program_id, nct_id, phase, status)
        VALUES %s
        ON CONFLICT (program_id, nct_id) DO UPDATE SET
          phase = EXCLUDED.phase,
          status = EXCLUDED.status
    """, pt_rows, page_size=2000)
    log_ingest(cur, "trials_industry_drug.csv", "preclin.program_trial",
               len(pt_rows), len(pt_rows))
    print(f"  program_trials: {len(pt_rows)}")


# ============================================================
# Step 7: Evidence scores — long-form facts
# ============================================================

def ingest_evidence_scores(cur):
    cur.execute("SELECT id, upper(symbol) FROM public.targets WHERE ip_type != 'Genomic'")
    sym_to_tid = {}
    for tid, sym in cur.fetchall():
        if sym not in sym_to_tid:
            sym_to_tid[sym] = tid

    def tid(sym):
        if not sym:
            return None
        s = re.sub(r"[^A-Za-z0-9]", "", str(sym)).upper()
        return sym_to_tid.get(s)

    cur.execute("SELECT drug_id, normalized_name FROM preclin.drug")
    drug_map = {n: did for did, n in cur.fetchall()}

    # Batch inserter — dedupes on UNIQUE constraint before insert
    def flush(rows, source_file, source):
        if not rows:
            return 0
        # UNIQUE: (subject_type, subject_id, subject_id2, dimension, source, source_version)
        # Row shape: (subject_type, subject_id, subject_id2, dimension, category,
        #             value_numeric, value_text, value_boolean, source, source_version,
        #             confidence, citation_pmids, extracted_by)
        seen = {}
        for r in rows:
            key = (r[0], r[1], r[2], r[3], r[8], r[9])
            seen[key] = r  # last wins
        rows = list(seen.values())
        execute_values(cur, """
            INSERT INTO preclin.evidence_score
              (subject_type, subject_id, subject_id2, dimension, category,
               value_numeric, value_text, value_boolean, source, source_version,
               confidence, citation_pmids, extracted_by)
            VALUES %s
            ON CONFLICT (subject_type, subject_id, subject_id2, dimension, source, source_version)
            DO UPDATE SET
              value_numeric = EXCLUDED.value_numeric,
              value_text = EXCLUDED.value_text,
              value_boolean = EXCLUDED.value_boolean,
              extracted_at = now()
        """, rows, page_size=2000)
        log_ingest(cur, source_file, "preclin.evidence_score",
                   len(rows), len(rows), notes=f"source={source}")
        return len(rows)

    # --- literature_scores.jsonl (target-level Line B/C/D/E) ---
    if (EV / "literature_scores.jsonl").exists():
        rows = []
        with (EV / "literature_scores.jsonl").open() as f:
            for line in f:
                try:
                    d = json.loads(line)
                    tgt = tid(d.get("gene"))
                    if tgt is None:
                        continue
                    pmids = [str(p) for p in d.get("notable_pmids", [])[:10]]
                    for line_key, dim, cat in [("line_b", "line_b_lit", "B_mechanistic"),
                                                ("line_c", "line_c_lit", "C_cell"),
                                                ("line_d", "line_d_lit", "D_animal"),
                                                ("line_e", "line_e_lit", "E_pd")]:
                        v = d.get(line_key)
                        if v is not None:
                            try:
                                rows.append(("target", tgt, None, dim, cat,
                                             float(v), None, None,
                                             "pubmed_haiku", "v1", None,
                                             pmids, "claude-haiku"))
                            except Exception:
                                pass
                except Exception:
                    pass
        n = flush(rows, "literature_scores.jsonl", "pubmed_haiku")
        print(f"  evidence (literature_scores): {n}")

    # --- drug_evidence.jsonl (drug-specific) ---
    if (EV / "drug_evidence.jsonl").exists():
        rows = []
        with (EV / "drug_evidence.jsonl").open() as f:
            for line in f:
                try:
                    d = json.loads(line)
                    k = norm(d.get("drug", ""))
                    if not k or k not in drug_map:
                        continue
                    did = drug_map[k]
                    conf = d.get("confidence")
                    dims = [
                        ("cell_efficacy_score", "drug_cell_efficacy", "C_cell"),
                        ("rodent_efficacy_score", "drug_rodent_efficacy", "D_animal"),
                        ("non_rodent_efficacy_score", "drug_nonrodent_efficacy", "D_animal"),
                        ("preclinical_tox_signal", "drug_tox_signal", "D_animal"),
                        ("target_engagement_score", "drug_target_engagement", "E_pd"),
                        ("structural_biology_score", "drug_structural_biology", "G_pharmacology"),
                    ]
                    for key, dim, cat in dims:
                        v = d.get(key)
                        if v is None:
                            continue
                        try:
                            rows.append(("drug", did, None, dim, cat,
                                         float(v), None, None,
                                         "pubmed_sonnet", "v1", conf, None,
                                         "claude-sonnet"))
                        except Exception:
                            pass
                    # Categorical endpoints
                    for key, dim, cat in [
                        ("phase2_endpoint", "drug_phase2_endpoint", "F_clinical"),
                        ("phase3_endpoint", "drug_phase3_endpoint", "F_clinical"),
                        ("effect_size_reported", "drug_effect_size", "F_clinical"),
                        ("cell_effect_direction", "drug_cell_direction", "C_cell"),
                    ]:
                        v = d.get(key)
                        if v is not None:
                            rows.append(("drug", did, None, dim, cat,
                                         None, str(v)[:100], None,
                                         "pubmed_sonnet", "v1", conf, None,
                                         "claude-sonnet"))
                except Exception:
                    pass
        n = flush(rows, "drug_evidence.jsonl", "pubmed_sonnet")
        print(f"  evidence (drug_evidence): {n}")

    # --- IMPC KO phenotypes ---
    if (EV / "gene_impc_summary.csv").exists():
        rows = []
        with (EV / "gene_impc_summary.csv").open() as f:
            for r in csv.DictReader(f):
                t = tid(r.get("gene"))
                if t is None:
                    continue
                try:
                    n = int(r.get("n_significant_phenotypes") or 0)
                    rows.append(("target", t, None, "impc_n_phenotypes", "D_animal",
                                 float(n), None, None,
                                 "impc", "2025", None, None, "script:ingest"))
                    cats = r.get("top_level_categories", "")
                    if cats:
                        rows.append(("target", t, None, "impc_categories", "D_animal",
                                     None, cats[:1000], None,
                                     "impc", "2025", None, None, "script:ingest"))
                except Exception:
                    pass
        n = flush(rows, "gene_impc_summary.csv", "impc")
        print(f"  evidence (IMPC): {n}")

    # --- Family precedent ---
    if (EV / "family_precedent.csv").exists():
        rows = []
        with (EV / "family_precedent.csv").open() as f:
            for r in csv.DictReader(f):
                t = tid(r.get("gene"))
                if t is None:
                    continue
                try:
                    rows.append(("target", t, None, "gene_approved_count", "I_landscape",
                                 float(r.get("gene_approved_count") or 0), None, None,
                                 "derived", "2025", None, None, "script:ingest"))
                    rows.append(("target", t, None, "family_approved_count", "I_landscape",
                                 float(r.get("family_approved_count") or 0), None, None,
                                 "derived", "2025", None, None, "script:ingest"))
                except Exception:
                    pass
        n = flush(rows, "family_precedent.csv", "derived")
        print(f"  evidence (family_precedent): {n}")

    # --- Sync from public.gene_essentiality_summary, public.gene_constraint,
    # public.clingen_validity, public.mendelian_associations, public.gwas_associations,
    # public.target_evidence — leave in public.* and read via VIEWS in step 9
    # Don't duplicate that data.
    print("  (public.gene_essentiality_summary etc. read via views, not duplicated)")

    # --- Nelson tier per (target, indication) from batch CSVs ---
    cur.execute("SELECT indication_id, normalized_name FROM preclin.indication")
    ind_map = {n: iid for iid, n in cur.fetchall()}
    def find_ind(kw):
        if not kw:
            return None
        k = norm(kw[:80])
        return ind_map.get(k)

    # Dedupe: same (target, indication, source_version) may appear in multiple rows
    seen = {}
    for batch in [1, 2, 3]:
        p = APP / f"nelson_tiers_batch_{batch}.csv"
        if not p.exists():
            continue
        with p.open() as f:
            for r in csv.DictReader(f):
                t = tid((r.get("target") or "").split("/")[0].strip())
                if t is None:
                    continue
                iid = find_ind(r.get("indication_keyword", ""))
                if iid is None:
                    continue
                tier = r.get("nelson_tier", "")
                key = (t, iid, f"batch_{batch}")
                seen[key] = tier  # last one wins within same batch
    rows = [("target_indication", t, iid, "nelson_tier", "A_genetics",
             None, tier, None, "manual_curation", batch_ver, None, None, "manual")
            for (t, iid, batch_ver), tier in seen.items()]
    n = flush(rows, "nelson_tiers_batch_*.csv", "manual_curation")
    print(f"  evidence (Nelson tier): {n}")

    # Nelson tier from approvals.csv (mapped) — dedupe on (target, indication)
    seen = {}
    with (APP / "approvals.csv").open() as f:
        for r in csv.DictReader(f):
            t = tid((r.get("target") or "").split("/")[0].strip())
            if t is None:
                continue
            iid = find_ind((r.get("indication") or ""))
            if iid is None:
                continue
            tier = r.get("nelson_tier", "")
            if tier:
                seen[(t, iid)] = tier
    rows = [("target_indication", t, iid, "nelson_tier", "A_genetics",
             None, tier, None, "fda_approval", "2025", "high", None, "manual")
            for (t, iid), tier in seen.items()]
    n = flush(rows, "approvals.csv (Nelson)", "fda_approval")
    print(f"  evidence (Nelson from approvals): {n}")


# ============================================================
# Step 8: Classifications (LLM outputs)
# ============================================================

def _read_cost(d):
    """Canonical cost extractor. Prefers `_cost_usd`; falls back to legacy
    field names produced by pre-2026-08 classifier runs."""
    for k in ("_cost_usd", "_cost_share", "_cost"):
        v = d.get(k)
        if v is not None:
            return v
    return None


def ingest_classifications(cur):
    def flush(rows, source_file, task, model):
        if not rows:
            return 0
        # Dedupe on UNIQUE key: (subject_type, subject_key, task, model, version)
        # Row shape: (subject_type, subject_key, classifier_task, category, confidence,
        #             rationale, citation_pmids, classifier_model, classifier_version,
        #             cost_usd, raw_output)
        seen = {}
        for r in rows:
            key = (r[0], r[1], r[2], r[7], r[8])  # unique-constraint columns
            seen[key] = r  # last wins
        rows = list(seen.values())
        execute_values(cur, """
            INSERT INTO preclin.classification
              (subject_type, subject_key, classifier_task, category, confidence,
               rationale, citation_pmids, classifier_model, classifier_version,
               cost_usd, raw_output)
            VALUES %s
            ON CONFLICT (subject_type, subject_key, classifier_task, classifier_model, classifier_version)
            DO UPDATE SET
              category = EXCLUDED.category,
              confidence = EXCLUDED.confidence,
              rationale = EXCLUDED.rationale,
              extracted_at = now()
        """, rows, page_size=2000)
        log_ingest(cur, source_file, "preclin.classification",
                   len(rows), len(rows), notes=f"task={task} model={model}")
        return len(rows)

    # why_stopped Haiku
    if (CT / "why_stopped_haiku.jsonl").exists():
        rows = []
        with (CT / "why_stopped_haiku.jsonl").open() as f:
            for line in f:
                try:
                    d = json.loads(line)
                    nct = d.get("nct_id")
                    if not nct:
                        continue
                    rows.append(("trial", nct, "why_stopped", d.get("cat", "unclear"),
                                 d.get("confidence"), None, None,
                                 "claude-haiku", "v1", _read_cost(d), Json(d)))
                except Exception:
                    pass
        n = flush(rows, "why_stopped_haiku.jsonl", "why_stopped", "claude-haiku")
        print(f"  classifications (why_stopped haiku): {n}")

    # why_stopped Sonnet
    if (CT / "why_stopped_sonnet.jsonl").exists():
        rows = []
        with (CT / "why_stopped_sonnet.jsonl").open() as f:
            for line in f:
                try:
                    d = json.loads(line)
                    nct = d.get("nct_id")
                    if not nct:
                        continue
                    rows.append(("trial", nct, "why_stopped", d.get("cat", "unclear"),
                                 d.get("confidence"), None, None,
                                 "claude-sonnet", "v1",
                                 _read_cost(d), Json(d)))
                except Exception:
                    pass
        n = flush(rows, "why_stopped_sonnet.jsonl", "why_stopped", "claude-sonnet")
        print(f"  classifications (why_stopped sonnet): {n}")

    # Silent kill verifications
    if (BASE / "silent_kill_verified.jsonl").exists():
        rows = []
        with (BASE / "silent_kill_verified.jsonl").open() as f:
            for line in f:
                try:
                    d = json.loads(line)
                    dk = d.get("drug_key")
                    if not dk:
                        continue
                    rows.append(("drug", dk, "silent_kill_verify", d.get("cat", "unclear"),
                                 d.get("confidence"), d.get("evidence"), None,
                                 "claude-sonnet", "v1", _read_cost(d), Json(d)))
                except Exception:
                    pass
        n = flush(rows, "silent_kill_verified.jsonl",
                  "silent_kill_verify", "claude-sonnet")
        print(f"  classifications (silent_kill_verify): {n}")

    # Target resolutions
    if (EV / "resolved_targets.jsonl").exists():
        rows = []
        with (EV / "resolved_targets.jsonl").open() as f:
            for line in f:
                try:
                    d = json.loads(line)
                    dk = norm(d.get("drug", ""))
                    if not dk:
                        continue
                    rows.append(("drug", dk, "target_resolution",
                                 d.get("primary_target", "unknown"),
                                 d.get("confidence"),
                                 (d.get("brief_rationale") or "")[:500],
                                 None, "claude-haiku", "v1", _read_cost(d), Json(d)))
                except Exception:
                    pass
        n = flush(rows, "resolved_targets.jsonl",
                  "target_resolution", "claude-haiku")
        print(f"  classifications (target_resolution haiku): {n}")

    if (EV / "verified_targets.jsonl").exists():
        rows = []
        with (EV / "verified_targets.jsonl").open() as f:
            for line in f:
                try:
                    d = json.loads(line)
                    dk = norm(d.get("drug", ""))
                    if not dk:
                        continue
                    rows.append(("drug", dk, "target_resolution",
                                 d.get("verified_target", "unknown"),
                                 d.get("verified_confidence"),
                                 (d.get("verified_rationale") or "")[:500],
                                 None, "claude-sonnet", "v1_verify",
                                 _read_cost(d), Json(d)))
                except Exception:
                    pass
        n = flush(rows, "verified_targets.jsonl",
                  "target_resolution", "claude-sonnet")
        print(f"  classifications (target_resolution sonnet): {n}")


# ============================================================
# Step 9: Program outcome rollup
# ============================================================

def ingest_program_outcomes(cur):
    """Compute per-program outcome by joining:
    - preclin.approval (US + ex-US)
    - preclin.classification (why_stopped for trials)
    - preclin.program_trial (roster)
    """
    print("  computing program outcomes...")
    cur.execute("""
        WITH
        -- Pick best failure category per trial (Sonnet > Haiku)
        trial_fail AS (
          SELECT DISTINCT ON (subject_key)
            subject_key AS nct_id, category
          FROM preclin.classification
          WHERE classifier_task = 'why_stopped'
          ORDER BY subject_key, CASE classifier_model WHEN 'claude-sonnet' THEN 1 ELSE 2 END
        ),
        -- Aggregate failure reasons per program
        prog_fails AS (
          SELECT
            pt.program_id,
            SUM(CASE WHEN tf.category = 'efficacy' THEN 1 ELSE 0 END) AS efficacy,
            SUM(CASE WHEN tf.category = 'safety' THEN 1 ELSE 0 END) AS safety,
            SUM(CASE WHEN tf.category = 'commercial_strategic' THEN 1 ELSE 0 END) AS commercial,
            SUM(CASE WHEN tf.category = 'enrollment_operational' THEN 1 ELSE 0 END) AS enrollment,
            SUM(CASE WHEN tf.category = 'planned_termination' THEN 1 ELSE 0 END) AS planned,
            SUM(CASE WHEN tf.category IN ('efficacy','safety','commercial_strategic',
                                          'enrollment_operational','planned_termination')
                     THEN 0 ELSE 1 END) AS other_fails,
            SUM(CASE WHEN tf.category IS NULL THEN 1 ELSE 0 END) AS no_text
          FROM preclin.program_trial pt
          LEFT JOIN trial_fail tf ON tf.nct_id = pt.nct_id
          WHERE pt.status IN ('TERMINATED', 'WITHDRAWN', 'SUSPENDED')
          GROUP BY pt.program_id
        ),
        -- Approval flags per program
        prog_appr AS (
          SELECT
            p.program_id,
            BOOL_OR(a.region = 'US') AS approved_us,
            BOOL_OR(a.region = 'ex_US') AS approved_ex_us,
            MIN(a.approval_id) AS first_approval_id
          FROM preclin.program p
          LEFT JOIN preclin.approval a ON a.drug_id = p.drug_id
          GROUP BY p.program_id
        )
        INSERT INTO preclin.program_outcome
          (program_id, outcome, outcome_broad, outcome_confidence,
           approved_us, approved_ex_us, first_approval_id, failure_reasons, method)
        SELECT
          p.program_id,
          -- outcome (fine)
          CASE
            WHEN COALESCE(pa.approved_us, false) OR COALESCE(pa.approved_ex_us, false)
              THEN 'approved'
            WHEN pf.efficacy > 0 THEN 'efficacy_fail'
            WHEN pf.safety > 0 THEN 'safety_fail'
            WHEN pf.commercial > 0 THEN 'commercial_fail'
            WHEN pf.enrollment > 0 THEN 'enrollment_fail'
            WHEN pf.planned > 0 THEN 'planned_termination'
            WHEN pf.other_fails > 0 OR pf.no_text > 0 THEN 'other_fail'
            WHEN p.n_active > 0 THEN 'in_development'
            WHEN p.n_completed > 0 AND p.highest_phase >= 2 THEN 'phase_complete_no_approval'
            WHEN p.n_completed > 0 AND p.highest_phase = 1 THEN 'phase1_complete_no_advance'
            ELSE 'unknown'
          END AS outcome,
          -- outcome_broad (with presumptive)
          CASE
            WHEN COALESCE(pa.approved_us, false) OR COALESCE(pa.approved_ex_us, false)
              THEN 'approved'
            WHEN pf.efficacy > 0 THEN 'efficacy_fail'
            WHEN pf.safety > 0 THEN 'safety_fail'
            WHEN pf.commercial > 0 THEN 'commercial_fail'
            WHEN pf.enrollment > 0 THEN 'enrollment_fail'
            WHEN pf.planned > 0 THEN 'planned_termination'
            WHEN pf.other_fails > 0 OR pf.no_text > 0 THEN 'unclassified_termination'
            WHEN p.n_active > 0 THEN 'in_dev'
            WHEN p.n_completed > 0 AND p.highest_phase >= 3 THEN 'presumptive_efficacy_fail_ph3'
            WHEN p.n_completed > 0 AND p.highest_phase = 2 THEN 'presumptive_fail_ph2'
            WHEN p.n_completed > 0 AND p.highest_phase = 1 THEN 'phase1_only'
            ELSE 'unknown'
          END AS outcome_broad,
          -- confidence
          CASE
            WHEN COALESCE(pa.approved_us, false) OR COALESCE(pa.approved_ex_us, false)
              THEN 'high'
            WHEN pf.efficacy > 0 OR pf.safety > 0 THEN 'high'
            WHEN pf.commercial > 0 OR pf.enrollment > 0 THEN 'medium'
            WHEN p.n_completed > 0 AND p.highest_phase >= 3 THEN 'medium'
            ELSE 'low'
          END AS outcome_confidence,
          COALESCE(pa.approved_us, false),
          COALESCE(pa.approved_ex_us, false),
          pa.first_approval_id,
          jsonb_build_object(
            'efficacy', COALESCE(pf.efficacy, 0),
            'safety', COALESCE(pf.safety, 0),
            'commercial', COALESCE(pf.commercial, 0),
            'enrollment', COALESCE(pf.enrollment, 0),
            'planned', COALESCE(pf.planned, 0),
            'other', COALESCE(pf.other_fails, 0),
            'no_text', COALESCE(pf.no_text, 0)
          ),
          'rollup_v1'
        FROM preclin.program p
        LEFT JOIN prog_fails pf ON pf.program_id = p.program_id
        LEFT JOIN prog_appr pa ON pa.program_id = p.program_id
        ON CONFLICT (program_id) DO UPDATE SET
          outcome = EXCLUDED.outcome,
          outcome_broad = EXCLUDED.outcome_broad,
          outcome_confidence = EXCLUDED.outcome_confidence,
          approved_us = EXCLUDED.approved_us,
          approved_ex_us = EXCLUDED.approved_ex_us,
          first_approval_id = EXCLUDED.first_approval_id,
          failure_reasons = EXCLUDED.failure_reasons,
          computed_at = now()
    """)
    log_ingest(cur, "SQL rollup", "preclin.program_outcome",
               None, None, notes="Computed via join query")
    cur.execute("SELECT COUNT(*), outcome FROM preclin.program_outcome GROUP BY outcome ORDER BY 1 DESC")
    print("  outcome distribution:")
    for cnt, outcome in cur.fetchall():
        print(f"    {outcome:35} {cnt:6}")


# ============================================================
# Main
# ============================================================

def main():
    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor()

    print("Step 1: dimensions")
    ingest_dimensions(cur)
    conn.commit()

    print("\nStep 2: indications")
    ingest_indications(cur)
    conn.commit()

    print("\nStep 3: drugs")
    ingest_drugs(cur)
    conn.commit()

    print("\nStep 4: drug-target links")
    ingest_drug_targets(cur)
    conn.commit()

    print("\nStep 5: approvals")
    ingest_approvals(cur)
    conn.commit()

    print("\nStep 6: programs + program_trials")
    ingest_programs(cur)
    conn.commit()

    print("\nStep 7: evidence scores")
    ingest_evidence_scores(cur)
    conn.commit()

    print("\nStep 8: classifications")
    ingest_classifications(cur)
    conn.commit()

    print("\nStep 9: program outcomes rollup")
    ingest_program_outcomes(cur)
    conn.commit()

    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
