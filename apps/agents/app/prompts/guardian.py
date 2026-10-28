"""A3 Data Quality Guardian prompt (PRD A3).

The LLM's ONLY job here is to write one human-readable sentence explaining a
failure pattern that was already detected by deterministic code. It never
decides pass/fail and its output is not parsed into any gate.
"""

from __future__ import annotations

import json
from typing import Any

from app.llm.provider import ChatMessage
from app.prompts.personas import PERSONA_QUALITY_GUARDIAN


def build_system_prompt() -> str:
    return "\n".join(
        [
            PERSONA_QUALITY_GUARDIAN.strip(),
            "",
            "# Tugas",
            "Jelaskan pola kegagalan kualitas data yang SUDAH dihitung oleh kode.",
            "Kamu tidak memutuskan lulus/gagal. Keputusan itu milik kode deterministik.",
            "",
            "Balas HANYA JSON:",
            '{"explanation": "<maks 3 kalimat Bahasa Indonesia>", "suggested_actions": ["<aksi singkat>"]}',
        ]
    )


def build_guardian_messages(
    *,
    reasons: dict[str, int],
    rows_in: int,
    rows_out: int,
    rows_quarantined: int,
    sample_details: list[dict[str, Any]] | None = None,
) -> list[ChatMessage]:
    """Explain deterministic quarantine statistics (no raw data cells)."""
    payload = {
        "rows_in": rows_in,
        "rows_out": rows_out,
        "rows_quarantined": rows_quarantined,
        "reasons": reasons,
        "sample_details": (sample_details or [])[:10],
    }
    return [
        ChatMessage(role="system", content=build_system_prompt()),
        ChatMessage(
            role="user",
            content="Statistik karantina:\n" + json.dumps(payload, ensure_ascii=False, indent=2),
        ),
    ]


__all__ = ["build_guardian_messages", "build_system_prompt"]
