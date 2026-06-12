# -*- coding: utf-8 -*-
"""
IDX MORNING SCREENER — Streamlit web app
=========================================
Screening teknikal harian saham IDX terlikuid, siap di-hosting di
Streamlit Community Cloud (share.streamlit.io).

Alur: unduh OHLCV riil via yfinance (server-side, tanpa CORS) ->
ranking likuiditas (median nilai transaksi 60 hari) -> hitung sinyal
teknikal & skor -> tampilkan Top Picks + tabel lengkap + export CSV.

Data di-cache 4 jam dan dibagi ke semua pengunjung, sehingga Yahoo
tidak dihantam berulang-ulang.
"""

from __future__ import annotations

import os
import time
from datetime import datetime

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------
# KONFIGURASI
# ----------------------------------------------------------------------------
BATCH = 50            # ukuran batch download yfinance
PERIOD = "2y"         # periode data harian
MIN_BARS = 120        # minimal candle agar dianalisis
LIQ_WINDOW = 20       # window rata-rata nilai transaksi (hari)
CACHE_TTL = 4 * 3600  # cache data 4 jam (data EOD, tak perlu lebih sering)

# Universe kandidat (~195 saham IDX yang umumnya aktif). Bisa basi karena
# merger/delisting/IPO — saham tanpa data otomatis dilewati, dan ranking
# likuiditas dihitung dari data AKTUAL. Bisa di-override lewat sidebar.
UNIVERSE = """
BBCA BBRI BMRI BBNI BRIS BBTN ARTO BANK BBHI BBYB AMAR AGRO BTPS BNGA BDMN
BJBR BJTM NISP PNBN SDRA BFIN SRTG TUGU
TLKM ISAT EXCL TOWR TBIG MTEL SCMA MNCN FILM EMTK GOTO BUKA WIFI MTDL BELI
DCII MSTI MLPT
ADRO AADI PTBA ITMG HRUM INDY BUMI BRMS DOID MEDC ENRG ELSA AKRA PGAS PGEO
RAJA RATU BSSR ADMR TOBA CUAN PTRO DSSA SGER MCOL GEMS ABMM RMKE WINS HUMI
ANTM INCO TINS MDKA MBMA NCKL PSAB AMMN HRTA CITA
BREN POWR
SMGR INTP TPIA BRPT ESSA AVIA ARNA INKP TKIM CLEO MARK KRAS ISSP
UNVR ICBP INDF MYOR KLBF SIDO CPIN JPFA MAIN GGRM HMSP WIIM CMRY ULTJ ROTI
GOOD ADES AISA TBLA SIMP AALI LSIP DSNG TAPG SSMS
AMRT MIDI ACES MAPI MAPA ERAA RALS LPPF MDIY TURI MPMX
MIKA HEAL SILO PRDA TSPC
BSDE CTRA SMRA PWON ASRI PANI KIJA DMAS LPKR APLN BEST BKSL DILD
WIKA PTPP ADHI SSIA TOTL JSMR
ASII UNTR AUTO SMSM DRMA GJTL HEXA VKTR
ASSA BIRD SMDR TMAS PSSI BULL ELPI GIAA IATA
CDIA COIN DAAZ BMTR DEWA
""".split()

BADGES = [
    ("combo1", "RSI+STOCH JENUH JUAL", "b-teal"),
    ("combo2", "BB BAWAH + OBV NAIK", "b-gold"),
    ("combo3", "DIVERGENSI RSI+MACD", "b-vio"),
    ("combo4", "MFI<20 + VOL SPIKE", "b-red"),
]

