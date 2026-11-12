"""Validierung der Engine.

1. Abgleich mit der Original-Arbeitsmappe input/ETF_Simulation.xlsm
   (Ampel-Signal des S&P 500 und Kontoführung Spalte H/I) – benötigt openpyxl,
   sonst wird dieser Teil übersprungen.
2. Regressionstests aller Rechenregeln (dieselben wie `python ETF_Tool.py --selftest`).
3. Abgleich des synthetischen 2x-Hebels mit dem echten LevDAX-ETF (DBPE.DE), falls im
   Datenbestand vorhanden.

Aufruf im Projektordner:  python tool_src/validate_engine.py
"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True

import data as D  # noqa: E402
import engine as E  # noqa: E402

SRC = os.path.join(ROOT, "input", "ETF_Simulation.xlsm")
CACHE = os.path.join(ROOT, "data", "cache")


def excel_checks():
    out = []
    try:
        from openpyxl import load_workbook
    except ImportError:
        print("[Excel] openpyxl nicht installiert – Abgleich mit der Arbeitsmappe übersprungen.")
        return out
    if not os.path.exists(SRC):
        print(f"[Excel] {SRC} fehlt – Abgleich übersprungen.")
        return out
    wb = load_workbook(SRC, data_only=True, keep_vba=True)

    # 1) Ampel-Signal (Blatt US78378X1072 = S&P 500)
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
    mine, _ratio, _ma = E.signal_ampel(prices, 130, 1.05, 0.92, 19, 16)
    cmp_n = mismatch = 0
    for i, x in enumerate(states):
        if x not in ("grün", "rot"):
            continue
        cmp_n += 1
        if mine[i] != (1 if x == "grün" else 0):
            mismatch += 1
    out.append(("Excel: Ampel-Signal S&P 500 wie Original", mismatch <= max(2, cmp_n * 0.0005),
                f"{cmp_n} Tage, {mismatch} Abweichungen"))

    # 2) Kontoführung Spalte H (Wert vor Steuer) und I (Wert nach Steuer und Provision)
    ws = wb["Berechnungsblatt"]
    fee, tax = 5.9, 0.26375
    chk = bad = 0
    for r in range(2, 600):
        e_, f_, g_, h_, i_ = (ws.cell(row=r, column=c).value for c in (5, 6, 7, 8, 9))
        d_ = ws.cell(row=r, column=4).value
        if not all(isinstance(x, (int, float)) for x in (e_, f_, g_, h_, i_)):
            continue
        calc_h = round(e_ * f_, 2)
        fee_r = 0 if d_ == "Kasse" else fee
        calc_i = round((calc_h - fee_r) + ((g_ - calc_h + fee_r) * tax), 2)
        chk += 1
        if abs(calc_h - h_) > 0.011 or abs(calc_i - i_) > 0.011:
            bad += 1
    out.append(("Excel: Kontoführung Spalte H/I wie Original", bad == 0,
                f"{chk} Zeilen, {bad} Abweichungen"))
    return out


def load_cached(symbol):
    path = os.path.join(CACHE, symbol.replace("^", "_") + ".json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return E.Series.from_dict(json.load(f))


def leverage_check():
    dax, lev = load_cached("^GDAXI"), load_cached("DBPE.DE")
    if dax is None or lev is None:
        print("[Hebel] ^GDAXI oder DBPE.DE nicht im Datenbestand – Abgleich übersprungen.")
        return []
    syn = D.synthetic_leverage(dax, 2.0, rf_pct=2.0, spread_pct=0.3)
    smap = dict(zip(syn.dates, syn.close))
    lmap = {d: lev.value_at(i) for i, d in enumerate(lev.dates)}
    common = sorted(set(smap) & set(lmap))
    if len(common) < 500:
        return []
    yrs = (common[-1] - common[0]).days / 365.25
    c_syn = (smap[common[-1]] / smap[common[0]]) ** (1 / yrs) - 1
    c_real = (lmap[common[-1]] / lmap[common[0]]) ** (1 / yrs) - 1
    diffs = [math.log(smap[common[i]] / smap[common[i - 1]]) - math.log(lmap[common[i]] / lmap[common[i - 1]])
             for i in range(1, len(common))]
    te = E.stdev(diffs) * math.sqrt(252)
    return [("Synthetischer 2x-DAX vs. echter LevDAX (DBPE.DE)", abs(c_syn - c_real) < 0.015,
             f"{common[0]}..{common[-1]}: synthetisch {c_syn:.2%} p. a., echt {c_real:.2%} p. a., "
             f"Tracking Error {te:.2%}")]


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except Exception:                                       # noqa: BLE001
            pass
    results = list(excel_checks())
    results += [(r["name"], r["ok"], r["detail"]) for r in E.self_checks() + D.data_self_checks()]
    results += leverage_check()
    for name, ok, detail in results:
        print(f"[{'ok    ' if ok else 'FEHLER'}] {name}  ({detail})")
    n_ok = sum(1 for _, ok, _ in results if ok)
    print(f"\nERGEBNIS: {n_ok} ok / {len(results) - n_ok} fehlgeschlagen")
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
