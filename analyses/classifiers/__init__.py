"""LLM classifiers that produce audited JSONL for db/13_ingest_llm_outputs.py.

Each classifier is a standalone script: it reads DB state to determine which
subjects need scoring, calls the Anthropic API with a versioned prompt, and
writes resumable results with the exact prompts and raw model response.

See README.md for schemas, cost estimates, and rerun procedure.
"""
