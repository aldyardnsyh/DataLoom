# VERIFY_POSTGRES — Verifikasi PostgresSaver Staging

Hasil lokal 2026-10-30: **SUCCEEDED** (Postgres `infra-postgres-1`, port 55434).
Test otomatis: `tests/recovery/test_healing_postgres_saver.py` (skip bila PG tidak reachable).

## Prosedur staging

1. Set env: `CHECKPOINT_DATABASE_URL=postgresql://<user>:<pass>@<host>:5432/<db>`
2. Pastikan tabel checkpoint ada: runner memanggil `saver.setup()` otomatis
   (`CHECKPOINTER_AUTOSETUP=true`), atau manual via `PostgresSaver.setup()`.
3. Jalankan: `pytest tests/recovery/test_healing_postgres_saver.py -v`
4. Ekspektasi: `1 passed` — graph berhenti di `interrupt()` (`next == ('approval',)`
   = WAITING_APPROVAL), lalu resume dengan `Command(resume=...)` menghasilkan
   state final (`SUCCEEDED`).
5. Bila PG tidak reachable: test **skip** (bukan fail) — cek konektivitas DSN.
