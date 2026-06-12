# IDX Morning Screener

Screening teknikal harian saham-saham terlikuid Bursa Efek Indonesia,
berjalan sebagai web app Streamlit dengan data riil dari Yahoo Finance
(ticker `.JK`, end-of-day).

## Fitur
- Ranking **likuiditas aktual**: rata-rata nilai transaksi 20 hari, ambil top-N (default 200)
- Sinyal teknikal: 4 kombinasi rebound — (1) RSI ≤ 30 + Stochastic %K < 20,
  (2) sentuh Bollinger bawah + OBV naik, (3) divergensi bullish RSI + histogram
  MACD memendek, (4) MFI < 20 + lonjakan volume (kelelahan jual)
- Skor komposit + bagian **Top Picks** untuk pengecekan tiap pagi;
  kombinasi yang dipakai bisa dipilih (1–4) lewat sidebar
- Running text backtest: top picks hari sebelumnya (skor ≥ 5, 4 kombo) dinilai
  **HIT** (hijau) bila high hari berikutnya ≥ +1% dari close saat pick, selain itu
  **FAIL** (merah) — tanpa look-ahead, sinyal dihitung dari data sampai H-1 saja
- Tabel lengkap yang bisa di-sort, export CSV, universe kustom lewat sidebar
- Cache data 4 jam (dibagi ke semua pengunjung) supaya Yahoo tidak di-spam

## Deploy ke Streamlit Community Cloud (gratis)
1. Buat repository baru di GitHub, upload **kedua file ini** ke root repo:
   - `streamlit_app.py`
   - `requirements.txt`
2. Buka **https://share.streamlit.io** → login dengan GitHub → **Create app**.
3. Pilih repo & branch, isi *Main file path*: `streamlit_app.py` → **Deploy**.
4. Tunggu build (±1–2 menit). Kunjungan pertama akan mengunduh data
   (±1–2 menit untuk ~195 saham); kunjungan berikutnya instan karena cache.

## Jalankan lokal
```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Catatan penting
- **Rate limit Yahoo**: server cloud memakai IP bersama, kadang Yahoo menolak
  sementara (HTTP 429). App sudah punya retry; kalau tetap gagal, tunggu beberapa
  menit lalu klik "Tarik ulang data" di sidebar.
- **Universe**: daftar ~195 kandidat tertanam di kode dan bisa basi
  (merger/delisting/IPO). Saham mati otomatis dilewati. Edit langsung lewat
  sidebar (expander "Universe kustom") atau di konstanta `UNIVERSE`.
- **Data tidak resmi**: yfinance memakai endpoint publik Yahoo (bukan API resmi),
  EOD/delayed, ditujukan untuk riset pribadi.
- **Disclaimer**: sinyal teknikal = kondisi chart, bukan rekomendasi/nasihat
  investasi. Keputusan sepenuhnya tanggung jawab pengguna.
