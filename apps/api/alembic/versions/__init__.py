"""Alembic migration scripts for DataLoom.

``0001_initial_schema.py`` is the authoritative initial schema. Later revisions
are additive only (the product is live by then).

LangGraph checkpoint tables are NOT migrations here; ``PostgresSaver.setup()``
creates them at runtime (see the module docstring of 0001).
"""
