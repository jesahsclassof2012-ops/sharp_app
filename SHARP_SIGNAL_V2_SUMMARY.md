# Sharp Signal V2 Implementation Notes

The running Streamlit application is upgraded on this branch. It uses the V2
core parsers and displays observed split data rather than confidence labels or
betting recommendations.

The table exposes matchup, start time, market, selection, bets and money share,
their gap, signal, split line, best line, best price, break-even percentage,
line-versus-split, data quality, and refresh time. Best Over and Best Under are
kept separate from away/home quotes.

Tests cover team codes, spreads, totals, American odds, break-even probability,
percentage imputation, favorable spread/total movement, and app integration.
Run `pytest -q` to obtain the current result for the environment.
