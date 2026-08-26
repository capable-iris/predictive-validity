"""Audit Phase 1+ genes absent from the 2025 IMPC summary against live IMPC.

The live endpoint currently serves IMPC Data Release 24 (published 2026-03-16).
This script is read-only for PostgreSQL and writes a target-level CSV. It does
not promote live results into evidence_score because multi-mouse-ortholog
mappings and tested-negative calls need an explicit ingestion policy.
"""

import csv
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

import psycopg2


IMPC_SOLR = "https://www.ebi.ac.uk/mi/impc/solr"
LIVE_RELEASE = "DR24"
LIVE_RELEASE_DATE = "2026-03-16"
OUTPUT = Path(__file__).resolve().parents[1] / "data" / "impc_missing_update_dr24.csv"


def _chunks(values, size=40):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _solr(core, params, attempts=4):
    url = f"{IMPC_SOLR}/{core}/select?" + urllib.parse.urlencode(params)
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return json.load(response)
        except Exception:
            if attempt == attempts - 1:
                raise
            time.sleep(2 ** attempt)


def _missing_phase1_targets():
    query = """
        WITH cohort AS (
          SELECT DISTINCT s.target_id, t.symbol
          FROM preclin.v_target_indication_strict_outcome s
          JOIN public.targets t ON t.id = s.target_id
          WHERE s.max_phase_reached >= 1
        )
        SELECT target_id, symbol
        FROM cohort c
        WHERE NOT EXISTS (
          SELECT 1
          FROM preclin.evidence_score es
          WHERE es.subject_type = 'target'
            AND es.subject_id = c.target_id
            AND es.dimension = 'impc_n_phenotypes'
            AND es.source = 'impc'
            AND es.source_version = '2025'
        )
        ORDER BY symbol
    """
    with psycopg2.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            return cur.fetchall()


def _add_facets(records, mouse_to_human, core, extra_params, facets, mode):
    for batch in _chunks(sorted(mouse_to_human)):
        params = {
            "q": "marker_symbol:(" + " OR ".join(batch) + ")",
            "rows": 0,
            "wt": "json",
            "json.facet": json.dumps({
                "genes": {
                    "type": "terms",
                    "field": "marker_symbol",
                    "limit": -1,
                    "facet": facets,
                }
            }),
        }
        params.update(extra_params)
        response = _solr(core, params)
        for bucket in response.get("facets", {}).get("genes", {}).get("buckets", []):
            mouse_symbol = bucket["val"]
            for human_symbol in mouse_to_human.get(mouse_symbol, ()):
                mouse = records[human_symbol]["mouse"][mouse_symbol]
                if mode == "significant":
                    mouse["significant_documents"] = bucket["count"]
                    mouse["distinct_significant_mp_terms"] = bucket.get("distinct_mp", 0)
                elif mode == "tested":
                    mouse["successful_statistical_results"] = bucket["count"]
                    mouse["distinct_tested_parameters"] = bucket.get("distinct_parameters", 0)
                    mouse["distinct_tested_procedures"] = bucket.get("distinct_procedures", 0)
                elif mode == "homozygous_tested":
                    mouse["homozygous_successful_results"] = bucket["count"]
                    mouse["homozygous_tested_procedures"] = bucket.get(
                        "distinct_procedures", 0
                    )


