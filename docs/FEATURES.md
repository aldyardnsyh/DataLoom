# DataLoom - Dokumentasi Fitur

> Daftar fitur per modul, perilaku yang dijanjikan, dan bukti kode. Dokumen ini
> tidak mengklaim fitur yang belum ada.

## 1. Ingest dan sumber data

| Fitur | Perilaku | Bukti |
| --- | --- | --- |
| Webhook per sumber | Endpoint dan API key per sumber, key hanya tampil sekali saat dibuat | `apps/api/app/api/v1/ingest.py`, `api_keys.py`, `apps/web/app/(app)/sources` |
| Unggah CSV/Parquet | Validasi tipe dan magic byte, batas 500 MB, presigned upload | `apps/web/lib/upload.ts`, `apps/api/app/api/v1/uploads.py` |
| REST polling | Sumber REST dengan status koneksi | `apps/api`, `apps/worker` |
| Postgres read-only | Koneksi baca, bukan jalur tulis | `apps/api`, `apps/worker` |
| Rotasi dan cabut key | Key baru hanya tampil sekali, key dicabut ditolak 401 | `sources` page, `api_keys.py` |
| Mode demo | Unggahan disimulasikan, tidak ada PUT ke object storage | `apps/web/lib/upload.ts` |

## 2. Pipeline dan Medallion

| Fitur | Perilaku | Bukti |
| --- | --- | --- |
| Kanvas DAG | Node sumber, validasi, pembersihan, transformasi, agregasi, sink | `apps/web/components/domain/pipeline-canvas.tsx` |
| Validator deterministik | Node yatim, siklus, edge tidak valid, sink tanpa input | `packages/contracts/dataloom_contracts/pipelines.py`, `apps/web/lib/dag-validate.ts` |
| Versi immutable | Edit membuat versi baru, run lama tetap menunjuk versi lamanya | `apps/api/app/services/pipeline_service.py` |
| Eksekusi worker | Topological order, timeout per node, retry terbatas | `apps/worker/app/dag_runner.py` |
| Bronze immutable | Setiap batch disimpan dengan id sendiri | `apps/worker/app/bronze.py` |
| Silver dan Gold | Cast, normalisasi, dedup, validasi, agregasi | `apps/worker/app/silver.py`, `gold.py` |
| Registri dataset | Worker mendaftarkan Silver, Gold, dan Karantina ke tabel `datasets` | `apps/worker/app/datasets.py`, `dag_runner.py` |
| Lineage dataset | Graf sumber ke Bronze, Silver, Gold, Karantina | `apps/api/app/services/pipeline_service.py`, `/pipelines/lineage` |

## 3. Gerbang kualitas deterministik

Empat aturan, tanpa LLM:

1. `schema_valid`
2. `quarantine_ratio`
3. `reconciliation`
4. `pending_proposals`

Bukti:
- `apps/worker/app/gate.py`
- `apps/api/app/api/v1/runs.py`
- `apps/web/app/(app)/runs/[runId]`
- `apps/web/app/(app)/data` tab Karantina & Rekonsiliasi
- `apps/web/app/(app)/data-contract`

Perilaku:
- Gold hanya dipromosikan jika semua aturan yang blocking lulus.
- Setiap aturan menyimpan bukti yang bisa dibaca operator.
- Gagal satu aturan berarti `needs_attention`, bukan sukses diam-diam.

## 4. Karantina dan rekonsiliasi

| Fitur | Perilaku | Bukti |
| --- | --- | --- |
| Baris ditolak dengan alasan | Setiap baris membawa `_reason_code`, `_reason_detail`, `_rule_id`, `_quarantined_at` | `apps/worker/app/quarantine.py`, `silver.py` |
| Rekonsiliasi Silver ke Gold | Jumlah grup dan jumlah measure dibandingkan | `apps/worker/app/gold.py` |
| Rasio terhadap ambang | Rasio run dibandingkan dengan pengaturan proyek | `apps/worker/app/gate.py` |
| Ledger run | Operator melihat run mana yang menolak dan berapa barisnya | `apps/web/components/domain/quarantine-tab.tsx` |
| Alasan terbanyak | Dikelompokkan dari kode alasan, bukan teks bebas | `quarantine-tab.tsx` |
| Pratinjau Parquet | API membaca objek Parquet milik org, membatasi 1.000 baris, dan tidak mengarang baris | `apps/api/app/services/query_service.py`, `explore.py` |
| State jujur | Jika objek hilang atau gagal dibaca, UI menampilkan skema dan jumlah tanpa baris palsu | `quarantine-tab.tsx`, `explore.py` |

## 5. Kontrak data

| Fitur | Perilaku | Bukti |
| --- | --- | --- |
| Kebijakan proyek | Ambang karantina, approval, freshness dibaca dari pengaturan proyek | `/data-contract`, `api.projects.settings` |
| Empat aturan gerbang | Dicetak dengan arti dan alasan | `docs` dan `/data-contract` |
| Aturan baris per pipeline | Dibaca dari node Validasi versi terbaru | `/data-contract`, `extractRules` |
| Versi yang berlaku | Ditampilkan per pipeline | `/data-contract` |
| Tautan penegakan | Menuju Riwayat Run, Penjelajah Data, Pipeline | `/data-contract` |
| State error | Pengaturan atau versi gagal dimuat ditampilkan, bukan disembunyikan | `/data-contract` |

