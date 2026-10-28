"""DataLoom agent worker (Phase 3).

LangGraph graphs, a swappable LLM adapter, read-only tools and prompt builders.
Nothing in this package is allowed to import ``app`` from ``apps/api`` or
``apps/worker`` (modular monolith, ADR-001): the only shared code is
``dataloom_contracts``.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
