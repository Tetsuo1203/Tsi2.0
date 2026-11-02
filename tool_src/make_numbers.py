"""Erzeugt Kennzahlen-Vergleiche für README/Dokumentation."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server as S

BASE = dict(capital=10000, fee_fixed=5.9, fee_pct=0, slippage_bps=5, tax_rate=26.375,
            tax_mode="accrued", loss_offset=True, cash_rate=2.0, exec_lag=1,
            defensive_is_cash=True)
AMPEL = {"sma_days": 130, "upper": 1.05, "lower": 0.92, "days_red": 19, "days_green": 16}

CASES = [
    ("S&P 500 (Index), RSL-Ampel", "^GSPC", "^GSPC", "ampel", AMPEL),
    ("S&P 500 (Index), 200-Tage-SMA", "^GSPC", "^GSPC", "sma", {"sma_days": 200, "band": 0.0}),
    ("S&P 500 (Index), Buy & Hold", "^GSPC", "^GSPC", "none", {}),
    ("DAX (Index), RSL-Ampel", "^GDAXI", "^GDAXI", "ampel", AMPEL),
    ("DAX (Index), Buy & Hold", "^GDAXI", "^GDAXI", "none", {}),
    ("DAX-ETF (EXS1), Ampel auf S&P", "EXS1.DE", "^GSPC", "ampel", AMPEL),
    ("MSCI World (IWDA), Ampel auf S&P", "IWDA.AS", "^GSPC", "ampel", AMPEL),
    ("MSCI World (IWDA), Buy & Hold", "IWDA.AS", "^GSPC", "none", {}),
]


def main():
    print(f"{'Strategie':34s} {'Zeitraum':23s} {'CAGR':>8s} {'B&H':>8s} "
          f"{'MaxDD':>8s} {'B&H-DD':>8s} {'Sharpe':>7s} {'Trades':>6s} {'Markt':>6s}")
    print("-" * 116)
    for name, risk, sig, stype, params in CASES:
        cfg = dict(BASE, risk=risk, signal=sig, signal_type=stype, params=params)
        try:
            r = S.run_config(cfg)
            m = r["metrics"]
            print(f"{name:34s} {m['start']}..{m['end']} {m['cagr']*100:7.2f}% "
                  f"{m['bench_cagr']*100:7.2f}% {m['max_drawdown']*100:7.2f}% "
                  f"{m['bench_max_drawdown']*100:7.2f}% {m['sharpe']:7.2f} "
                  f"{m['trades']:6d} {m['time_in_market']*100:5.1f}%")
        except Exception as e:                                  # noqa: BLE001
            print(f"{name}: FEHLER {e}")
    print()
    # Original-Nachbau
    presets = {p["id"]: p["config"] for p in S.PRESETS}
    r = S.run_config(presets["original-comparison"])
    m = r["metrics"]
    print(f"Original-Nachbau (2x/-2x DAX, monatlich): {m['start']}..{m['end']}  "
          f"Endkapital {m['end_capital']:,.2f} €  CAGR {m['cagr']*100:.2f}%  "
          f"MaxDD {m['max_drawdown']*100:.2f}%  Trades {m['trades']}")


if __name__ == "__main__":
    main()
