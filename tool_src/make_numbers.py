"""Erzeugt die Kennzahlen-Tabellen für README/Anleitung aus dem gebauten ETF_Tool.py.

Aufruf im Projektordner:  python tool_src/make_numbers.py
(Nutzt den Datenbestand in data/cache; fehlende Reihen werden von Yahoo geladen.)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.dont_write_bytecode = True
os.chdir(ROOT)
import ETF_Tool as T  # noqa: E402

AMPEL = {"sma_days": 130, "upper": 1.05, "lower": 0.92, "days_red": 19, "days_green": 16}
SMA200 = {"sma_days": 200, "band": 0.0}
ORIGINAL_TAX = {"tax_rule": "securities", "tax_allowance": 0.0}

CASES = [
    ("RSL-Ampel, S&P 500 (Kursindex)", dict(risk="^GSPC", signal="^GSPC", signal_type="ampel", params=AMPEL)),
    ("RSL-Ampel, S&P 500 Total Return", dict(risk="^SP500TR", signal="^SP500TR", signal_type="ampel", params=AMPEL)),
    ("RSL-Ampel, DAX", dict(risk="^GDAXI", signal="^GDAXI", signal_type="ampel", params=AMPEL)),
    ("RSL-Ampel, DAX-ETF (EXS1), Signal DAX", dict(risk="EXS1.DE", signal="^GDAXI", signal_type="ampel", params=AMPEL)),
    ("RSL-Ampel, MSCI World (IWDA), Signal S&P", dict(risk="IWDA.AS", signal="^GSPC", signal_type="ampel", params=AMPEL)),
    ("200-Tage-SMA, MSCI World (IWDA)", dict(risk="IWDA.AS", signal="IWDA.AS", signal_type="sma", params=SMA200)),
    ("200-Tage-SMA, Bitcoin (BTC-EUR)", dict(T.COSTS_CRYPTO, risk="BTC-EUR", signal="BTC-EUR", signal_type="sma", params=SMA200)),
    ("Momentum 365, Bitcoin (BTC-EUR)", dict(T.COSTS_CRYPTO, risk="BTC-EUR", signal="BTC-EUR", signal_type="momentum",
                                             params={"momentum_days": 365})),
]


def pct(x, nd=2):
    return "–" if x is None else f"{x * 100:.{nd}f} %".replace(".", ",")


def num(x, nd=2):
    return "–" if x is None else f"{x:.{nd}f}".replace(".", ",")


def table(title, extra):
    print(f"\n### {title}\n")
    print("| Strategie | Zeitraum | CAGR | B&H | Max. DD | DD B&H | Sharpe | B&H | Calmar | B&H | Up/Down | Zeit im Risiko |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for name, cfg in CASES:
        try:
            r = T.run_config(dict(cfg, **extra))
        except Exception as e:  # noqa: BLE001
            print(f"| {name} | FEHLER: {e} |")
            continue
        m = r["metrics"]
        print(f"| {name} | {m['start'][:4]}–{m['end'][:4]} | {pct(m['cagr'])} | {pct(m['bench_cagr'])} | "
              f"{pct(m['max_drawdown'], 1)} | {pct(m['bench_max_drawdown'], 1)} | {num(m['sharpe'])} | "
              f"{num(m['bench_sharpe'])} | {num(m['calmar'])} | {num(m['bench_calmar'])} | "
              f"{pct(m['up_capture'], 0)} / {pct(m['down_capture'], 0)} | {pct(m['time_in_market'], 0)} |")


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass
    table("Standard (Teilfreistellung je Anlageklasse, Pauschbetrag 1.000 €)", {})
    table("Steuer wie im Original (keine Teilfreistellung, kein Pauschbetrag)", ORIGINAL_TAX)
    print("\n### Rollierende Renditen (Standard)\n")
    for name, cfg in CASES:
        try:
            r = T.run_config(cfg)
        except Exception:  # noqa: BLE001
            continue
        parts = []
        for h in r["rolling"]["horizons"]:
            if h["n"]:
                parts.append(f"{h['years']} J.: besser {pct(h['beats_share'], 0)}, Verlust "
                             f"{pct(h['strategy']['neg_share'], 0)} vs. {pct(h['benchmark']['neg_share'], 0)}")
        print(f"- {name}: " + "; ".join(parts))
    presets = {p["id"]: p["config"] for p in T.PRESETS}
    r = T.run_config(presets["original-comparison"])
    m = r["metrics"]
    print(f"\nOriginal-Nachbau (2x/-2x DAX, monatlich): {m['start']}..{m['end']}  Endkapital "
          f"{m['end_capital']:,.2f}  CAGR {pct(m['cagr'])}  MaxDD {pct(m['max_drawdown'], 1)}  "
          f"Orders {m['trades']}  B&H 2x DAX: CAGR {pct(m['bench_cagr'])}, MaxDD {pct(m['bench_max_drawdown'], 1)}")


if __name__ == "__main__":
    main()