## 6. Agent dan approval

| Fitur | Perilaku | Bukti |
| --- | --- | --- |
| Schema Profiler | Membaca sampel, menghasilkan usulan pembersihan | `apps/agents`, `/agents` |
| Usulan tidak langsung diterapkan | Agent mengusulkan, manusia memutuskan | `apps/api/app/api/v1/agents.py` |
| Usulan destruktif | Wajib konfirmasi satu per satu | `confirm-dialog.tsx`, `/agents` |
| Penerapan | Menolak jika masih ada usulan destruktif pending | `apps/api/app/api/v1/agents.py` |
| Audit | Keputusan tercatat | `apps/api/app/core/audit.py` |
| Insufficient evidence | Agent berhenti dan tidak menebak | `apps/agents` |

## 7. Tanya data dan SQL read-only

| Fitur | Perilaku | Bukti |
| --- | --- | --- |
| Text-to-SQL | Pertanyaan bahasa natural menjadi SQL | `apps/api/app/services/text_to_sql_service.py` |
| Guard SQL | Hanya SELECT, allowlist tabel, tanpa file/network function | `apps/api/app/services/query_service.py` |
| Batas baris | Maksimum 1.000 baris | `packages/contracts`, `query_service.py` |
| Klasifikasi hasil | hasil, ditolak, klarifikasi | `/ask` |

## 8. Monitor run dan observability

| Fitur | Perilaku | Bukti |
| --- | --- | --- |
| SSE | Node status, log, gate, run status | `apps/api/app/api/v1/runs.py`, `apps/web/hooks/use-run-stream.ts` |
| Reconnect dan fallback | Polling saat stream terputus | `/runs/[runId]` |
| Timeline | Durasi node sebagai batang | `run-timeline.tsx` |
| Gate checklist | Ikon dan teks, bukan warna saja | `/runs/[runId]` |
| Cancel dan rerun | Aksi nyata dengan konfirmasi | `/runs/[runId]` |

## 9. Auth, RBAC, dan audit

| Fitur | Perilaku | Bukti |
| --- | --- | --- |
| Sesi | Cookie HttpOnly, CSRF double-submit | `apps/api/app/core/security.py`, `apps/web/lib/api.ts` |
| Peran | owner, admin, editor, viewer | `apps/api/app/core/rbac.py` |
| Isolasi tenant | `org_id` di semua sumber daya, lintas tenant 404 | `apps/api/app/deps.py` |
| Audit log | Aksi sensitif tercatat dengan pelaku dan IP | `apps/api/app/core/audit.py`, `/admin/audit` |
| Installation admin | Flag terpisah dari peran organisasi | `/admin/installation` |

## 10. Penggunaan dan biaya

| Fitur | Perilaku | Bukti |
| --- | --- | --- |
| Token dan biaya | Dari catatan panggilan LLM, bukan perkiraan | `/usage`, `apps/api/app/api/v1/usage.py` |
| Anggaran | Bulanan dan batas token per run | `/usage`, `/settings` |
| Peringatan | Ambang 80% dengan ikon dan teks | `budget-meter.tsx` |

## 11. Mode demo

| Fitur | Perilaku | Bukti |
| --- | --- | --- |
| Tanpa server | Router in-memory di browser | `apps/web/lib/demo/backend.ts` |
| Tiga proyek utuh | Penjualan, CRM, Inventori | `apps/web/lib/demo/fixtures.ts` |
| Tulis berfungsi | Buat proyek, sumber, pipeline, run, approval, budget | `backend.ts` |
| Tidak menyentuh jaringan | `apiFetch` short-circuit sebelum HTTP | `apps/web/lib/api.ts` |
| Banner tunggal | Satu label demo di atas topbar | `demo-banner.tsx` |
| Reset dan keluar | Mengembalikan fixture atau keluar tanpa akun | `demo-provider.tsx` |

## 12. Kualitas UI

| Fitur | Perilaku | Bukti |
| --- | --- | --- |
| Loading, empty, error | Setiap halaman data punya state | `components/domain/*` |
| Status ikon dan teks | Tidak pernah warna saja | `status-badge.tsx` |
| Keyboard | Fokus terlihat, dialog bisa ditutup Escape | komponen Radix, `confirm-dialog.tsx` |
| Responsif | Sidebar rail dan sheet, tabel bisa digeser di dalam kontainer | `sidebar.tsx`, `data-table.tsx` |
| Bahasa | Indonesia, teks terpusat di `lib/i18n/id.ts` | `id.ts` |
| E2E regression | Playwright library + Node test, dijalankan lewat app runner global | `apps/web/e2e/demo.e2e.mjs`, `npm run test:e2e` |

## Non-tujuan

- Tidak ada marketplace konektor.
- Tidak ada streaming sub-detik end-to-end.
- Tidak ada eksekusi kode pengguna.
- Tidak ada dark mode.
- Tidak ada perubahan aturan tanpa persetujuan manusia.
- Tidak ada klaim performa atau akurasi yang belum diukur.