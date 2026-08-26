"""Load a versioned ChEMBL first-approval map into PostgreSQL.

The local ``preclin.approval`` table begins in 2015 and cannot identify a
target's historical first validation.  This helper resolves the analysis
universe to ChEMBL single-protein targets through UniProt accessions, joins
approved mechanisms to molecule ``first_approval`` years, and atomically
stores the normalized mappings and events in ``preclin.*``.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, execute_values

from approval_research_event_study import UNIVERSE_SQL, direct_database_url


BASE_URL = "https://www.ebi.ac.uk/chembl/api/data"
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--input-json",
        type=Path,
        help="Import a legacy snapshot instead of fetching the current release",
    )
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def request_json(resource: str, params: dict | None = None, attempts: int = 5):
    query = urllib.parse.urlencode(params or {})
    url = f"{BASE_URL}/{resource}.json" + (f"?{query}" if query else "")
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "predictive-validity-research/1.0"}
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except Exception:
            if attempt == attempts - 1:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def fetch_pages(resource: str, params: dict, collection_key: str) -> list[dict]:
    offset = 0
    rows: list[dict] = []
    while True:
        page_params = dict(params)
        page_params.update({"limit": 1000, "offset": offset})
        payload = request_json(resource, page_params)
        rows.extend(payload.get(collection_key) or [])
        page = payload["page_meta"]
        if not page.get("next"):
            return rows
        offset += int(page["limit"])


def batches(values, size):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start : start + size]


def load_universe_targets():
    conn = psycopg2.connect(direct_database_url(os.environ["DATABASE_URL"]))
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"WITH universe AS ({UNIVERSE_SQL.rstrip().rstrip(';')}) "
                "SELECT u.target_id, u.symbol FROM universe u ORDER BY u.target_id"
            )
            return cur.fetchall()
    finally:
        conn.close()


def write_database(payload: dict, dry_run: bool) -> None:
    version = payload["chembl_db_version"]
    mappings = [
        (version, row["target_id"], row["symbol"], target_chembl_id)
        for row in payload["targets"]
        for target_chembl_id in row["chembl_target_ids"]
    ]
    # ChEMBL occasionally returns byte-identical mechanism records more than
    # once. The database stores each distinct mechanism only once.
    events = sorted(
        {
            (
                version,
                row["target_id"],
                event["target_chembl_id"],
                event["molecule_chembl_id"],
                event.get("molecule_name"),
                int(event["first_approval"]),
                event.get("action_type"),
                event.get("mechanism_of_action"),
            )
            for row in payload["targets"]
            for event in row["supporting_mechanisms"]
        },
        key=lambda row: tuple("" if value is None else str(value) for value in row),
    )

    database_url = direct_database_url(os.environ["DATABASE_URL"])
    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO preclin.chembl_target_approval_release
                  (chembl_db_version, release_date, source, source_url,
                   mapping_policy, source_audit, imported_at)
                VALUES (%s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (chembl_db_version) DO UPDATE SET
                  release_date = EXCLUDED.release_date,
                  source = EXCLUDED.source,
                  source_url = EXCLUDED.source_url,
                  mapping_policy = EXCLUDED.mapping_policy,
                  source_audit = EXCLUDED.source_audit,
                  imported_at = now()
                """,
                (
                    version,
                    payload["chembl_release_date"],
                    payload["source"],
                    payload["source_url"],
                    payload["mapping_policy"],
                    Json(payload["audit"]),
                ),
            )
            cur.execute(
                "DELETE FROM preclin.chembl_target_approval_event "
                "WHERE chembl_db_version = %s",
                (version,),
            )
            cur.execute(
                "DELETE FROM preclin.chembl_target_mapping "
                "WHERE chembl_db_version = %s",
                (version,),
            )
            execute_values(
                cur,
                """
                INSERT INTO preclin.chembl_target_mapping
                  (chembl_db_version, target_id, target_symbol, target_chembl_id)
                VALUES %s
                """,
                mappings,
            )
            execute_values(
                cur,
                """
                INSERT INTO preclin.chembl_target_approval_event
                  (chembl_db_version, target_id, target_chembl_id,
                   molecule_chembl_id, molecule_name, first_approval_year,
                   action_type, mechanism_of_action)
                VALUES %s
                ON CONFLICT DO NOTHING
                """,
                events,
            )
            cur.execute(
                """
                SELECT count(*), count(*) FILTER (WHERE first_approval_year IS NOT NULL),
                       sum(supporting_mechanism_count)
                FROM preclin.v_chembl_target_first_approval
                WHERE chembl_db_version = %s
                """,
                (version,),
            )
            mapped_targets, approved_targets, event_count = cur.fetchone()

        if dry_run:
            conn.rollback()
            disposition = "validated and rolled back"
        else:
            conn.commit()
            disposition = "committed"
        print(
            f"{disposition} {version}: {mapped_targets} mapped targets, "
            f"{approved_targets} with approval histories, "
            f"{event_count} distinct supporting mechanisms"
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    args = parse_args()
    if args.input_json is not None:
        payload = json.loads(args.input_json.read_text())
        write_database(payload, args.dry_run)
        return

    status = request_json("status")
    universe = load_universe_targets()
    symbol_to_local = {
        symbol: (int(target_id), symbol)
        for target_id, symbol in universe
    }

    chembl_target_to_local: dict[str, tuple[int, str]] = {}
    queried_symbols = 0
    for batch in batches(sorted(symbol_to_local), args.batch_size):
        rows = fetch_pages(
            "target",
            {
                "target_components__target_component_synonyms__component_synonym__in": ",".join(batch),
                "only": "target_chembl_id,target_type,organism,target_components",
            },
            "targets",
        )
        queried_symbols += len(batch)
        for row in rows:
            if row.get("organism") != "Homo sapiens":
                continue
            if row.get("target_type") != "SINGLE PROTEIN":
                continue
            components = row.get("target_components") or []
            gene_symbols = {
                synonym.get("component_synonym")
                for component in components
                for synonym in (component.get("target_component_synonyms") or [])
                if synonym.get("syn_type") == "GENE_SYMBOL"
                and synonym.get("component_synonym") in symbol_to_local
            }
            if len(gene_symbols) != 1:
                continue
            symbol = next(iter(gene_symbols))
            chembl_target_to_local[row["target_chembl_id"]] = symbol_to_local[
                symbol
            ]
        print(
            f"target mapping: queried_symbols={queried_symbols}/{len(universe)} "
            f"mapped={len(chembl_target_to_local)}",
            flush=True,
        )

    mechanisms: list[dict] = []
    for batch in batches(sorted(chembl_target_to_local), args.batch_size):
        mechanisms.extend(
            fetch_pages(
                "mechanism",
                {
                    "target_chembl_id__in": ",".join(batch),
                    "max_phase": 4,
                    "only": (
                        "molecule_chembl_id,target_chembl_id,action_type,"
                        "direct_interaction,mechanism_of_action,max_phase"
                    ),
                },
                "mechanisms",
            )
        )
    direct_mechanisms = [row for row in mechanisms if row.get("direct_interaction") == 1]
    molecule_ids = sorted(
        {row["molecule_chembl_id"] for row in direct_mechanisms}
    )
    print(
        f"approved mechanisms={len(mechanisms)} direct={len(direct_mechanisms)} "
        f"molecules={len(molecule_ids)}",
        flush=True,
    )

    molecules: dict[str, dict] = {}
    for batch in batches(molecule_ids, 50):
        rows = fetch_pages(
            "molecule",
            {
                "molecule_chembl_id__in": ",".join(batch),
                "only": "molecule_chembl_id,pref_name,first_approval,max_phase",
            },
            "molecules",
        )
        molecules.update({row["molecule_chembl_id"]: row for row in rows})

    target_events: dict[int, list[dict]] = defaultdict(list)
    for mechanism in direct_mechanisms:
        molecule = molecules.get(mechanism["molecule_chembl_id"])
        target = chembl_target_to_local.get(mechanism["target_chembl_id"])
        if molecule is None or target is None or molecule.get("first_approval") is None:
            continue
        target_id, symbol = target
        target_events[target_id].append(
            {
                "molecule_chembl_id": molecule["molecule_chembl_id"],
                "molecule_name": molecule.get("pref_name"),
                "first_approval": int(molecule["first_approval"]),
                "target_chembl_id": mechanism["target_chembl_id"],
                "action_type": mechanism.get("action_type"),
                "mechanism_of_action": mechanism.get("mechanism_of_action"),
                "symbol": symbol,
            }
        )

    local_target_mappings: dict[int, dict] = {}
    for chembl_target_id, (target_id, symbol) in chembl_target_to_local.items():
        item = local_target_mappings.setdefault(
            target_id,
            {"target_id": target_id, "symbol": symbol, "chembl_target_ids": []},
        )
        item["chembl_target_ids"].append(chembl_target_id)

    targets = []
    for target_id, mapping in sorted(local_target_mappings.items()):
        events = target_events.get(target_id, [])
        events.sort(key=lambda row: (row["first_approval"], row["molecule_chembl_id"]))
        targets.append(
            {
                "target_id": target_id,
                "symbol": mapping["symbol"],
                "chembl_target_ids": sorted(mapping["chembl_target_ids"]),
                "first_approval_year": (
                    events[0]["first_approval"] if events else None
                ),
                "supporting_mechanisms": events,
            }
        )

    payload = {
        "source": "ChEMBL Data Web Services",
        "source_url": BASE_URL,
        "chembl_db_version": status.get("chembl_db_version"),
        "chembl_release_date": status.get("chembl_release_date"),
        "mapping_policy": (
            "Homo sapiens SINGLE PROTEIN target mapped by exact ChEMBL "
            "GENE_SYMBOL synonym; "
            "approved max_phase=4 mechanism with direct_interaction=1; target "
            "event is minimum molecule first_approval"
        ),
        "audit": {
            "dated_clinical_targets_with_symbols": len(universe),
            "mapped_single_protein_chembl_targets": len(chembl_target_to_local),
            "mapped_local_targets": len(local_target_mappings),
            "approved_mechanisms": len(mechanisms),
            "direct_approved_mechanisms": len(direct_mechanisms),
            "approved_molecules_with_direct_mechanisms": len(molecule_ids),
            "local_targets_with_first_approval": len(target_events),
        },
        "targets": targets,
    }
    write_database(payload, args.dry_run)


if __name__ == "__main__":
    main()
