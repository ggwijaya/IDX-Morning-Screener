# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file Streamlit web app (`streamlit_app.py`) that screens liquid Indonesian (IDX) stocks daily using technical signals, with end-of-day data from Yahoo Finance via `yfinance` (`.JK` tickers). It's deployed to Streamlit Community Cloud, which expects exactly `streamlit_app.py` and `requirements.txt` at the repo root. All UI text, comments, and docs are in Indonesian — keep new user-facing text in Indonesian.

## Commands

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

There are no tests or linters. To import the module without launching the app (e.g. to exercise the pure functions in a REPL or script), set the `SCREENER_TEST` env var — the `main()` guard at the bottom of the file skips startup:

```bash
SCREENER_TEST=1 python -c "import streamlit_app; ..."
```

## Architecture

The app is a pipeline, all in `streamlit_app.py`:

1. **Universe** — the `UNIVERSE` constant embeds ~195 candidate tickers (overridable via the sidebar "Universe kustom" expander; `parse_universe` normalizes the text). Dead/delisted tickers are silently skipped downstream.
2. **Fetch** — `fetch_batch` (defined inside `main()`) downloads OHLCV in batches of `BATCH=50` via `yf.download`, with 3 retries against Yahoo rate limits (HTTP 429). It's wrapped in `@st.cache_data(ttl=CACHE_TTL)` so data is cached 4 hours and shared across all visitors — this is deliberate to avoid hammering Yahoo; don't shorten the TTL or bypass the cache.
3. **Analyze** — `analyze_one` computes indicators (RSI, Stochastic, Bollinger, OBV, MFI, MACD — pure pandas, no TA library) and four boolean oversold-rebound combo signals (`combo1`–`combo4`) per stock; the score is 2.5 per combo, max 10. The combo definitions are documented in the caption near the end of `main()`; if you change them, update that caption and the `ProgressColumn` `max_value` to match.
4. **Rank & display** — `build_screen` ranks by liquidity (20-day mean transaction value, zero-volume days excluded), keeps top-N, then sorts by score. `main()` renders Top Picks as hand-built HTML (`picks_table_html`, styled by the `CSS` constant and `BADGES` list) plus a sortable `st.dataframe` and CSV export.

Key invariants:

- Pure computation (indicators, scoring, parsing) lives at module level; only `main()` touches `streamlit`/`yfinance` imports. Keep new logic testable by following this split.
- Adding a signal means touching three places: the `sig` dict and score formula in `analyze_one`, the `BADGES` list (for display), and the score caption.
- Number formatting uses Indonesian conventions (`_fmt` swaps `.`/`,`).

## Git Workflow

- **Always develop directly on the `dev` branch.** Never create feature branches.
- Before making changes, ensure you are on `dev`: `git checkout dev && git pull origin dev`
- Commit and push directly to `dev`: `git push -u origin dev`
- Do not open pull requests unless explicitly asked.
