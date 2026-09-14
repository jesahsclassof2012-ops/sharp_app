# Sharp Signal V2

This branch replaces the live Streamlit app's legacy estimated-handle and
confidence-score model with transparent `Money % - Bets %` screening.

## Included

- `streamlit_app.py` imports and uses `sharp_core.py`.
- Card-local ScoresAndOdds parsing supports 2-, 3-, and 4-character team codes
  and hyphenated codes, including UTSA, TXST, MSST, MINN, SJSU, PITT, and M-OH.
- Totals use explicitly labeled **Best over** and **Best under** quotes; they do
  not use away/home prices.
- The UI separates split line, best executable line, and best price, and has
  the requested market, time, split-gap, ticket-share, and price filters.
- Snapshot comparisons are browser-session-only and are clearly labeled as
  non-persistent, not historical backtesting.

## Verification

Run `pytest -q` after installing `requirements.txt`. The suite includes V2 core
unit tests and a Streamlit parser integration test.

## Limitations

The app depends on ScoresAndOdds HTML labels/classes and session snapshots are
lost when the session restarts. No historical results or betting outcomes are
stored.
