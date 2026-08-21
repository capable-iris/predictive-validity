#!/usr/bin/env python3
"""Validate target-report JSON and the rendered two-page PDF."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


REQUIRED_TOP = {
    "target",
    "assessment",
    "phenotypes",
    "modalities",
    "candidates",
    "in_vitro_assays",
    "in_vivo_assays",
    "mechanism",
    "sources",
}
ROW_LIMITS = {
    "phenotypes": (1, 7),
    "modalities": (1, 3),
    "candidates": (0, 8),
    "in_vitro_assays": (1, 3),
    "in_vivo_assays": (1, 3),
    "sources": (1, 40),
}
CATEGORIES = {"Genetic", "Human PD", "Animal", "Cell", "Mechanistic"}
CONFIDENCE = {"Low", "Moderate", "High"}
DIRECTNESS = {"direct", "claimed direct", "indirect", "disputed"}
MODULATIONS = {
    "agonism",
    "antagonism",
    "activation",
    "inhibition",
    "loss of function",
    "gain of function",
    "mixed",
    "unclear",
}
EFFECT_DIRECTIONS = {"increase", "decrease", "mixed", "unclear", "no change"}
BAD_DASHES = {"\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212"}


class ReportValidationError(ValueError):
    pass


def _require_dict(value: Any, path: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return {}
    return value


def _require_list(value: Any, path: str, errors: list[str]) -> list[Any]:
    if not isinstance(value, list):
        errors.append(f"{path} must be an array")
        return []
    return value


def _text(
    obj: dict[str, Any], key: str, path: str, errors: list[str], max_len: int
) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path}.{key} must be a non-empty string")
        return ""
    if len(value) > max_len:
        errors.append(f"{path}.{key} exceeds {max_len} characters")
    if any(char in value for char in BAD_DASHES):
        errors.append(f"{path}.{key} contains a non-ASCII dash")
    return value


def _keys(obj: dict[str, Any], required: set[str], path: str, errors: list[str]) -> None:
    missing = sorted(required - set(obj))
    if missing:
        errors.append(f"{path} missing fields: {', '.join(missing)}")


def _source_refs(
    obj: dict[str, Any], path: str, known_sources: set[str], errors: list[str]
) -> None:
    refs = _require_list(obj.get("sources"), f"{path}.sources", errors)
    if not refs:
        errors.append(f"{path}.sources must contain at least one source ID")
    for ref in refs:
        if ref not in known_sources:
            errors.append(f"{path}.sources contains unknown source ID {ref!r}")


def _string_list(
    obj: dict[str, Any], key: str, path: str, errors: list[str], max_items: int, max_len: int
) -> list[str]:
    values = _require_list(obj.get(key), f"{path}.{key}", errors)
    if len(values) > max_items:
        errors.append(f"{path}.{key} has more than {max_items} items")
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{path}.{key}[{index}] must be a non-empty string")
        elif len(value) > max_len:
            errors.append(f"{path}.{key}[{index}] exceeds {max_len} characters")
    return values


def validate_data(data: Any) -> dict[str, Any]:
    errors: list[str] = []
    root = _require_dict(data, "report", errors)
    _keys(root, REQUIRED_TOP, "report", errors)

    for key, (minimum, maximum) in ROW_LIMITS.items():
        rows = _require_list(root.get(key), key, errors)
        if not minimum <= len(rows) <= maximum:
            errors.append(f"{key} must contain {minimum}-{maximum} rows")

    source_rows = _require_list(root.get("sources"), "sources", errors)
    source_ids: list[str] = []
    source_types: dict[str, str] = {}
    for index, raw in enumerate(source_rows):
        path = f"sources[{index}]"
        row = _require_dict(raw, path, errors)
        _keys(row, {"id", "citation", "url", "type"}, path, errors)
        source_id = _text(row, "id", path, errors, 8)
        _text(row, "citation", path, errors, 180)
        url = _text(row, "url", path, errors, 500)
        source_type = _text(row, "type", path, errors, 40)
        if source_id and not re.fullmatch(r"S[1-9][0-9]?", source_id):
            errors.append(f"{path}.id must match S1-S99")
        if url and urlparse(url).scheme not in {"http", "https"}:
            errors.append(f"{path}.url must be HTTP(S)")
        source_ids.append(source_id)
        if source_id:
            source_types[source_id] = source_type.lower()
    if len(source_ids) != len(set(source_ids)):
        errors.append("sources contains duplicate IDs")
    known_sources = set(source_ids)

    target = _require_dict(root.get("target"), "target", errors)
    _keys(target, {"symbol", "name", "aliases", "indication", "as_of"}, "target", errors)
    _text(target, "symbol", "target", errors, 30)
    _text(target, "name", "target", errors, 100)
    _text(target, "indication", "target", errors, 100)
    as_of = _text(target, "as_of", "target", errors, 10)
    try:
        if as_of:
            date.fromisoformat(as_of)
    except ValueError:
        errors.append("target.as_of must be YYYY-MM-DD")
    _string_list(target, "aliases", "target", errors, 8, 40)

    assessment = _require_dict(root.get("assessment"), "assessment", errors)
    _keys(
        assessment,
        {"verdict", "confidence", "opportunity", "key_risk", "limitations"},
        "assessment",
        errors,
    )
    _text(assessment, "verdict", "assessment", errors, 220)
    confidence = _text(assessment, "confidence", "assessment", errors, 12)
    if confidence and confidence not in CONFIDENCE:
        errors.append("assessment.confidence must be Low, Moderate, or High")
    _text(assessment, "opportunity", "assessment", errors, 170)
    _text(assessment, "key_risk", "assessment", errors, 170)
    _string_list(assessment, "limitations", "assessment", errors, 3, 150)

    for index, raw in enumerate(_require_list(root.get("phenotypes"), "phenotypes", errors)):
        path = f"phenotypes[{index}]"
        row = _require_dict(raw, path, errors)
        _keys(
            row,
            {"phenotype", "modulation", "effect_direction", "effect", "category", "score", "evidence", "tissue", "sources"},
            path,
            errors,
        )
        for key, limit in (("phenotype", 80), ("effect", 110), ("evidence", 190), ("tissue", 100)):
            _text(row, key, path, errors, limit)
        modulation = _text(row, "modulation", path, errors, 30).lower()
        if modulation and modulation not in MODULATIONS:
            errors.append(f"{path}.modulation is not an allowed direction")
        effect_direction = _text(row, "effect_direction", path, errors, 20).lower()
        if effect_direction and effect_direction not in EFFECT_DIRECTIONS:
            errors.append(f"{path}.effect_direction must be one of {sorted(EFFECT_DIRECTIONS)}")
        category = _text(row, "category", path, errors, 20)
        if category and category not in CATEGORIES:
            errors.append(f"{path}.category must be one of {sorted(CATEGORIES)}")
        if not isinstance(row.get("score"), int) or not 0 <= row.get("score", -1) <= 3:
            errors.append(f"{path}.score must be an integer from 0 to 3")
        _source_refs(row, path, known_sources, errors)

    for index, raw in enumerate(_require_list(root.get("modalities"), "modalities", errors)):
        path = f"modalities[{index}]"
        row = _require_dict(raw, path, errors)
        _keys(row, {"modality", "rank", "pros", "cons", "sources"}, path, errors)
        if "examples" in row:
            errors.append(f"{path}.examples is not allowed; fold named program results into pros/cons")
        _text(row, "modality", path, errors, 80)
        if not isinstance(row.get("rank"), int) or not 1 <= row.get("rank", 0) <= 3:
            errors.append(f"{path}.rank must be an integer from 1 to 3")
        for key in ("pros", "cons"):
            values = _string_list(row, key, path, errors, 3, 150)
            if not values:
                errors.append(f"{path}.{key} must contain at least one item")
        _source_refs(row, path, known_sources, errors)

    phenotype_categories = {
        row.get("category") for row in root.get("phenotypes", []) if isinstance(row, dict)
    }
    if "Human PD" not in phenotype_categories:
        errors.append("phenotypes must include a Human PD row, using score 0 when no engagement is verified")

    for index, raw in enumerate(_require_list(root.get("candidates"), "candidates", errors)):
        path = f"candidates[{index}]"
        row = _require_dict(raw, path, errors)
        required = {"name", "modality", "sponsor", "route", "directness", "indication", "status", "reason", "sources"}
        _keys(row, required, path, errors)
        for key, limit in (
            ("name", 70), ("modality", 55), ("sponsor", 70), ("route", 45),
            ("indication", 65), ("status", 120), ("reason", 140),
        ):
            _text(row, key, path, errors, limit)
        directness = _text(row, "directness", path, errors, 20).lower()
        if directness and directness not in DIRECTNESS:
            errors.append(f"{path}.directness must be one of {sorted(DIRECTNESS)}")
        _source_refs(row, path, known_sources, errors)
        patent_text = " ".join(
            str(row.get(key, "")) for key in ("name", "modality", "status", "reason")
        ).lower()
        if "patent-only" in patent_text:
            if "no active program verified" not in patent_text:
                errors.append(f"{path} patent-only row must state 'No active program verified'")
            if directness == "direct":
                errors.append(f"{path} patent-only row cannot use directness 'direct' without separate verification")
            refs = row.get("sources", [])
            if not any("patent" in source_types.get(ref, "") for ref in refs):
                errors.append(f"{path} patent-only row must cite a patent source")
            if not re.search(r"\b(?:WO|US|EP|JP|CN)\d{6,}[A-Z]?\d?\b", patent_text, re.IGNORECASE):
                errors.append(f"{path} patent-only row must name a representative publication or grant number")

    assay_required = {
        "method", "assay", "measured", "mechanism_link", "positive_readout",
        "negative_readout", "model", "model_availability_score",
        "model_availability", "setup_difficulty_score", "setup",
        "phase2_precedent", "sources",
    }
    for table_name in ("in_vitro_assays", "in_vivo_assays"):
        for index, raw in enumerate(_require_list(root.get(table_name), table_name, errors)):
            path = f"{table_name}[{index}]"
            row = _require_dict(raw, path, errors)
            _keys(row, assay_required, path, errors)
            for key, limit in (
                ("method", 70), ("assay", 100), ("model", 100),
                ("measured", 180), ("mechanism_link", 180),
                ("positive_readout", 180), ("negative_readout", 180),
                ("model_availability", 180), ("setup", 190),
                ("phase2_precedent", 190),
            ):
                _text(row, key, path, errors, limit)
            availability_score = row.get("model_availability_score")
            if not isinstance(availability_score, int) or not 0 <= availability_score <= 3:
                errors.append(f"{path}.model_availability_score must be an integer from 0 to 3")
            setup_score = row.get("setup_difficulty_score")
            if not isinstance(setup_score, int) or not 0 <= setup_score <= 3:
                errors.append(f"{path}.setup_difficulty_score must be an integer from 0 to 3")
            availability_text = str(row.get("model_availability", ""))
            if availability_score == 3 and not re.search(r"(?:stock|catalog|cat\.?|#)\s*[:#]?\s*[A-Z0-9-]+", availability_text, re.IGNORECASE):
                errors.append(f"{path}.model_availability must give a stock/catalog identifier for score 3")
            _source_refs(row, path, known_sources, errors)

    mechanism = _require_dict(root.get("mechanism"), "mechanism", errors)
    _keys(mechanism, {"caption", "nodes", "edges"}, "mechanism", errors)
    _text(mechanism, "caption", "mechanism", errors, 190)
    nodes = _require_list(mechanism.get("nodes"), "mechanism.nodes", errors)
    edges = _require_list(mechanism.get("edges"), "mechanism.edges", errors)
    if not 3 <= len(nodes) <= 8:
        errors.append("mechanism.nodes must contain 3-8 nodes")
    if not 2 <= len(edges) <= 10:
        errors.append("mechanism.edges must contain 2-10 edges")
    node_ids: list[str] = []
    for index, raw in enumerate(nodes):
        path = f"mechanism.nodes[{index}]"
        node = _require_dict(raw, path, errors)
        _keys(node, {"id", "label", "x", "y", "sources"}, path, errors)
        node_ids.append(_text(node, "id", path, errors, 16))
        _text(node, "label", path, errors, 70)
        for coord in ("x", "y"):
            value = node.get(coord)
            if not isinstance(value, (int, float)) or not 0 <= value <= 1:
                errors.append(f"{path}.{coord} must be a number from 0 to 1")
        _source_refs(node, path, known_sources, errors)
    if len(node_ids) != len(set(node_ids)):
        errors.append("mechanism.nodes contains duplicate IDs")
    known_nodes = set(node_ids)
    for index, raw in enumerate(edges):
        path = f"mechanism.edges[{index}]"
        edge = _require_dict(raw, path, errors)
        _keys(edge, {"from", "to", "label", "sources"}, path, errors)
        from_id = _text(edge, "from", path, errors, 16)
        to_id = _text(edge, "to", path, errors, 16)
        _text(edge, "label", path, errors, 45)
        if from_id not in known_nodes or to_id not in known_nodes:
            errors.append(f"{path} references an unknown node")
        if from_id == to_id:
            errors.append(f"{path} cannot be a self-edge")
        _source_refs(edge, path, known_sources, errors)

    if errors:
        raise ReportValidationError("\n".join(f"- {error}" for error in errors))
    return root


def validate_pdf(path: Path) -> None:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ReportValidationError("pypdf is required for PDF validation") from exc

    if not path.is_file() or path.stat().st_size < 5_000:
        raise ReportValidationError(f"PDF missing or unexpectedly small: {path}")
    reader = PdfReader(str(path))
    if len(reader.pages) != 3:
        raise ReportValidationError(f"PDF must contain 2 analytical pages plus 1 source appendix; found {len(reader.pages)} pages")
    page_text = [page.extract_text() or "" for page in reader.pages]
    extracted = "\n".join(page_text)
    markers = ["Phenotype evidence", "Modality strategy", "Candidate landscape", "In vitro assays", "In vivo assays", "Mechanism", "Sources"]
    missing = [marker for marker in markers if marker not in extracted]
    if missing:
        raise ReportValidationError(f"PDF text is missing sections: {', '.join(missing)}")
    if "Sources" in page_text[1] or "Sources" not in page_text[2]:
        raise ReportValidationError("Sources must appear on page 3, not analytical page 2")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path, help="Report JSON")
    parser.add_argument("--pdf", type=Path, help="Rendered PDF to verify")
    args = parser.parse_args()
    if not args.input and not args.pdf:
        parser.error("provide report JSON and/or --pdf")
    try:
        if args.input:
            with args.input.open(encoding="utf-8") as handle:
                validate_data(json.load(handle))
            print(f"JSON valid: {args.input}")
        if args.pdf:
            validate_pdf(args.pdf)
            print(f"PDF valid: {args.pdf}")
    except (OSError, json.JSONDecodeError, ReportValidationError) as exc:
        print(f"Validation failed:\n{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