def main():
    missing = _missing_phase1_targets()
    records = {
        symbol: {"target_id": target_id, "mouse": {}}
        for target_id, symbol in missing
    }

    for batch in _chunks(list(records)):
        response = _solr("gene", {
            "q": "human_gene_symbol:(" + " OR ".join(batch) + ")",
            "rows": 500,
            "wt": "json",
            "fl": (
                "human_gene_symbol,marker_symbol,mgi_accession_id,"
                "phenotyping_data_available,assignment_status"
            ),
        })
        for document in response["response"]["docs"]:
            for human_symbol in document.get("human_gene_symbol", []):
                if human_symbol not in records:
                    continue
                records[human_symbol]["mouse"][document["marker_symbol"]] = {
                    "mgi_accession_id": document.get("mgi_accession_id"),
                    "phenotyping_data_available": bool(
                        document.get("phenotyping_data_available")
                    ),
                    "assignment_status": document.get("assignment_status"),
                    "significant_documents": 0,
                    "distinct_significant_mp_terms": 0,
                    "successful_statistical_results": 0,
                    "distinct_tested_parameters": 0,
                    "distinct_tested_procedures": 0,
                    "homozygous_successful_results": 0,
                    "homozygous_tested_procedures": 0,
                }

    mouse_to_human = {}
    for human_symbol, record in records.items():
        for mouse_symbol in record["mouse"]:
            mouse_to_human.setdefault(mouse_symbol, set()).add(human_symbol)

    _add_facets(
        records,
        mouse_to_human,
        "genotype-phenotype",
        {},
        {
            "distinct_mp": "unique(mp_term_id)",
        },
        "significant",
    )
    _add_facets(
        records,
        mouse_to_human,
        "statistical-result",
        {"fq": "status:Successful"},
        {
            "distinct_parameters": "unique(parameter_stable_id)",
            "distinct_procedures": "unique(procedure_stable_id)",
        },
        "tested",
    )
    _add_facets(
        records,
        mouse_to_human,
        "statistical-result",
        {"fq": "status:Successful AND zygosity:homozygote"},
        {
            "distinct_procedures": "unique(procedure_stable_id)",
        },
        "homozygous_tested",
    )

    fields = [
        "target_id", "human_symbol", "status", "mapping_ambiguous",
        "mouse_symbols", "mgi_accession_ids", "phenotyping_data_available",
        "distinct_significant_mp_terms_sum", "successful_statistical_results",
        "distinct_tested_parameters_max", "distinct_tested_procedures_max",
        "homozygous_tested_procedures", "eligible_observed_zero",
        "impc_release", "release_date",
    ]
    counts = {}
    with OUTPUT.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for human_symbol, record in sorted(records.items()):
            mice = record["mouse"]
            values = list(mice.values())
            significant = sum(v["distinct_significant_mp_terms"] for v in values)
            tested_results = sum(v["successful_statistical_results"] for v in values)
            phenotyping_available = any(v["phenotyping_data_available"] for v in values)
            homozygous_procedures = max(
                (v["homozygous_tested_procedures"] for v in values),
                default=0,
            )
            eligible_zero = (
                len(mice) == 1
                and phenotyping_available
                and significant == 0
                and homozygous_procedures >= 13
            )
            if not values:
                status = "no_mouse_mapping"
            elif significant:
                status = "current_significant_phenotype"
            elif phenotyping_available or tested_results:
                status = "phenotyped_no_significant_phenotype"
            else:
                status = "mapped_no_phenotyping"
            counts[status] = counts.get(status, 0) + 1
            writer.writerow({
                "target_id": record["target_id"],
                "human_symbol": human_symbol,
                "status": status,
                "mapping_ambiguous": len(mice) > 1,
                "mouse_symbols": "|".join(sorted(mice)),
                "mgi_accession_ids": "|".join(sorted(
                    v["mgi_accession_id"] for v in values
                    if v["mgi_accession_id"]
                )),
                "phenotyping_data_available": phenotyping_available,
                "distinct_significant_mp_terms_sum": significant,
                "successful_statistical_results": tested_results,
                "distinct_tested_parameters_max": max(
                    (v["distinct_tested_parameters"] for v in values),
                    default=0,
                ),
                "distinct_tested_procedures_max": max(
                    (v["distinct_tested_procedures"] for v in values),
                    default=0,
                ),
                "homozygous_tested_procedures": homozygous_procedures,
                "eligible_observed_zero": eligible_zero,
                "impc_release": LIVE_RELEASE,
                "release_date": LIVE_RELEASE_DATE,
            })

    print(f"Wrote {len(records)} rows to {OUTPUT}")
    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")


if __name__ == "__main__":
    main()