CSS = """
<style>
.stApp {background:#eef0ec;}
.idx-head {background:#0a4646;border-bottom:3px solid #b8862b;border-radius:10px;
  padding:18px 22px;margin-bottom:6px;}
.idx-head h1 {color:#fff;font-size:24px;margin:0;letter-spacing:.04em;font-weight:700;}
.idx-head .sub {color:#9fc4be;font-family:monospace;font-size:12px;
  letter-spacing:.14em;text-transform:uppercase;margin-top:4px;}
.bd {display:inline-block;font-family:monospace;font-size:10px;font-weight:600;
  letter-spacing:.05em;padding:2px 7px;border-radius:4px;margin:1px 3px 1px 0;}
.b-teal{background:#dcebe7;color:#0a4646}.b-gold{background:#f0e3c4;color:#7a5b12}
.b-vio{background:#e4dff0;color:#473585}.b-red{background:#f3d6d6;color:#8a2b2b}
table.picks {width:100%;border-collapse:collapse;font-size:13.5px;background:#fff;
  border:1px solid #d7dbd4;border-radius:8px;overflow:hidden;}
table.picks th {font-family:monospace;font-size:10px;letter-spacing:.08em;
  text-transform:uppercase;color:#5b626c;background:#f4f6f2;text-align:right;
  padding:8px 11px;border-bottom:1px solid #d7dbd4;}
table.picks th.l {text-align:left;}
table.picks td {padding:8px 11px;border-bottom:1px solid #e7eae4;white-space:nowrap;}
table.picks td.num {font-family:monospace;text-align:right;}
table.picks td.code {font-family:monospace;font-weight:700;color:#0a4646;}
table.picks td.sig {white-space:normal;}
.up{color:#137a52}.down{color:#b23a3a}.score{font-weight:700;color:#0e5d5d}
</style>
"""


# ----------------------------------------------------------------------------
# INDIKATOR (pandas murni)
# ----------------------------------------------------------------------------
def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / n, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1 / n, min_periods=n).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def macd(close: pd.Series):
    ema12 = close.ewm(span=12, min_periods=12).mean()
    ema26 = close.ewm(span=26, min_periods=26).mean()
    line = ema12 - ema26
    signal = line.ewm(span=9, min_periods=9).mean()
    return line, signal


def stoch_k(close: pd.Series, high: pd.Series, low: pd.Series,
            n: int = 14, smooth: int = 3) -> pd.Series:
    ll = low.rolling(n).min()
    hh = high.rolling(n).max()
    k = 100 * (close - ll) / (hh - ll).replace(0, np.nan)
    return k.rolling(smooth).mean()


def bollinger_lower(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.Series:
    mid = close.rolling(n).mean()
    std = close.rolling(n).std()
    return mid - k * std


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0.0)
    return (direction * volume).cumsum()


def mfi(high: pd.Series, low: pd.Series, close: pd.Series,
        volume: pd.Series, n: int = 14) -> pd.Series:
    tp = (high + low + close) / 3
    flow = tp * volume
    delta = tp.diff()
    pos = flow.where(delta > 0, 0.0).rolling(n).sum()
    neg = flow.where(delta < 0, 0.0).rolling(n).sum()
    ratio = pos / neg.replace(0, np.nan)
    return 100 - 100 / (1 + ratio)


def bullish_divergence(close: pd.Series, r: pd.Series,
                       recent: int = 10, prior: int = 20) -> bool:
    """Harga membuat low lebih rendah dari periode sebelumnya, tapi RSI tidak."""
    if len(close) < recent + prior:
        return False
    c_recent, c_prior = close.iloc[-recent:], close.iloc[-(recent + prior):-recent]
    r_recent, r_prior = r.iloc[-recent:], r.iloc[-(recent + prior):-recent]
    if r_recent.isna().all() or r_prior.isna().all():
        return False
    return bool(c_recent.min() < c_prior.min() and r_recent.min() > r_prior.min())


