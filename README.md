# DataLoom

Ruang kerja operator data untuk membangun, menjalankan, dan memantau pipeline
end-to-end. Data mentah masuk, dibersihkan secara deterministik, diperiksa
gerbang kualitasnya, lalu dipromosikan ke Gold hanya jika layak dipakai.

DataLoom tidak berhenti pada pembersihan kolom. Produk ini menjawab tiga hal
yang tidak diberikan formula spreadsheet atau AI cleaner:

1. **Baris ditolak dengan alasan tercatat.** Setiap baris yang gagal validasi
   masuk karantina dengan `_reason_code`, bukan dibuang diam-diam.
2. **Gerbang deterministik sebelum promosi.** Empat aturan dievaluasi kode,
   bukan opini model: skema valid, rasio karantina di bawah ambang, rekonsiliasi
   baris Silver dan Gold, dan tidak ada usulan agent yang menunggu keputusan.
3. **Kontrak data yang tercetak.** Aturan validasi dan ambang proyek dibaca di
   `/data-contract`, bukan tersembunyi di kepala seseorang.

## Verifikasi (30 Okt 2026, dijalankan nyata)

- Web: `npm run typecheck` exit 0, `npm run lint` exit 0 (no warnings/errors), `npm run build` compile + 25/25 halaman sukses.
- Backend: `pytest tests/unit` **320 passed, 0 failed** (10 profiler contract + 310 unit lain, termasuk 84 transition, 9 healing wiring/resume).
- Demo visual: suite browser 58/58 langkah lulus, `assert_no_console_errors` pass, 33 screenshot di `docs/screenshots/`.
- E2E regression tersimpan di repo: `npm run test:e2e` mencakup landing, rail 64px, run pipeline selesai dengan gate, karantina, kontrak data, notifikasi, sheet mobile.
- Self-healing: graph ter-wiring ke runner dengan approval gate manusia, tenant-scoped, cap 2 usulan per run — bukti `docs/TEST_SELFHEAL.md`, `docs/SECURITY_SELFHEAL.md` (7/7 PASS).

## Tech stack

| Lapisan | Teknologi | Versi |
| --- | --- | --- |
| Web framework | Next.js App Router | 15.5 |
| UI library | React | 19 |
| Bahasa web | TypeScript strict | 5.x |
| Styling | Tailwind CSS | 3.4 |
| Komponen | shadcn/ui, Radix UI | — |
| State server | TanStack Query | 5.90 |
| Tabel | TanStack Table | 8.21 |
| Kanvas DAG | React Flow (@xyflow/react) | 12.8 |
| Chart | Recharts | — |
| Ikon | Lucide | 0.544 |
| Form | react-hook-form, zod | — |
| Animasi | framer-motion | — |
| API | FastAPI, SQLAlchemy 2 async, Alembic, Pydantic v2 | — |
| Auth | Argon2id, sesi HttpOnly + CSRF double-submit | — |
| Storage client | boto3 (S3-compatible) | — |
| Worker | Python 3.11, Polars, DuckDB | 2.1 / 2.0 |
| Queue | Redis Streams consumer group | 7 |
| Agent | LangGraph + PostgresSaver, Pydantic output, sqlglot guard | 30.26 |
| Data | Postgres, Redis 7, object storage S3-compatible | — |
| Observabilitas | Prometheus client | 0.26 |
| Infrastruktur | Docker Compose, Caddy, Prometheus | — |
| Test web | Playwright library + Node test runner | — |
| Test backend | pytest, pytest-asyncio | — |

## Arsitektur

Modular monolith dengan empat proses:

```text
apps/web      Next.js App Router, UI dan mode demo in-browser
apps/api      FastAPI: auth, RBAC, CRUD, ingest, SSE, query read-only
apps/worker   Redis Streams consumer, Bronze/Silver/Gold/Quarantine, gate
apps/agents   LangGraph: profiler, builder, guardian, text-to-SQL, self-healing
packages/contracts  Pydantic, satu sumber kebenaran bentuk data
infra/        Docker Compose, Caddy, Prometheus
tests/        unit, integrasi, isolasi tenant, recovery
```

Alur data:

```text
Sumber -> Bronze (immutable) -> Silver -> gate -> Gold
                                  |
                                  +-> Quarantine (dengan alasan)
```

Detail modul, siklus request, dan batas kepercayaan ada di
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Fitur utama

| Modul | Fitur |
| --- | --- |
| Sumber data | Webhook, unggah CSV/Parquet, REST polling, Postgres read-only, rotasi API key |
| Pipeline | Kanvas DAG, validasi, versi immutable, eksekusi worker, lineage |
| Kualitas | Empat aturan gate, karantina dengan alasan, rekonsiliasi, kontrak data |
| Data Explorer | Pratinjau Parquet Bronze/Silver/Karantina, Gold dari DuckDB, schema, lineage |
| Operasi | Monitor run dengan SSE, log, timeline, cancel, rerun |
| Agent | Usulan pembersihan, approval manusia, confidence, budget, audit |
| Tanya data | Text-to-SQL read-only dengan guard dan batas baris |
| Governansi | RBAC, isolasi tenant, audit log, ambang kualitas per proyek |
| Biaya | Token, biaya, latency, anggaran bulanan dan batas per run |
| Demo | Tiga proyek sintetis dengan cerita, run, gate, dan karantina masing-masing |

