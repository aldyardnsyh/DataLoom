# Galeri DataLoom

Semua tangkapan layar diambil dari mode demo, bukan dari tenant nyata. Data
yang terlihat adalah fixture sintetis: `Demo Nusantara Retail`, `run-1`, dan
sejenisnya. Tidak ada kredensial, email pelanggan, atau data produksi.

- Desktop: 1440 x 900.
- Mobile: 390 x 844.
- Sumber: suite QA demo yang sama dengan `npm run test:e2e`.

## Landing dan shell

### Landing page

Halaman publik menjelaskan mengapa DataLoom bukan alat pembersih sekali jalan:
gerbang kualitas, karantina, rekonsiliasi, dan kontrak data.

![Landing page](screenshots/01-landing.png)

### Dashboard

Ringkasan 24 jam, kesehatan pipeline, dan run terbaru untuk tiga proyek demo.

![Dashboard](screenshots/02-dashboard.png)

### Sidebar rail 64px

Sidebar menyempit menjadi rail ikon tanpa menabrak logo, label demo, atau
footer. Satu label "Mode demo" tetap berada di banner.

![Sidebar rail](screenshots/03-sidebar-rail.png)

## Ruang kerja

### Proyek

Tiga proyek dengan cerita berbeda: Penjualan Ritel, Pelanggan & CRM, dan
Inventori Gudang.

![Proyek](screenshots/04-projects.png)

### Sumber data

Webhook, CSV, REST, Postgres, API key, rotasi, dan pencabutan key. Di mode demo
unggahan tidak mengirim byte, tetapi dataset Bronze baru tetap terdaftar agar
alurnya bisa diperiksa.

![Sumber data](screenshots/05-sources.png)

### Daftar pipeline

Pipeline dengan status versi dan run terakhir.

![Daftar pipeline](screenshots/06-pipelines.png)

### Kanvas pipeline

Node sumber, validasi, pembersihan, transformasi, agregasi, dan sink. Validasi
deterministik menandai node yang bermasalah.

![Kanvas pipeline](screenshots/07-pipeline-canvas.png)

### Lineage

Graf sumber ke Bronze, Silver, Gold, dan Karantina.

![Lineage](screenshots/08-lineage.png)

### Riwayat run

Run sukses, gagal, karantina tinggi, dan antrean dalam satu daftar.

![Riwayat run](screenshots/09-runs.png)

### Monitor run

Timeline node, baris masuk dan keluar, karantina, rasio, gate checklist, dan log.

![Monitor run](screenshots/10-run-detail.png)

## Nilai produk

### Karantina dan rekonsiliasi

Baris ditolak dengan kode alasan, rasio terhadap ambang proyek, ledger run,
empat aturan gate, dan status rekonsiliasi.

![Karantina dan rekonsiliasi](screenshots/11-data-quarantine.png)

### Setup terpandu

Lima langkah dari akun kosong sampai run pertama, lengkap dengan endpoint ingest
dan contoh perintah yang bisa disalin.

![Setup terpandu](screenshots/28-setup.png)

### Kontrak data

Kebijakan proyek, empat aturan gate, aturan validasi baris per pipeline, dan
tautan ke penegakannya.

![Kontrak data](screenshots/12-data-contract.png)

## Agent dan tanya data

### Ruang kerja agent

Profil dataset, usulan pembersihan, bukti, risiko, confidence, dan approval
manusia.

![Agent](screenshots/13-agents.png)

### Tanya data

Pertanyaan bahasa natural, SQL read-only, hasil, asumsi, dan klarifikasi.

![Tanya data](screenshots/14-ask.png)

### Penggunaan dan biaya

Token, biaya, latency, model, dan anggaran.

![Penggunaan dan biaya](screenshots/15-usage.png)

## Pengaturan dan administrasi

### Pengaturan

Akun, anggota, API key, gerbang kualitas, audit, dan konfigurasi AI.

![Pengaturan](screenshots/16-settings.png)

### Admin

Hub administrasi dengan empat lingkup yang berbeda.

![Admin](screenshots/17-admin.png)

### Gerbang kualitas

Ambang karantina, kewajiban approval, dan freshness per proyek.

![Gerbang kualitas](screenshots/18-admin-quality.png)

### Anggota dan peran

Owner, admin, editor, dan viewer.

![Anggota dan peran](screenshots/19-admin-team.png)

### Audit log

Aksi sensitif dengan pelaku, target, dan waktu.

![Audit log](screenshots/20-admin-audit.png)

### Instalasi model AI

Konfigurasi penyedia model tingkat instalasi. Key tidak pernah dikirim balik ke
browser.

![Instalasi model AI](screenshots/21-admin-installation.png)

## Mobile

### Landing mobile

![Landing mobile](screenshots/22-mobile-landing.png)

### Dashboard mobile

![Dashboard mobile](screenshots/23-mobile-dashboard.png)

### Sidebar sheet

![Sidebar sheet](screenshots/24-mobile-sidebar-sheet.png)

### Penjelajah Data mobile

![Data mobile](screenshots/25-mobile-data.png)

### Kontrak Data mobile

![Kontrak data mobile](screenshots/26-mobile-data-contract.png)

## Notifikasi

Notifikasi memakai ikon dan warna berbeda per jenis:

| Jenis | Ikon | Kapan dipakai |
| --- | --- | --- |
| Sukses | centang hijau | aksi selesai dan data berubah |
| Gagal | silang merah | aksi ditolak atau server gagal |
| Peringatan | segitiga amber | validasi belum lulus atau ambang mendekati batas |
| Info | lingkaran biru | proses dimulai atau pesan mode demo |
| Loading | spinner | proses yang masih berjalan |

Setiap notifikasi membawa judul dan deskripsi, bukan hanya satu kalimat pendek.

![Notifikasi sukses](screenshots/27-notification-success.png)