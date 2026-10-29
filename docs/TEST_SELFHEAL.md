# TEST_SELFHEAL — Verifikasi Self-Healing Wiring

Tanggal: 2026-10-30. Role: QA. Repo: `D:\Riset Pribadi\DataLoom`, Python 3.11.
Tidak mengubah kode sumber. Tidak menjalankan dev server.

## 0. Re-verify pasca security fix (2026-10-30)

- Healing: `apps/agents/.venv/Scripts/python.exe -m pytest apps/agents/tests/unit/test_self_healing_wiring.py apps/agents/tests/unit/test_healing_resume.py -q` → **9 passed** (workdir: repo root; venv agents wajib — python root tidak punya `langgraph`).
- Transisi: `python -m pytest tests/unit/test_transition.py` → **84 passed** (count via `--co -q`; run `-q` hijau penuh, 84 dots, 0 failed).
- `git status --short`: M apps/agents/app/graphs/self_healing.py, M apps/agents/app/prompts/healing.py, M apps/agents/app/runner.py, ?? apps/agents/tests/, ?? apps/agents/uv.lock, ?? docs/SECURITY_SELFHEAL.md, ?? docs/TEST_SELFHEAL.md.
- Kesimpulan: hijau, tidak ada regresi dari security fix di area healing/transition.

## 1. Cakupan

- Wiring self-healing graph + resume routing + apply-patch validation:
  `apps/agents/tests/unit/test_self_healing_wiring.py`,
  `apps/agents/tests/unit/test_healing_resume.py`
  (catatan: file ini TIDAK ada di `tests/unit/` root — lokasi benar di `apps/agents/tests/unit/`).
- Kontrak transisi: `tests/unit/test_transition.py` (96 tes).
- Full unit suite root `tests/unit/` sebagai baseline regresi.

## 2. Perintah

```powershell
# 1. Target healing (venv agents, butuh pytest + sqlalchemy[asyncio] + pytest-asyncio)
.\.venv\Scripts\python.exe -m pytest tests/unit/test_self_healing_wiring.py tests/unit/test_healing_resume.py -q -p no:warnings
# workdir: apps/agents

# 2. Transisi
python -m pytest tests/unit/test_transition.py -q -p no:warnings
# workdir: repo root

# 3. Full unit (4 file polars di-ignore karena polars belum terinstal di env root)
python -m pytest tests/unit -q -p no:warnings --tb=no --ignore=tests/unit/test_dataset_registry.py --ignore=tests/unit/test_parquet_preview.py --ignore=tests/unit/test_profiler_contract.py --ignore=tests/unit/test_quarantine_reasons.py

# 4. Regresi file
git status --short
```

Env note: venv `apps/agents/.venv` awalnya tanpa `pytest`/`pip`; dipasang via
`uv pip install --python .\.venv\Scripts\python.exe pytest "sqlalchemy[asyncio]" greenlet pytest-asyncio`
(hanya tooling tes, bukan kode sumber). Tes healing async gagal collect tanpa `pytest-asyncio`
("async def functions are not natively supported") — setelah dipasang, hijau.

## 3. Hasil angka

| Suite | Hasil |
|---|---|
| Healing wiring + resume (`apps/agents`, 8 tes) | **8 passed** |
| `test_transition.py` | **96 passed** (84 + 12, satu file) |
| Full `tests/unit` (minus 4 file polars) | **43 FAILED**: `test_ingest_limits.py` 20x, `test_security.py` (api_key) 23x; sisanya pass |
| Collection error (di-exclude) | 4 file: `test_dataset_registry`, `test_parquet_preview`, `test_profiler_contract`, `test_quarantine_reasons` — `ModuleNotFoundError: No module named 'polars'` |

Kegagalan `test_ingest_limits` + `test_security::test_parse_api_key_*` adalah **pre-existing**,
di luar area healing/transition, tidak tersentuh perubahan ini.

## 4. Regresi file (`git status --short`)

```text
M apps/agents/app/graphs/self_healing.py
M apps/agents/app/runner.py
?? apps/agents/tests/
?? apps/agents/uv.lock
?? docs/SECURITY_SELFHEAL.md
```

Hanya file healing + transition/runner yang berubah (modified), plus file tes baru
dan `docs/SECURITY_SELFHEAL.md` (untracked, dari lane security). Tidak ada file lain tersentuh.

## 5. Batasan (diketahui, bukan temuan baru)

1. **MemorySaver vs PostgresSaver**: resume/healing terverifikasi dengan checkpointer
   in-memory pada tes; path `PostgresSaver` (produksi) belum dieksekusi di verifikasi ini.
2. **`new_version` di API belum ada**: endpoint API untuk membuat versi baru dari hasil
   healing belum tersedia; alur berhenti di usulan patch + approval.
3. Dependensi env root belum lengkap (`polars` hilang → 4 file tidak ter-collect;
   `pytest-timeout` unknown config warning). Rekomendasi: `pip install polars pytest-timeout`
   atau bekukan `requirements-test.txt`.