def analyze_one(df: pd.DataFrame, min_price: float) -> dict | None:
    df = df.dropna(subset=["Close"]).copy()
    if len(df) < MIN_BARS:
        return None
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"].fillna(0)

    r = rsi(c)
    macd_line, macd_sig = macd(c)
    hist = macd_line - macd_sig
    k = stoch_k(c, h, l)
    bb_low = bollinger_lower(c)
    obv_line = obv(c, v)
    mfi_line = mfi(h, l, c, v)
    vol_sma20 = v.rolling(20).mean()

    close = float(c.iloc[-1])
    if close < min_price:
        return None

    last = lambda s: float(s.iloc[-1]) if pd.notna(s.iloc[-1]) else np.nan
    rsi_now, k_now, mfi_now = last(r), last(k), last(mfi_line)
    vol_ratio = float(v.iloc[-1] / vol_sma20.iloc[-1]) if vol_sma20.iloc[-1] else np.nan

    def ret(n):
        if len(c) <= n:
            return np.nan
        prev = c.iloc[-1 - n]
        return (close / prev - 1) * 100 if prev else np.nan

    # Hari tanpa transaksi (volume 0/NaN dari Yahoo) dikecualikan agar
    # rata-rata tidak tertarik ke bawah oleh baris hantu/libur.
    value = (c * v)
    value = value[v > 0].tail(LIQ_WINDOW)
    liq = float(value.mean()) if len(value) else 0.0

    # Kombo 2: low menyentuh/menembus Bollinger bawah dalam 5 hari terakhir,
    # sementara OBV naik dibanding 5 hari lalu (volume akumulasi masuk).
    bb_touch = bool(((l - bb_low).tail(5) <= 0).any()) if pd.notna(bb_low.iloc[-1]) else False
    obv_rising = len(obv_line) > 5 and obv_line.iloc[-1] > obv_line.iloc[-6]

    # Kombo 3: histogram MACD masih negatif tapi memendek 2 bar berturut-turut.
    hist_clean = hist.dropna()
    hist_shrinking = (
        len(hist_clean) >= 3 and hist_clean.iloc[-1] < 0
        and hist_clean.iloc[-1] > hist_clean.iloc[-2] > hist_clean.iloc[-3]
    )

    sig = {
        "combo1": pd.notna(rsi_now) and pd.notna(k_now) and rsi_now <= 30 and k_now < 20,
        "combo2": bb_touch and obv_rising,
        "combo3": bullish_divergence(c, r) and hist_shrinking,
        "combo4": pd.notna(mfi_now) and pd.notna(vol_ratio)
                  and mfi_now < 20 and vol_ratio >= 1.8,
    }
    score = 2.5 * sum(sig.values())
    return {
        "close": close, "ret1": ret(1), "ret5": ret(5), "ret20": ret(20),
        "rsi": rsi_now, "stoch": k_now, "mfi": mfi_now,
        "vol_ratio": vol_ratio, "liq": liq,
        "last_date": df.index[-1], "score": round(score, 1), **sig,
    }


def build_screen(prices: dict[str, pd.DataFrame], top_n: int, min_price: float) -> pd.DataFrame:
    rows = []
    for code, df in prices.items():
        try:
            res = analyze_one(df, min_price)
        except Exception:
            continue
        if res:
            rows.append({"code": code, **res})
    if not rows:
        return pd.DataFrame()
    table = pd.DataFrame(rows)
    table = table.sort_values("liq", ascending=False).head(top_n)
    return table.sort_values(["score", "liq"], ascending=[False, False]).reset_index(drop=True)


def signal_text(r) -> str:
    return " · ".join(label for key, label, _ in BADGES if r.get(key)) or "—"


# ----------------------------------------------------------------------------
# HELPER TAMPILAN
# ----------------------------------------------------------------------------
def _fmt(v, d=1):
    if pd.isna(v):
        return "—"
    return f"{v:,.{d}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _pct(v):
    if pd.isna(v):
        return "<span>—</span>"
    cls = "up" if v > 0 else "down" if v < 0 else ""
    return f'<span class="{cls}">{v:+.1f}</span>'


def picks_table_html(picks: pd.DataFrame) -> str:
    head = ("<tr><th class='l'>Kode</th><th>Close</th><th>1D%</th><th>20D%</th>"
            "<th>RSI</th><th>Vol×</th><th class='l'>Sinyal</th><th>Skor</th></tr>")
    rows = []
    for _, r in picks.iterrows():
        badges = "".join(f'<span class="bd {cls}">{label}</span>'
                         for key, label, cls in BADGES if r.get(key))
        rows.append(
            f"<tr><td class='code'>{r['code']}</td>"
            f"<td class='num'>{_fmt(r['close'], 0)}</td>"
            f"<td class='num'>{_pct(r['ret1'])}</td>"
            f"<td class='num'>{_pct(r['ret20'])}</td>"
            f"<td class='num'>{_fmt(r['rsi'], 0)}</td>"
            f"<td class='num'>{_fmt(r['vol_ratio'])}</td>"
            f"<td class='sig'>{badges}</td>"
            f"<td class='num score'>{r['score']:.1f}</td></tr>"
        )
    return f"<table class='picks'><thead>{head}</thead><tbody>{''.join(rows)}</tbody></table>"


