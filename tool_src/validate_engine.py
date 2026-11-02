"""Validierung: Engine-Logik gegen die Original-Arbeitsmappe + Invarianten."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from openpyxl import load_workbook

import engine as E

SRC = r"input\ETF_Simulation.xlsm"


def main():
    wb = load_workbook(SRC, data_only=True, keep_vba=True)
    ok = 0
    fail = 0

    # ---------------------------------------------------------------- 1) Signal
    ws = wb["US78378X1072"]
    dates, prices, states = [], [], []
    for r in range(2, ws.max_row + 1):
        d = ws.cell(row=r, column=1).value
        p = ws.cell(row=r, column=2).value
        s = ws.cell(row=r, column=4).value
        if d is None or not isinstance(p, (int, float)):
            continue
        dates.append(E.pdate(d))
        prices.append(float(p))
        states.append(s if isinstance(s, str) else None)

    mine, ratio, ma = E.signal_ampel(prices, 130, 1.05, 0.92, 19, 16)
    cmp_n = mismatch = 0
    first_mm = []
    for i, x in enumerate(states):
        if x not in ("grün", "rot"):
            continue
        cmp_n += 1
        want = 1 if x == "grün" else 0
        if mine[i] != want:
            mismatch += 1
            if len(first_mm) < 8:
                first_mm.append((dates[i].isoformat(), x, "grün" if mine[i] else "rot",
                                 round(ratio[i], 4) if ratio[i] else None))
    print(f"[1] Signal US78378X1072: {cmp_n} Vergleichstage, {mismatch} Abweichungen")
    for row in first_mm:
        print("      ", row)
    ok += 1 if mismatch <= max(2, cmp_n * 0.0005) else 0
    fail += 0 if mismatch <= max(2, cmp_n * 0.0005) else 1

    # ------------------------------------------------- 2) Kontofuehrung (Spalte I)
    ws = wb["Berechnungsblatt"]
    fee, tax = 5.9, 0.26375
    chk = bad = 0
    examples = []
    for r in range(2, 600):
        E_ = ws.cell(row=r, column=5).value      # Kurs
        F_ = ws.cell(row=r, column=6).value      # Stueck
        G_ = ws.cell(row=r, column=7).value      # Anschaffungskosten
        H_ = ws.cell(row=r, column=8).value      # Wert vor Steuern
        I_ = ws.cell(row=r, column=9).value      # Wert nach Steuern/Provision
        D_ = ws.cell(row=r, column=4).value
        if not all(isinstance(x, (int, float)) for x in (E_, F_, G_, H_, I_)):
            continue
        calc_h = round(E_ * F_, 2)
        calc_i = round((calc_h - (0 if D_ == "Kasse" else fee)) +
                       ((G_ - calc_h + (0 if D_ == "Kasse" else fee)) * tax), 2)
        chk += 1
        if abs(calc_h - H_) > 0.011 or abs(calc_i - I_) > 0.011:
            bad += 1
            if len(examples) < 5:
                examples.append((r, H_, calc_h, I_, calc_i))
    print(f"[2] Kontofuehrung Spalte H/I: {chk} Zeilen geprueft, {bad} Abweichungen")
    for e in examples:
        print("      ", e)
    ok += 1 if bad == 0 else 0
    fail += 0 if bad == 0 else 1

    # ----------------------------------------------------- 3) Engine-Invarianten
    import math
    n = 800
    dates = [E.date(2000, 1, 3) + E.timedelta(days=i) for i in range(n)]
    px = [100.0 * (1.0004 ** i) for i in range(n)]
    s = E.Series("TEST", dates, px, list(px), name="Test", currency="EUR")
    cfg = {"risk": "TEST", "signal": "TEST", "signal_type": "none",
           "params": {}, "capital": 10000.0, "fee_fixed": 0.0, "fee_pct": 0.0,
           "slippage_bps": 0.0, "tax_rate": 0.0, "tax_mode": "none", "exec_lag": 0,
           "defensive_is_cash": True, "cash_rate": 0.0}
    r = E.run_backtest(s, s, cfg)
    ratio_end = r["equity"]["strategy"][-1] / 10000.0
    expect = px[-1] / px[0]
    print(f"[3a] Buy&Hold ohne Kosten: Faktor {ratio_end:.6f} erwartet {expect:.6f}")
    good = abs(ratio_end - expect) < 1e-6
    ok += 1 if good else 0
    fail += 0 if good else 1

    # Kauf + Verkauf mit Gebuehr: 30 Tage steigend, dann fallend -> genau 2 Gebuehren
    d2 = [E.date(2001, 1, 1) + E.timedelta(days=i) for i in range(61)]
    px2 = [100.0 + i for i in range(31)] + [130.0 - (i + 1) for i in range(30)]
    s2 = E.Series("RND", d2, px2, list(px2))
    cfg2 = dict(cfg, fee_fixed=5.0, capital=10000.0, tax_mode="none",
                signal_type="momentum", params={"momentum_days": 1})
    r2 = E.run_backtest(s2, s2, cfg2)
    tr = r2["trades"]
    units = tr[0]["units"] if tr else 0.0
    expect2 = units * tr[-1]["price"] - 5.0 if tr else 0.0
    end2 = r2["equity"]["strategy"][-1]
    fees2 = sum(t["fee"] for t in tr)
    print(f"[3b] Kauf/Verkauf mit 5 EUR Gebuehr: {end2:.2f} (erwartet {expect2:.2f}), "
          f"Trades={len(tr)}, Gebuehren={fees2:.2f}")
    good = (abs(end2 - expect2) < 0.05 and len(tr) == 2 and abs(fees2 - 10.0) < 1e-9
            and tr[0]["action"] == "KAUF" and tr[1]["action"] == "VERKAUF")
    ok += 1 if good else 0
    fail += 0 if good else 1

    # Steuer: Gewinn 1000 EUR -> 263,75 EUR Steuer
    up = [100.0, 110.0] + [110.0] * 59
    s3 = E.Series("UP", list(s2.dates), up, list(up))
    st3 = E.Series("SIG3", list(s2.dates), [1.0] * 2 + [0.0] * 59, [1.0] * 2 + [0.0] * 59)
    cfg3 = dict(cfg, fee_fixed=0.0, tax_mode="accrued", tax_rate=26.375, slippage_bps=0,
                signal_type="none")
    r3 = E.run_backtest(s3, st3, cfg3)
    # Bei Tag 2 (Kurs 110) Wert: 11000 - Steuer auf 1000 = 10736.25
    day2 = r3["equity"]["strategy"][1]
    print(f"[3c] Steuer accrued: Tag2 {day2:.2f} (erwartet 10736.25)")
    good = abs(day2 - 10736.25) < 0.02
    ok += 1 if good else 0
    fail += 0 if good else 1

    # Benchmark = Buy&Hold muss mit Signal 'none' identisch sein
    print(f"[3d] Benchmark vs. immer-investiert: {r['equity']['benchmark'][-1]:.2f} vs "
          f"{r['equity']['strategy'][-1]:.2f}")
    good = abs(r["equity"]["benchmark"][-1] - r["equity"]["strategy"][-1]) < 0.02
    ok += 1 if good else 0
    fail += 0 if good else 1

    # Invarianz: Equity > 0, Drawdown <= 0, Monats-/Jahresmatrizen plausibel
    assert all(v > 0 for v in r["equity"]["strategy"])
    assert all(v <= 1e-12 for v in r["equity"]["drawdown"])
    yrs = r["yearly"]
    prod = 1.0
    for y in yrs:
        prod *= (1.0 + y["strategy"])
    print(f"[4] Jahresrenditen verkettet: {prod:.4f} vs Gesamt {1 + r['metrics']['total_return']:.4f}")
    good = abs(prod - (1 + r["metrics"]["total_return"])) < 0.02
    ok += 1 if good else 0
    fail += 0 if good else 1

    print(f"\nERGEBNIS: {ok} ok / {fail} fehlgeschlagen")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
