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
LIQ_WINDOW = 60       # window median nilai transaksi (hari)
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
    ("breakout", "BREAKOUT 20D", "b-gold"),
    ("near52w", "DEKAT 52W HIGH", "b-gold"),
    ("golden", "GOLDEN CROSS", "b-teal"),
    ("macd", "MACD CROSS", "b-teal"),
    ("trend", "TREN NAIK", "b-teal"),
    ("volspike", "VOL SPIKE", "b-vio"),
    ("overbought", "RSI>80", "b-red"),
    ("below200", "DI BAWAH MA200", "b-red"),
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


def crossed_above_within(a: pd.Series, b: pd.Series, lookback: int) -> bool:
    diff = (a - b).dropna()
    if len(diff) < lookback + 1:
        return False
    window = diff.iloc[-(lookback + 1):]
    above = window > 0
    return bool(above.iloc[-1] and (~above.iloc[:-1]).any())


def analyze_one(df: pd.DataFrame, min_price: float) -> dict | None:
    df = df.dropna(subset=["Close"]).copy()
    if len(df) < MIN_BARS:
        return None
    c, h, v = df["Close"], df["High"], df["Volume"].fillna(0)

    sma20, sma50, sma200 = c.rolling(20).mean(), c.rolling(50).mean(), c.rolling(200).mean()
    r = rsi(c)
    macd_line, macd_sig = macd(c)
    vol_sma20 = v.rolling(20).mean()

    close = float(c.iloc[-1])
    if close < min_price:
        return None

    last = lambda s: float(s.iloc[-1]) if pd.notna(s.iloc[-1]) else np.nan
    s20, s50, s200 = last(sma20), last(sma50), last(sma200)
    rsi_now = last(r)
    vol_ratio = float(v.iloc[-1] / vol_sma20.iloc[-1]) if vol_sma20.iloc[-1] else np.nan

    def ret(n):
        if len(c) <= n:
            return np.nan
        prev = c.iloc[-1 - n]
        return (close / prev - 1) * 100 if prev else np.nan

    value = (c * v).tail(LIQ_WINDOW)
    liq = float(value.median()) if len(value) else 0.0

    prior20_high = h.iloc[-21:-1].max() if len(h) >= 21 else np.nan
    hi52 = c.rolling(252, min_periods=120).max().iloc[-1]

    sig = {
        "trend":    pd.notna(s20) and pd.notna(s50) and close > s20 > s50,
        "above200": pd.notna(s200) and close > s200,
        "breakout": pd.notna(prior20_high) and close > float(prior20_high),
        "near52w":  pd.notna(hi52) and close >= 0.97 * float(hi52),
        "golden":   crossed_above_within(sma50, sma200, 10),
        "macd":     crossed_above_within(macd_line, macd_sig, 3),
        "volspike": pd.notna(vol_ratio) and vol_ratio >= 1.8,
        "rsi_ok":   pd.notna(rsi_now) and 50 <= rsi_now <= 70,
        "overbought": pd.notna(rsi_now) and rsi_now > 80,
        "below200": pd.notna(s200) and close < s200,
    }
    r20 = ret(20)
    score = (
        2.0 * sig["trend"] + 1.0 * sig["above200"] + 2.0 * sig["breakout"]
        + 1.5 * sig["near52w"] + 1.5 * sig["golden"] + 1.0 * sig["macd"]
        + 1.0 * sig["volspike"] + 1.0 * sig["rsi_ok"]
        + (0.5 if (pd.notna(r20) and r20 > 0) else 0.0)
        - 1.0 * sig["overbought"] - 1.0 * sig["below200"]
    )
    return {
        "close": close, "ret1": ret(1), "ret5": ret(5), "ret20": r20,
        "rsi": rsi_now, "vol_ratio": vol_ratio, "liq": liq,
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
                          help="Diranking dari median nilai transaksi 60 hari (data aktual)")
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
    st.caption("Chart yang sedang berada dalam kondisi teknikal terkuat menurut aturan skor.")
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
        "RSI": table["rsi"], "Vol×": table["vol_ratio"],
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
            "Vol×": st.column_config.NumberColumn(format="%.1f"),
            "Liq (Rp M)": st.column_config.NumberColumn(format="%.1f"),
            "Skor": st.column_config.ProgressColumn(format="%.1f", min_value=0, max_value=11.5),
        },
    )
    st.download_button(
        "⬇️ Download CSV", disp.to_csv(index=False).encode("utf-8"),
        file_name=f"idx-screen-{datetime.now():%Y%m%d}.csv", mime="text/csv",
    )

    # ---------- Catatan ----------
    st.caption(
        "**Skor**: TREN NAIK (close>MA20>MA50) +2 · di atas MA200 +1 · BREAKOUT 20D +2 · "
        "DEKAT 52W HIGH +1,5 · GOLDEN CROSS (≤10 hari) +1,5 · MACD CROSS (≤3 hari) +1 · "
        "VOL SPIKE ≥1,8× +1 · RSI 50–70 +1 · return 20d positif +0,5 · "
        "penalti RSI>80 −1, di bawah MA200 −1."
    )
    st.caption(
        "Liq = median nilai transaksi harian 60 hari. Data EOD/delayed dari Yahoo Finance "
        "(tidak resmi, untuk riset pribadi). Sinyal teknikal adalah kondisi chart, **bukan "
        "rekomendasi atau nasihat investasi** — lakukan analisis lanjutan sebelum mengambil keputusan."
    )


if not os.environ.get("SCREENER_TEST"):
    main()
