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

## Stack

| Lapisan | Teknologi |
| --- | --- |
| Web | Next.js 15.5 App Router, React 19, TypeScript strict, Tailwind CSS 3.4 |
| UI | shadcn/ui, Radix UI, TanStack Query, TanStack Table, React Flow, Recharts, Lucide |
| Form | react-hook-form, zod |
| API | FastAPI, SQLAlchemy 2 async, Alembic, Pydantic v2, Argon2id, boto3 |
| Worker | Python 3.11, Polars, DuckDB, Redis Streams consumer group |
| Agent | LangGraph, PostgresSaver, Pydantic output, sqlglot guard |
| Data | Postgres, Redis 7, S3-compatible object storage |
| Infrastruktur | Docker Compose, Caddy, Prometheus |

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
| Operasi | Monitor run dengan SSE, log, timeline, cancel, rerun |
| Agent | Usulan pembersihan, approval manusia, confidence, budget, audit |
| Tanya data | Text-to-SQL read-only dengan guard dan batas baris |
| Governansi | RBAC, isolasi tenant, audit log, ambang kualitas per proyek |
| Biaya | Token, biaya, latency, anggaran bulanan dan batas per run |
| Demo | Tiga proyek sintetis dengan cerita, run, gate, dan karantina masing-masing |

Detail per modul ada di [`docs/FEATURES.md`](docs/FEATURES.md).

## Halaman

| Rute | Tujuan |
| --- | --- |
| `/` | Landing: nilai produk, alur, batas, CTA demo |
| `/login`, `/register` | Masuk dan membuat organisasi |
| `/dashboard` | Ringkasan run, kesehatan pipeline, approval |
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

- API belum membaca pratinjau baris Parquet untuk lapisan Bronze, Silver, dan
  Karantina. UI menampilkan jumlah dan alasan yang tersedia, dan tidak
  mengarang baris.
- Scheduler cron berjalan in-process; beberapa replika API membutuhkan leader
  election yang belum ada.
- Redis AOF `everysec` dapat kehilangan sekitar satu detik data saat crash.
- Self-healing agent masih kerangka dan belum diuji end-to-end.
- Angka performa dan akurasi tidak diklaim sampai diukur di lingkungan nyata.

## Dokumentasi

| Dokumen | Isi |
| --- | --- |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Komponen, alur request, batas kepercayaan |
| [`docs/PAGES.md`](docs/PAGES.md) | Rute, tujuan halaman, state |
| [`docs/FEATURES.md`](docs/FEATURES.md) | Fitur per modul dan bukti kode |

## Lisensi

Hak cipta (c) 2026 PogungSoftwareHouseTeam (PSHT). Seluruh hak dilindungi.

Kode dalam repositori ini dikembangkan dan dimiliki oleh PogungSoftwareHouseTeam (PSHT). Detail ketentuan penggunaan ada di LICENSE.md.

Dependensi pihak ketiga tunduk pada lisensi masing-masing, bukan pada lisensi ini.