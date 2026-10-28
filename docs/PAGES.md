# DataLoom - Dokumentasi Halaman

> Setiap rute di bawah ini ada di `apps/web/app`. Dokumen ini menjelaskan tujuan
> halaman, state yang harus ditangani, dan bagian yang membedakan halaman
> operator dari halaman pemasaran.

## Lingkup publik

### `/` - Landing

Tujuan: menjelaskan nilai produk tanpa klaim palsu, lalu mengantar ke demo atau
pendaftaran.

Isi:
- hero yang menyatakan cleaning adalah komoditas, sedangkan gerbang kualitas
  bukan;
- skematik satu run dengan gate checklist;
- bagian nilai produk: karantina, rekonsiliasi, versi dan approval;
- alur Bronze, Silver, Gold;
- kemampuan yang benar-benar ada;
- batas produk yang jujur;
- tautan ke kontrak data dan karantina dalam mode demo.

State: server-rendered, tanpa data sensitif. Tombol demo menyetel flag demo
sebelum navigasi.

### `/login` dan `/register`

Tujuan: masuk ke ruang kerja atau membuat organisasi.

State:
- loading saat sesi diperiksa;
- error inline untuk kredensial atau validasi;
- tombol disabled saat submit;
- pengguna yang sudah masuk dipantulkan ke dasbor.

## Lingkup ruang kerja

### `/dashboard`

Tujuan: menjawab "apakah hari ini ada yang perlu saya tangani?".

Isi: KPI 24 jam, kesehatan pipeline, run terbaru, usulan yang menunggu.
State: loading, kosong tanpa proyek, error, data parsial.

### `/projects`

Tujuan: membuat dan memilih proyek.

State: loading, kosong, error, dialog hapus dengan konfirmasi nama, tombol
disabled untuk peran yang tidak boleh mengubah.

### `/sources`

Tujuan: menghubungkan data masuk.

Isi: webhook, unggah CSV/Parquet, polling REST, Postgres read-only, API key,
endpoint, rotasi dan pencabutan key.
State: loading, kosong, error koneksi, key dicabut, unggahan tidak valid,
mode demo yang tidak mengirim berkas.

### `/pipelines`

Tujuan: daftar pipeline dan pintu masuk kanvas.

State: loading, kosong, error, pembuatan pipeline.

### `/pipelines/[pipelineId]`

Tujuan: menyusun dan memvalidasi DAG.

Isi: kanvas React Flow, pustaka node, panel properti, editor parameter JSON,
validasi deterministik, simpan versi, jalankan pipeline.
State: draf, valid, invalid dengan node ditandai, perubahan belum disimpan,
read-only di layar kecil.

### `/pipelines/lineage`

Tujuan: melihat asal dataset lintas lapisan.

State: loading, kosong, error, fokus keyboard pada graf.

### `/runs`

Tujuan: riwayat eksekusi.

State: loading, kosong, error, filter status.

### `/runs/[runId]`

Tujuan: monitor satu run.

Isi: status, baris masuk/keluar, karantina, durasi, timeline node, log SSE,
gate checklist, aksi cancel dan rerun, usulan self-healing.
State: queued, running, retrying, succeeded, failed, needs attention, terputus.

### `/data`

Tujuan: Penjelajah Data.

Isi:
- pohon dataset per lapisan Bronze, Silver, Gold, Karantina;
- pratinjau, skema, kualitas, lineage;
- tab **Karantina & Rekonsiliasi**:
  - dataset karantina;
  - baris tersimpan;
  - rasio karantina run terhadap ambang proyek;
  - status gerbang dan rekonsiliasi;
  - daftar periksa empat aturan dengan bukti;
  - ledger run yang menolak baris;
  - alasan penolakan;
  - baris yang ditolak dengan `_reason_code`.

State: loading, kosong, error, terpotong pada 1.000 baris, dataset bukan
karantina, API tidak mengembalikan pratinjau baris.

### `/data-contract`

Tujuan: mencetak kontrak data proyek.

Isi:
- kebijakan proyek: ambang rasio karantina, kewajiban approval, freshness;
- empat aturan gerbang;
- aturan validasi baris dari node Validasi setiap pipeline;
- versi pipeline yang berlaku;
- tautan ke Riwayat Run, Penjelajah Data, dan Pipeline.

State: loading, tanpa proyek, tanpa pipeline, error pengaturan, error versi,
pipeline tanpa aturan validasi.

### `/agents`

Tujuan: ruang kerja agent.

Isi: Schema Profiler, usulan, bukti, risiko, confidence, approval satu per satu,
penolakan dengan alasan, penerapan usulan sebagai versi pipeline baru.
State: menunggu approval, budget hampir habis, insufficient evidence, kosong.

### `/ask`

Tujuan: tanya data dalam bahasa natural.

Isi: pertanyaan, SQL read-only, hasil, asumsi, klarifikasi, penolakan query.
State: loading, hasil, ditolak, klarifikasi, kosong.

### `/usage`

Tujuan: penggunaan dan biaya LLM.

Isi: token, biaya, latency, biaya per agent, token per model, panggilan terbaru,
anggaran bulanan dan batas token per run.
State: loading, kosong, data nyata, peringatan anggaran.

### `/settings`

Tujuan: pengaturan pengguna dan organisasi.

Isi: akun, peran, anggota, API key, gerbang kualitas, audit log, model AI
read-only.
State: read-only viewer, loading, error, simpan berhasil.

## Lingkup administrasi

### `/admin`

Hub administrasi dengan empat lingkup yang berbeda: anggota, kualitas, audit,
dan konfigurasi LLM instalasi.

### `/admin/team`

Anggota dan peran. Owner tidak dapat dihapus atau diturunkan dari halaman ini.

### `/admin/quality`

Ambang gerbang kualitas proyek aktif: rasio karantina, kewajiban approval,
freshness.

### `/admin/audit`

Audit log organisasi, read-only.

### `/admin/installation`

Konfigurasi LLM seluruh instalasi. Form read-only untuk non-admin instalasi.
API key tidak pernah dikirim balik ke browser.

## Perilaku lintas halaman

- **Sidebar**: 240px pada desktop, rail 64px saat diciutkan atau pada 768-1023px,
  sheet di bawah 768px.
- **Topbar**: breadcrumb, pemilih proyek, pencarian, menu akun. Tidak ada tombol
  notifikasi karena belum ada layanan notifikasi.
- **Mode demo**: satu banner di atas topbar, bukan label berulang di sidebar.
- **Status**: selalu ikon dan teks, bukan warna saja.
- **Angka**: berasal dari API atau fixture demo yang diberi label.
- **Error**: menyebut penyebab dan aksi berikutnya, bukan pesan generik.