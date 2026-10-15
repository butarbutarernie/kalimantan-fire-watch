# Perbaikan Field Nama Desa NAMOBJ (v1.4.2)

Nama desa untuk popup hotspot dan garis batas desa dibaca dari kolom **NAMOBJ** pada file `Kalimantan_Batas_Desa.dbf` yang diberikan pengguna.

- `NAMOBJ` = nama desa / objek administrasi sesuai data sumber.
- `WADMKC` = kecamatan, `WADMKK` = kabupaten/kota, `WADMPR` = provinsi, `KDEPUM` = kode wilayah.
- 7 dari 7.234 rekaman memiliki perbedaan `NAMOBJ` terhadap `WADMKD`.
- 5 poligon memiliki `NAMOBJ` berupa `Area Tidak Terdefinisi`; popup menandainya sebagai nama belum diketahui, bukan nama desa.
- **Arsip NPZ harus ikut diganti.** Hanya mengganti kode Python tidak mengubah atribut lama yang sudah dibundel dalam NPZ.
- Data poligon dan sistem koordinat tidak diubah dari v1.4.1; yang diperbarui hanya label dan deteksi nama tidak terdefinisi.

## Cara memasang ke GitHub

1. Ekstrak ZIP, lalu salin `village_lookup.py`, `view_v14.py`, `streamlit_app.py` ke akar repository.
2. Masuk ke `data/` di GitHub, **ganti kelima berkas** `villages_lookup_kalbar.npz`, `villages_lookup_kalsel.npz`, `villages_lookup_kalteng.npz`, `villages_lookup_kaltim.npz`, `villages_lookup_kaltara.npz` dengan yang ada di paket. Pastikan unggah berkas `.npz` itu sendiri, bukan ZIP.
3. Ganti juga `data/villages_visual_130m.geojson.gz` bila memakai layer visual batas desa, serta `data/villages_lookup_manifest.json` dan `data/villages_QA.json` jika menyimpan manifest.
4. Commit dan tunggu deploy ulang; jika popup masih lama, gunakan Reboot App/Clear cache.
5. Pastikan sidebar mencatat 5/5 arsip dan klik hotspot; nama desa akan muncul **jika titik termasuk ke dalam poligon dengan NAMOBJ terdefinisi**.

**Penting:** jika popup masih menyatakan arsip tidak ada, periksa file pada `main/data/` GitHub terlebih dahulu. Masalah arsip belum terpasang **berbeda** dari pemilihan field `NAMOBJ`.

Sumber atribut administratif: shapefile pengguna. Tanggal versi dan izin publikasi batas desa wajib diperiksa sebelum repository publik.
