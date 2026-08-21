"""Pinned HPO ontology support for phenotypic-abnormality feature counts."""

from __future__ import annotations

import hashlib
import urllib.request
from collections import defaultdict, deque
from pathlib import Path

from psycopg2.extras import execute_values


ONTOLOGY_RELEASE = "2026-06-23"
ONTOLOGY_URL = (
    "https://github.com/obophenotype/human-phenotype-ontology/releases/"
    "download/v2026-06-23/hp.obo"
)
ONTOLOGY_SHA256 = "a5092cbdf605f568403cf7380d9173014015692433b2cc631bc5c1b053876b1b"
PHENOTYPIC_ABNORMALITY_ROOT = "HP:0000118"
EXPECTED_TERM_COUNT = 19119


def load_ontology_bytes(path: Path | None = None) -> bytes:
    """Load the pinned ontology release and verify its published digest."""
    if path is None:
        request = urllib.request.Request(
            ONTOLOGY_URL,
            headers={"User-Agent": "predictive-validity-hpo-loader/1.0"},
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = response.read()
    else:
        payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != ONTOLOGY_SHA256:
        raise ValueError(
            f"HPO ontology digest mismatch: expected {ONTOLOGY_SHA256}, got {digest}"
        )
    return payload


def phenotypic_abnormality_terms(payload: bytes) -> list[str]:
    """Return active descendants of HP:0000118 from an OBO payload."""
    terms: dict[str, dict] = {}
    block: dict | None = None

    def finish_term() -> None:
        if block and block.get("id"):
            terms[block["id"]] = block

    for line in payload.decode("utf-8").splitlines() + ["[Term]"]:
        if line == "[Term]":
            finish_term()
            block = {}
        elif line.startswith("["):
            finish_term()
            block = None
        elif block is not None:
            if line.startswith("id: HP:"):
                block["id"] = line[4:].strip()
            elif line.startswith("is_a: HP:"):
                block.setdefault("parents", set()).add(line.split()[1])
            elif line == "is_obsolete: true":
                block["obsolete"] = True

    if PHENOTYPIC_ABNORMALITY_ROOT not in terms:
        raise ValueError(f"HPO root {PHENOTYPIC_ABNORMALITY_ROOT} is missing")

    children: dict[str, set[str]] = defaultdict(set)
    for term_id, term in terms.items():
        for parent in term.get("parents", ()):
            children[parent].add(term_id)

    descendants: set[str] = set()
    queue = deque([PHENOTYPIC_ABNORMALITY_ROOT])
    while queue:
        for child in children[queue.popleft()]:
            if child not in descendants:
                descendants.add(child)
                queue.append(child)

    active = sorted(
        term_id
        for term_id in descendants
        if not terms.get(term_id, {}).get("obsolete", False)
    )
    if len(active) != EXPECTED_TERM_COUNT:
        raise ValueError(
            f"unexpected HPO phenotypic-abnormality term count: {len(active)} "
            f"(expected {EXPECTED_TERM_COUNT})"
        )
    return active


def ensure_reference_table(cur, ontology_path: Path | None = None) -> int:
    """Ensure the database contains the pinned positive branch allowlist."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS preclin.hpo_phenotypic_abnormality_term (
          hpo_id            TEXT PRIMARY KEY,
          ontology_release  TEXT NOT NULL,
          ontology_sha256   TEXT NOT NULL,
          branch_root       TEXT NOT NULL,
          loaded_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    if ontology_path is None:
        cur.execute(
            """
            SELECT count(*), min(ontology_release), max(ontology_release),
                   min(ontology_sha256), max(ontology_sha256),
                   min(branch_root), max(branch_root)
            FROM preclin.hpo_phenotypic_abnormality_term
            """
        )
        row = cur.fetchone()
        if row == (
            EXPECTED_TERM_COUNT,
            ONTOLOGY_RELEASE,
            ONTOLOGY_RELEASE,
            ONTOLOGY_SHA256,
            ONTOLOGY_SHA256,
            PHENOTYPIC_ABNORMALITY_ROOT,
            PHENOTYPIC_ABNORMALITY_ROOT,
        ):
            return EXPECTED_TERM_COUNT

    terms = phenotypic_abnormality_terms(load_ontology_bytes(ontology_path))
    cur.execute("DELETE FROM preclin.hpo_phenotypic_abnormality_term")
    execute_values(
        cur,
        """
        INSERT INTO preclin.hpo_phenotypic_abnormality_term
          (hpo_id, ontology_release, ontology_sha256, branch_root)
        VALUES %s
        """,
        [
            (
                term_id,
                ONTOLOGY_RELEASE,
                ONTOLOGY_SHA256,
                PHENOTYPIC_ABNORMALITY_ROOT,
            )
            for term_id in terms
        ],
        page_size=2000,
    )
    return len(terms)
