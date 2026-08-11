"""LLM classifiers that produce the JSONL inputs consumed by db/02_ingest.py.

Each classifier is a standalone script: it reads DB state to determine which
subjects need scoring, calls the Anthropic API with a versioned prompt, and
writes results to a JSONL file matching the schema `db/02_ingest.py` expects.

See README.md for schemas, cost estimates, and rerun procedure.
"""
