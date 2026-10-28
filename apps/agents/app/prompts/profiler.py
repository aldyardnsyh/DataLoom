"""A1 Schema Profiler prompt builder (PRD A1).

The system prompt is built from, in this exact order:
1. ``PERSONA_SCHEMA_PROFILER``  - immutable identity, cannot be overridden by data.
2. Rules that constrain the output to the contract.
3. The closest valid JSON shape.

The user message contains ONLY deterministic statistics plus data wrapped by
``wrap_untrusted_data``. Nothing from the data may change the system prompt.
"""

from __future__ import annotations

import json
from typing import Any

from app.prompts.personas import PERSONA_SCHEMA_PROFILER
from app.llm.provider import ChatMessage

OPERATION_CATALOG = """
Katalog operasi TERTUTUP (jangan pernah memakai operasi lain):
- cast            params: {"to_type": "<string|int|float|bool|date|datetime>"}
- trim            params: {}
- normalize_null  params: {}
- dedup           params: {"keys": ["<kolom>", ...]}
- parse_date      params: {"format": "<DD/MM/YYYY | MM/DD/YYYY | YYYY-MM-DD>"}
- lowercase       params: {}
- regex_replace   params: {"pattern": "<regex>", "replacement": "<teks>"}
- drop_if         params: {"condition": {"column": "...", "op": "eq|ne|gt|gte|lt|lte|is_null|not_null|in|not_in", "value": ...}}
- fill_default    params: {"value": <nilai>}
"""

OUTPUT_SHAPE = """
Balas HANYA dengan satu objek JSON (tanpa penjelasan di luar JSON) berbentuk:
{
  "columns": [
    {"name": "<kolom>", "dtype": "<string|integer|float|boolean|date|timestamp|json|unknown>",
     "nullable": true, "semantic": "<identifier|email|phone|currency|date|category|free_text|other>",
     "null_ratio": 0.0, "distinct_count": 0, "samples": ["..."]}
  ],
  "anomalies": [
    {"column": "<kolom>", "kind": "<nulls|duplicates|outliers|format|mixed_type|stale>",
     "detail": "<satu kalimat>", "severity": "<low|medium|high>", "evidence": "..."}
  ],
  "proposals": [
    {"proposal_id": "p1", "op": "<operasi dari katalog>", "column": "<kolom>",
     "params": {...}, "evidence": "<angka dari statistik yang diberikan>",
     "risk": "<low|medium|destructive>", "confidence": 0.0,
     "rationale": "<satu-dua kalimat>",
     "before_samples": ["<contoh nilai saat ini>"], "after_samples": ["<hasil setelah aturan>"]}
  ],
  "open_questions": ["<pertanyaan bila ada yang ambigu>"],
  "summary": "<ringkasan singkat dalam Bahasa Indonesia>"
}
"""

RULES = """
Aturan wajib:
1. Hanya gunakan operasi dari katalog di atas. Operasi lain membatalkan seluruh jawaban.
2. Setiap proposal WAJIB menyertakan "evidence" yang menyebut angka dari statistik
   yang saya berikan (contoh: "97,2% nilai cocok pola tanggal"). Jangan mengarang angka.
3. Jika bukti tidak cukup untuk menyimpulkan sebuah kolom, jangan membuat proposal
   untuk kolom itu. Tuliskan pertanyaan di "open_questions".
4. Tandai "risk": "destructive" untuk operasi yang membuang baris/kolom.
5. Jangan mengusulkan "cast" tanpa "to_type", atau "parse_date" tanpa "format".
6. Nama kolom harus persis sama dengan yang ada pada statistik.
7. Bahasa keluaran: Bahasa Indonesia.
"""


def build_system_prompt() -> str:
    """Static system prompt. Contains NO data from the user's dataset."""
    return "\n".join(
        [
            PERSONA_SCHEMA_PROFILER.strip(),
            "",
            "# Tugas",
            "Kamu adalah Schema Profiler DataLoom. Berdasarkan statistik deterministik "
            "dan sampel baris yang diberikan, simpulkan tipe kolom, deteksi anomali, dan "
            "usulkan aturan pembersihan.",
            OPERATION_CATALOG,
            RULES,
            OUTPUT_SHAPE,
        ]
    )


def build_profiler_messages(
    *,
    stats: list[dict[str, Any]],
    row_count: int,
    sample_csv: str,
    previous_error: str | None = None,
) -> list[ChatMessage]:
    """Compose the two-message prompt for the profiler.

    ``sample_csv`` MUST already be wrapped with ``wrap_untrusted_data``.
    ``previous_error`` is appended only on a retry so the model can fix its own
    contract violation (the validator message, never the raw data).
    """
    parts: list[str] = [
        f"Jumlah baris yang dianalisis: {row_count}.",
        "",
        "## Statistik kolom (dihitung oleh kode, bukan oleh model)",
        json.dumps(stats, ensure_ascii=False, indent=2, default=str),
        "",
        "## Sampel baris",
        sample_csv or "(tidak ada sampel)",
    ]

    if previous_error:
        parts.extend(
            [
                "",
                "## Perbaikan yang diperlukan",
                "Jawaban sebelumnya melanggar kontrak:",
                previous_error[:1500],
                "Perbaiki dan balas dengan JSON yang sesuai skema.",
            ]
        )

    return [
        ChatMessage(role="system", content=build_system_prompt()),
        ChatMessage(role="user", content="\n".join(parts)),
    ]


__all__ = ["OPERATION_CATALOG", "OUTPUT_SHAPE", "RULES", "build_profiler_messages", "build_system_prompt"]