Detail per modul ada di [`docs/FEATURES.md`](docs/FEATURES.md).

## Galeri

Semua gambar berasal dari mode demo dengan data sintetis. Galeri lengkap 27
tangkapan layar ada di [`docs/GALLERY.md`](docs/GALLERY.md).

| Landing | Dashboard |
| --- | --- |
| ![Landing](docs/screenshots/01-landing.png) | ![Dashboard](docs/screenshots/02-dashboard.png) |

| Karantina dan rekonsiliasi | Kontrak data |
| --- | --- |
| ![Karantina](docs/screenshots/11-data-quarantine.png) | ![Kontrak data](docs/screenshots/12-data-contract.png) |

| Agent | Mobile |
| --- | --- |
| ![Agent](docs/screenshots/13-agents.png) | ![Mobile](docs/screenshots/22-mobile-landing.png) |

## Halaman

| Rute | Tujuan |
| --- | --- |
| `/` | Landing: nilai produk, alur, batas, CTA demo |
| `/login`, `/register` | Masuk dan membuat organisasi |
| `/dashboard` | Ringkasan run, kesehatan pipeline, approval |
| `/setup` | Setup terpandu: proyek, sumber, API key, pipeline, run pertama |
| `/projects` | Daftar dan pembuatan proyek |
| `/sources` | Webhook, unggahan, REST, Postgres, API key |
| `/pipelines` | Daftar pipeline |
| `/pipelines/[pipelineId]` | Kanvas DAG, properti node, versi, jalankan |
| `/pipelines/lineage` | Lineage dataset |
| `/runs` | Riwayat run |
| `/runs/[runId]` | Monitor run, timeline, log, gate checklist |
| `/data` | Penjelajah Bronze, Silver, Gold, Karantina |
| `/data-contract` | Kontrak data proyek dan pipeline |
| `/agents` | Usulan agent dan approval |
| `/ask` | Tanya data dengan SQL read-only |
| `/usage` | Token, biaya, latency |
| `/settings` | Akun, anggota, API key, gate, audit |
| `/admin` | Hub administrasi |
| `/admin/team` | Anggota dan peran |
| `/admin/quality` | Ambang gerbang kualitas |
| `/admin/audit` | Audit log |
| `/admin/installation` | Konfigurasi LLM instalasi |

Detail state setiap halaman ada di [`docs/PAGES.md`](docs/PAGES.md).

## Menjalankan web

```bash
cd apps/web
npm install
npm run dev
```

Buka `http://localhost:3000`.

Mode demo tanpa akun:

```text
http://localhost:3000/?mode=demo
```

Build produksi:

```bash
cd apps/web
npm run typecheck
npm run lint
npm run build
npm run start
```

E2E demo (server harus sudah hidup di port 3210):

```bash
cd apps/web
npm run test:e2e
```

## Menjalankan backend

Salin satu template env di root, isi nilainya, lalu jalankan infrastruktur:

```bash
cp .env.example .env
docker compose -f infra/docker-compose.yml up -d
```

Backend dan worker membutuhkan Postgres, Redis, object storage, dan konfigurasi
LLM. Mode demo web tidak membutuhkan salah satunya.

## Mode demo

Mode demo adalah backend in-memory di browser (`apps/web/lib/demo/`), bukan
server kedua.

- Kontrol utama berfungsi: buat proyek, tambah sumber, rotasi API key, simpan
  pipeline, jalankan pipeline, putuskan usulan agent, ubah budget, ubah peran.
- Tidak ada permintaan ke server. `apiFetch` berhenti di adapter memori sebelum
  menyusun HTTP request.
- Data hanya ada di memori tab dan hilang saat tab ditutup.
- Banner demo menyebut data contoh dan menyediakan tombol reset serta keluar.
- Tiga proyek demo: Penjualan Ritel, Pelanggan & CRM, dan Inventori Gudang.

## Batasan

- Scheduler cron berjalan in-process; beberapa replika API membutuhkan leader
  election yang belum ada.
- Redis AOF `everysec` dapat kehilangan sekitar satu detik data saat crash.
- `apply_healing_fix` mengembalikan action plan; eksekusi `new_version`/requeue milik API belum disambung.
- Angka performa dan akurasi tidak diklaim sampai diukur di lingkungan nyata.

## Dokumentasi

| Dokumen | Isi |
| --- | --- |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Komponen, alur request, batas kepercayaan |
| [`docs/PAGES.md`](docs/PAGES.md) | Rute, tujuan halaman, state |
| [`docs/FEATURES.md`](docs/FEATURES.md) | Fitur per modul dan bukti kode |
| [`docs/GALLERY.md`](docs/GALLERY.md) | Galeri 33 tangkapan layar desktop dan mobile |
| [`docs/TEST_SELFHEAL.md`](docs/TEST_SELFHEAL.md) | Verifikasi self-healing: angka test dan batas |
| [`docs/SECURITY_SELFHEAL.md`](docs/SECURITY_SELFHEAL.md) | Review keamanan self-healing: 7/7 PASS |

## Lisensi

Hak cipta (c) 2026 PogungSoftwareHouseTeam (PSHT). Seluruh hak dilindungi.

Kode dalam repositori ini dikembangkan dan dimiliki oleh PogungSoftwareHouseTeam (PSHT). Detail ketentuan penggunaan ada di LICENSE.md.

Dependensi pihak ketiga tunduk pada lisensi masing-masing, bukan pada lisensi ini.