def parse_universe(text: str) -> list[str]:
    toks = text.replace(",", " ").upper().split()
    return sorted({t.replace(".JK", "") for t in toks if t and not t.startswith("#")})


# ----------------------------------------------------------------------------
# APLIKASI STREAMLIT
# ----------------------------------------------------------------------------
def main():
    import streamlit as st
    import yfinance as yf

    st.set_page_config(page_title="IDX Morning Screener", page_icon="📈", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)

    @st.cache_data(ttl=CACHE_TTL, show_spinner=False)
    def fetch_batch(symbols: tuple) -> dict:
        data = None
        for attempt in range(3):
            try:
                data = yf.download(list(symbols), period=PERIOD, interval="1d",
                                   group_by="ticker", auto_adjust=True,
                                   threads=True, progress=False)
                break
            except Exception:
                time.sleep(4 * (attempt + 1))
        if data is None or data.empty:
            return {}
        out = {}
        for sym in symbols:
            try:
                df = data[sym] if isinstance(data.columns, pd.MultiIndex) else data
                df = df.dropna(subset=["Close"])
                if len(df):
                    out[sym.replace(".JK", "")] = df[["Open", "High", "Low", "Close", "Volume"]].copy()
            except Exception:
                pass
        return out

    # ---------- Sidebar ----------
    with st.sidebar:
        st.markdown("### ⚙️ Pengaturan")
        top_n = st.slider("Jumlah saham terlikuid", 50, 300, 200, step=25,
                          help="Diranking dari rata-rata nilai transaksi 20 hari (data aktual)")
        threshold = st.slider("Ambang skor Top Picks", 0.0, 10.0, 5.0, step=0.5)
        min_price = st.number_input("Harga minimum (Rp)", 50, 10000, 50, step=50)
        with st.expander("Universe kustom"):
            uni_text = st.text_area("Kode saham (tanpa .JK)", " ".join(UNIVERSE),
                                    height=180, label_visibility="collapsed")
        if st.button("🔄 Tarik ulang data", use_container_width=True,
                     help="Hapus cache & unduh ulang dari Yahoo"):
            fetch_batch.clear()
            st.rerun()
        st.caption(f"Data di-cache {CACHE_TTL // 3600} jam & dibagi ke semua pengunjung.")

    tickers = parse_universe(uni_text) or UNIVERSE
    symbols = [f"{t}.JK" for t in tickers]

    # ---------- Header ----------
    st.markdown(
        '<div class="idx-head"><h1>IDX MORNING SCREENER</h1>'
        '<div class="sub">Screening teknikal harian · saham terlikuid Bursa Efek Indonesia · '
        'data penutupan terakhir (yfinance)</div></div>',
        unsafe_allow_html=True,
    )

    # ---------- Unduh data (batched + cached) ----------
    batches = [symbols[i:i + BATCH] for i in range(0, len(symbols), BATCH)]
    prog = st.progress(0.0, text="Menyiapkan data harga …")
    prices: dict[str, pd.DataFrame] = {}
    for i, b in enumerate(batches):
        prices.update(fetch_batch(tuple(b)))
        done = min((i + 1) * BATCH, len(symbols))
        prog.progress((i + 1) / len(batches), text=f"Menyiapkan data … {done}/{len(symbols)} saham")
    prog.empty()

    if not prices:
        st.error(
            "Tidak ada data yang berhasil diunduh dari Yahoo Finance. Biasanya ini karena "
            "rate limit sementara dari sisi Yahoo. Tunggu beberapa menit lalu klik "
            "**Tarik ulang data** di sidebar."
        )
        st.stop()

    failed = len(symbols) - len(prices)
    table = build_screen(prices, top_n, float(min_price))
    if table.empty:
        st.warning("Tidak ada saham yang lolos filter dasar (harga minimum / panjang data).")
        st.stop()

    picks = table[table["score"] >= threshold]
    last_date = max(r["last_date"] for _, r in table.iterrows())

    # ---------- Ringkasan ----------
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Saham discreen", f"{len(table)}")
    c2.metric("Top picks", f"{len(picks)}", help=f"Skor ≥ {threshold:g}")
    c3.metric("Data terakhir", pd.Timestamp(last_date).strftime("%d %b %Y"))
    c4.metric("Gagal diunduh", f"{failed}", help="Kode delisting/berganti otomatis dilewati")

    # ---------- Top Picks ----------
    st.subheader(f"🏆 Top Picks (skor ≥ {threshold:g})")
    st.caption("Kandidat rebound: saham jenuh jual yang mulai menunjukkan tanda "
               "akumulasi / pembalikan momentum menurut aturan skor.")
    if len(picks):
        st.markdown(picks_table_html(picks), unsafe_allow_html=True)
    else:
        st.info("Belum ada saham yang lolos ambang skor. Turunkan ambang di sidebar.")

    # ---------- Tabel lengkap ----------
    st.subheader("📊 Semua hasil")
    disp = pd.DataFrame({
        "Kode": table["code"],
        "Close": table["close"],
        "1D%": table["ret1"], "5D%": table["ret5"], "20D%": table["ret20"],
        "RSI": table["rsi"], "Stoch": table["stoch"], "MFI": table["mfi"],
        "Vol×": table["vol_ratio"],
        "Liq (Rp M)": table["liq"] / 1e9,
        "Sinyal": table.apply(signal_text, axis=1),
        "Skor": table["score"],
    })
    st.dataframe(
        disp, use_container_width=True, height=560, hide_index=True,
        column_config={
            "Close": st.column_config.NumberColumn(format="%.0f"),
            "1D%": st.column_config.NumberColumn(format="%+.1f%%"),
            "5D%": st.column_config.NumberColumn(format="%+.1f%%"),
            "20D%": st.column_config.NumberColumn(format="%+.1f%%"),
            "RSI": st.column_config.NumberColumn(format="%.0f"),
            "Stoch": st.column_config.NumberColumn(format="%.0f"),
            "MFI": st.column_config.NumberColumn(format="%.0f"),
            "Vol×": st.column_config.NumberColumn(format="%.1f"),
            "Liq (Rp M)": st.column_config.NumberColumn(format="%.1f"),
            "Skor": st.column_config.ProgressColumn(format="%.1f", min_value=0, max_value=10),
        },
    )
    st.download_button(
        "⬇️ Download CSV", disp.to_csv(index=False).encode("utf-8"),
        file_name=f"idx-screen-{datetime.now():%Y%m%d}.csv", mime="text/csv",
    )

    # ---------- Catatan ----------
    st.caption(
        "**Skor**: tiap kombinasi +2,5 (maks 10) · "
        "**Kombo 1** RSI ≤ 30 & Stochastic %K < 20 (jenuh jual ganda) · "
        "**Kombo 2** low menyentuh Bollinger bawah (20, 2σ, ≤5 hari) & OBV naik (akumulasi) · "
        "**Kombo 3** divergensi bullish RSI & histogram MACD memendek (momentum berbalik) · "
        "**Kombo 4** MFI < 20 & lonjakan volume ≥ 1,8× (kelelahan jual)."
    )
    st.caption(
        "Liq = rata-rata nilai transaksi harian 20 hari (hari tanpa transaksi dikecualikan). "
        "Data EOD/delayed dari Yahoo Finance "
        "(tidak resmi, untuk riset pribadi). Sinyal teknikal adalah kondisi chart, **bukan "
        "rekomendasi atau nasihat investasi** — lakukan analisis lanjutan sebelum mengambil keputusan."
    )


if not os.environ.get("SCREENER_TEST"):
    main()
