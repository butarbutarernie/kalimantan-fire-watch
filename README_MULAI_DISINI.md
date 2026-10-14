# KALIMANTAN FIRE WATCH — Dashboard Interaktif Open Data

**Versi:** MVP 1.0, 14 Oktober 2026  
**Bentuk keluaran:** aplikasi Streamlit siap dijalankan/di-deploy, bukan URL situs yang sudah dihosting.  
**Sumber:** NASA FIRMS (VIIRS S-NPP, NOAA-20, NOAA-21) + geoBoundaries gbOpen ADM1 Indonesia. **Tidak memakai BMKG/SiPongi.**

## A. Jalankan hari ini: langkah tercepat

### Jalur 1 — Publikasikan ke Streamlit Community Cloud (dashboard bisa dibuka lewat URL)

1. Ekstrak ZIP `Kalimantan_Fire_Dashboard_ReadyToDeploy_20261014.zip`.
2. Buka [NASA FIRMS Area API](https://firms.modaps.eosdis.nasa.gov/api/area/) dan ambil **MAP_KEY gratis** melalui email resmi. MAP_KEY dibutuhkan NASA untuk penggunaan layanan API meskipun data terbuka.
3. Buat repository GitHub, misalnya `kalimantan-fire-watch`. Unggah `streamlit_app.py`, `fire_core.py`, `fire_viz.py`, `requirements.txt`, serta folder `.streamlit/config.toml` (jangan unggah file secrets.toml pribadi). File contoh HTML dan tes boleh disertakan tetapi tidak wajib.
4. Buka [Streamlit Community Cloud](https://share.streamlit.io/) → **Create app** → pilih repository → **App file path**: `streamlit_app.py`.
5. Di menu **Advanced settings → Secrets**, isi tepat (ganti nilainya dengan MAP_KEY Anda):

   ```toml
   FIRMS_MAP_KEY = "ISI_NASA_MAP_KEY_ANDA"
   ```

6. Deploy. Dashboard menampilkan deteksi NASA setelah berhasil mengambil data dan batas provinsi geoBoundaries. URL yang dibuat Streamlit dapat Anda bagikan kepada tim.
7. Pilih sensor, periode 24/48/72 jam atau 7 hari, provinsi, confidence, dan aktifkan layer klaster/heatmap. Tekan **Ambil ulang data** bila perlu.

**Catatan:** Hosting cloud bergantung pada akun dan pengaturan layanan Anda. Belum ada website publik yang dibuat/dioperasikan oleh penyusun ZIP ini.

### Jalur 2 — Jalankan di laptop (tanpa GitHub)

Syarat: Python 3.11–3.13, koneksi internet, NASA MAP_KEY gratis.

**Windows PowerShell:**

```powershell
cd C:\path\ke\Kalimantan_Fire_Dashboard_20261014
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

**macOS/Linux:**

```bash
cd /path/ke/Kalimantan_Fire_Dashboard_20261014
python3 -m pip install -r requirements.txt
python3 -m streamlit run streamlit_app.py
```

Browser biasanya otomatis membuka alamat lokal `http://localhost:8501`. Bila MAP_KEY belum disimpan sebagai secret, pilih **NASA FIRMS API** dan masukkan MAP_KEY di panel kiri (input tersembunyi).

### Jalur 3 — Lihat contoh peta tanpa instalasi

Buka `assets/DEMO_SYNTHETIC_INTERACTIVE_MAP.html` di browser. Tersedia pan/zoom, layer, popup, klaster, dan heatmap. **Seluruh titik adalah sintetis, bukan kejadian karhutla tanggal 14 Oktober 2026.** Basemap membutuhkan koneksi internet.

### Jalur 4 — Quicklook di Google Earth Engine, tanpa MAP_KEY NASA

Buka [Google Earth Engine Code Editor](https://code.earthengine.google.com), salin `assets/GEE_Quicklook_Opsional.js`, kemudian jalankan. Sumber GEE menggunakan VIIRS S-NPP raster 375 m dari dataset `NASA/LANCE/SNPP_VIIRS/C2`. Pengguna perlu akun dan akses Earth Engine. **Ini adalah alat analisis GEE terpisah, bukan dependensi dashboard Streamlit.** Periksa tanggal citra paling akhir sebelum mendeskripsikan hasil sebagai kondisi terkini.

## B. Apa yang sudah ada di dashboard?

- Peta interaktif Folium: **titik individu** (popup waktu UTC/lokal, confidence dan FRP), **heatmap**, **klaster DBSCAN**, **batas provinsi**, pilihan basemap, mini-map, fullscreen.
- Sumber live NASA FIRMS API dari tiga satelit VIIRS; rentang tanggal bergerak 24/48/72 jam, 7 hari terakhir. API membatasi 1–5 hari per permintaan, sehingga sistem memecah rentang panjang secara otomatis.
- Filter provinsi, satelit, tingkat kepercayaan, dan parameter pengelompokan DBSCAN (Haversine).
- Metrik jumlah **rekaman deteksi**, deteksi high-confidence, klaster eksploratif, dan usia pengamatan terbaru.
- Grafik jumlah deteksi per provinsi, menurut sensor/confidence, serta seri waktu pengamatan UTC.
- Ekspor **ZIP** berisi CSV, GeoJSON, peta HTML, tabel klaster, manifest JSON dengan QC/waktu observasi, dan batasan ilmiah; serta ekspor CSV / HTML terpisah.
- Mode unggah CSV FIRMS, bermanfaat bila API sementara tidak dapat diakses; mode demonstrasi sintetis dengan watermark tegas.
- Pengunduhan batas provinsi melalui geoBoundaries berlisensi CC BY 4.0, validasi tepat 5 provinsi, dan **point-in-polygon** untuk menolak deteksi dari wilayah negara tetangga di dalam bounding box. Bisa pula unggah GeoJSON ADM1 yang legal/valid bila API batas tidak bisa diakses.

## C. Interpretasi ilmiah — wajib dibaca

1. **Hotspot NASA FIRMS adalah deteksi anomali termal** dan tidak membuktikan satu kebakaran unik ataupun luas hektare terbakar. Tiga satelit dapat melaporkan observasi yang tumpang tindih.
2. **FRP** adalah daya radiatif deteksi pada pengamatan tertentu (MW), bukan luas kebakaran, kecepatan penyebaran, atau intensitas operasi pemadaman.
3. **Klaster DBSCAN** mengelompokkan lokasi dengan ambang geografis yang dipilih, bukan perimeter api, probabilitas penyebaran, atau perintah patroli.
4. **Confidence tinggi** adalah tingkat keyakinan algoritme deteksi satelit, bukan peringkat keparahan kejadian.
5. Pengamatan dapat tertutup awan, berbeda jadwal lintasan, dan terlambat masuk arsip. **Tidak ditemukan titik** bukan berarti tidak ada api.
6. Jangan menganggap dashboard sebagai sistem peringatan keselamatan atau instruksi pemadaman. Konfirmasi lapangan dan koordinasi dengan otoritas tetap diperlukan.
7. Periksa kolom **usia observasi terbaru**, waktu unduhan API (manifest) dan status sensor yang gagal. Jika sumber parsial, jangan membandingkan hitungan seakan cakupannya identik.

## D. Rekomendasi operasional yang aman

- Pakai filter **72 jam**, lalu telaah klaster yang terdeteksi pada lebih dari satu hari atau memiliki beberapa sensor.
- Simpan CSV, GeoJSON dan manifest sebagai satu paket bukti waktu/metode.
- Gabungkan kemudian dengan data gambut, jalan, permukiman, angin atau data lapangan yang memiliki hak penggunaan jelas sebelum melakukan penilaian risiko atau tindakan lapangan.
- Untuk rapat situasional, sebut sebagai **indikasi anomali termal yang memerlukan verifikasi**, bukan sebagai jumlah kebakaran aktual.

## E. Status validasi pada saat paket disusun

| Pemeriksaan | Status |
|---|---|
| Sintaks seluruh modul Python | **PASS** |
| Unit test pemrosesan ilmiah, klaster, filter, ekspor | **13/13 PASS** |
| Generasi peta HTML interaktif dengan **data sintetis** | **PASS** |
| Generasi ZIP ekspor dengan data sintetis | **PASS** |
| Validasi langsung API NASA FIRMS memakai MAP_KEY pengguna | **BELUM**, tidak ada kredensial pengguna |
| Pengunduhan geoBoundaries dalam runtime cloud pengguna | **BELUM**, perlu jaringan saat deploy |
| Uji interaksi UI lengkap pada browser Streamlit yang sudah ter-deploy | **BELUM**, runtime Streamlit tidak tersedia di lingkungan build |
| Verifikasi kebakaran nyata dengan ground truth | **BELUM** |

**Kesimpulan status:** paket kode **siap dicoba dan siap di-deploy**, tetapi **belum diklaim sebagai sistem live yang sudah diuji pada data aktual**. Pemeriksaan akhir perlu dilakukan segera setelah deploy.

## F. Struktur berkas

```text
Kalimantan_Fire_Dashboard_20261014/
├── streamlit_app.py                 # antarmuka web
├── fire_core.py                     # API, QC, polygon, deteksi, klaster
├── fire_viz.py                      # peta Folium dan ekspor
├── requirements.txt
├── .streamlit/
│   ├── config.toml                  # warna/tata letak dan opsi server
│   └── secrets.toml.example         # contoh; BUKAN kunci aktual
├── assets/
│   ├── DEMO_SYNTHETIC_INTERACTIVE_MAP.html
│   └── GEE_Quicklook_Opsional.js
├── tests/test_fire_core.py          # unit test
├── README_MULAI_DISINI.md
└── .gitignore
```

## G. Troubleshooting

| Masalah | Langkah |
|---|---|
| Dashboard hanya memperlihatkan mode demo | Pilih NASA FIRMS API dan simpan/masukkan MAP_KEY gratis. |
| NASA menolak API | Periksa MAP_KEY, akses internet, kuota, dan waktu; jangan tempelkan MAP_KEY ke chat atau berkas GitHub publik. |
| NASA menjawab tanpa data | Ganti 24 jam ke 72 jam/7 hari; cek timestamp NASA, sensor, dan status parsial. |
| Boundary geoBoundaries gagal | Coba muat ulang; alternatif unggah GeoJSON ADM1 yang mencakup persis 5 provinsi Indonesia di Kalimantan. Aplikasi menolak menghasilkan hitungan wilayah nyata tanpa batas valid. |
| Peta lambat | Matikan heatmap/klaster/titik sesuai kebutuhan, persempit provinsi/periode, gunakan runtime dengan memori lebih besar. |
| Browser tidak menunjukkan peta | Muat ulang halaman setelah cache refresh; cek koneksi ke CDN/tiles map dan error logs Cloud. |
| Perubahan belum muncul | Tekan tombol Ambil ulang data (hapus cache) atau tunggu TTL 30 menit. |

## H. Rujukan dan atribusi

- [NASA FIRMS API / Area](https://firms.modaps.eosdis.nasa.gov/api/area/)
- [NASA FIRMS global fire map & disclaimer](https://firms.modaps.eosdis.nasa.gov/map/)
- [GEE: VIIRS S-NPP Collection 2](https://developers.google.com/earth-engine/datasets/catalog/NASA_LANCE_SNPP_VIIRS_C2)
- [geoBoundaries API (gbOpen CC BY 4.0)](https://www.geoboundaries.org/api.html)
- [Streamlit Community Cloud deployment](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)
- [Streamlit secret management](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)

**Atribusi untuk publikasi data:** NASA Land, Atmosphere Near real-time Capability for EOS (LANCE) / FIRMS, produk VIIRS active fire, tanggal akses; geoBoundaries ADM1 IDN (gbOpen, CC BY 4.0), dan atribusi basemap menurut penyedia.
