# Arsitektur DataLoom

DataLoom adalah modular monolith dengan empat proses. Tidak ada microservice.

```text
apps/web      Next.js App Router, UI dan mode demo in-browser
apps/api      FastAPI: auth, RBAC, CRUD, ingest, SSE, query read-only
apps/worker   Redis Streams consumer, pipeline runner, gate, karantina
apps/agents   LangGraph: profiler, builder, guardian, text-to-SQL, self-healing
packages/contracts  Pydantic, satu sumber kebenaran bentuk data
infra/        Docker Compose, Caddy, Prometheus
tests/        unit, integrasi, isolasi tenant, recovery
```

## Batas kepercayaan

- Browser hanya berbicara ke `/v1` lewat `apps/web/lib/api.ts`.
- API memegang sesi, RBAC, isolasi tenant, dan audit.
- Worker memproses data tanpa LLM.
- Agent hanya membaca sampel, mengusulkan, dan menunggu approval manusia.
- Object storage menyimpan Parquet. Gold memakai DuckDB.
- Mode demo berhenti di `apps/web/lib/demo/` dan tidak menyentuh jaringan.

## Alur ingest

1. `POST /v1/ingest/{source_id}` dengan header `X-API-Key`.
2. API memeriksa ukuran, bentuk payload, laju request, dan status key.
3. API menulis batch ke Redis Stream.
4. Worker membaca batch, menulis Bronze Parquet, lalu mengakui pesan.
5. Silver menormalkan tipe, null, duplikat, dan aturan validasi.
6. Baris yang gagal masuk Quarantine dengan `_reason_code`.
7. Gold dibentuk hanya jika gate lulus.
8. API menyajikan metadata, sampel, lineage, dan status run.

## Lapisan data

| Lapisan | Penulis | Format | Invarian |
| --- | --- | --- | --- |
| Bronze | Worker | Parquet | append-only, metadata sumber dan batch |
| Silver | Worker | Parquet | tipe benar, null ternormalisasi, dedup, validasi |
| Quarantine | Worker | Parquet | `_reason_code`, `_reason_detail`, `_row_id` |
| Gold | Worker | DuckDB dan Parquet | hanya setelah gate lulus, direkonsiliasi |

Worker mendaftarkan Silver, Gold, dan Karantina ke tabel `datasets`. API
membaca Gold dari DuckDB dan lapisan Parquet dari object storage, dengan batas
1.000 baris dan pemeriksaan prefix `org/{org_id}/` sebelum membaca objek.

## Gate kualitas

Empat aturan, dievaluasi kode:

1. `schema_valid`
2. `quarantine_ratio`
3. `reconciliation`
4. `pending_proposals`

Gagal satu aturan blocking berarti Gold tidak dipromosikan. Setiap aturan
menyimpan bukti yang ditampilkan di monitor run dan Penjelajah Data.

## Agent

- Output wajib Pydantic.
- Retry terbatas, timeout per node dan per run.
- Budget guard sebelum panggilan LLM.
- Usulan tidak pernah langsung mengubah data.
- Usulan destruktif wajib diputuskan satu per satu.
- `insufficient_evidence` adalah hasil yang sah, bukan error yang disembunyikan.

## Keamanan

- Password Argon2id.
- Sesi HttpOnly dan CSRF double-submit.
- API key disimpan sebagai hash, plaintext hanya tampil sekali.
- `org_id` tidak pernah diambil dari body atau query.
- RLS sebagai lapisan kedua setelah filter aplikasi.
- Query user hanya SELECT, allowlist tabel, tanpa fungsi file atau jaringan.
- Audit log mencatat aksi sensitif.

## Deployment

`infra/docker-compose.yml` menjalankan `api`, `worker`, `web`, `redis`, dan
opsional `agents`. Postgres dan object storage bersifat eksternal. Satu file
`.env` di root dipakai oleh semua service.

## Observabilitas

- Log run per node.
- SSE untuk status, log, dan gate.
- Metrik Prometheus untuk API, worker, dan antrean.
- Catatan `llm_calls` untuk token, biaya, dan latency.