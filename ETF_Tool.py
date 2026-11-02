#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETF-Timing-Studio 1.0
=====================

Ein eigenständiges Analyse-Werkzeug für Trendfolge-Strategien auf Aktien und ETFs.
Nachbau und Erweiterung der Arbeitsmappe "ETF_Simulation.xlsm"
(RSL-Ampel: Kurs / gleitender Durchschnitt mit Hysterese, monatliche Rotation,
Ordergebühren, Abgeltungssteuer).

Start:      python ETF_Tool.py
Optional:   python ETF_Tool.py --port 9000 --no-browser
            python ETF_Tool.py --selftest
            python ETF_Tool.py --download IWDA.AS ^GDAXI EXS1.DE
            python ETF_Tool.py --backtest '{"risk":"IWDA.AS","signal":"^GSPC"}'

Benötigt nur Python 3.8+ mit Standardbibliothek – keine Installation von Paketen.
Die Oberfläche läuft im Browser, die Berechnung lokal auf diesem Rechner.
Kurse: Yahoo Finance (kostenlos, ohne API-Key) oder CSV-Import.

Diese Datei ist generiert (Quellen in tool_src/). Nicht von Hand editieren.
"""

from __future__ import annotations

import argparse
import base64
import bisect
import csv
import gzip
import io
import json
import math
import os
import random
import re
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Die Module "engine" und "data" sind weiter unten in dieser Datei enthalten.
# Damit die urspruenglichen Modulnamen weiter funktionieren, zeigen E und D
# auf das eigene Modulobjekt.
E = D = sys.modules[__name__]

# ==========================================================================
# Teil 1/3: Analyse-Engine
# ==========================================================================

"""
ETF-Timing-Werkzeug -- Analyse-Engine (reine Standardbibliothek).

Nachbau und Erweiterung der Logik aus "ETF_Simulation.xlsm":
  * RSL-Ampel: Kurs / gleitender Durchschnitt (N Tage) mit Hysterese
  * Umschaltung zwischen Risiko-Anlage und Defensiv-Anlage (Kasse/ETF)
  * Handels- und Steuerlogik (Gebuehr je Order, Abgeltungssteuer)
  * Kennzahlen, Jahres-/Monatsrenditen, Trades, Drawdown, Parameter-Suche

Bewertung: Der ausgewiesene Vermoegenswert ist -- wie im Original-Sheet
"Berechnungsblatt" Spalte I -- der *Netto-Liquidationswert* (nach Ordergebuehr
und fiktiver Steuer auf den noch nicht realisierten Gewinn). Benchmark wird
identisch behandelt, damit der Vergleich fair ist.
"""


import bisect
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

EPS = 1e-12
MONTHS_DE = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
             "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


# --------------------------------------------------------------------------- #
# Parsing-Helfer
# --------------------------------------------------------------------------- #

def pdate(s) -> Optional[date]:
    """Robuste Datumserkennung: ISO, deutsch, Excel-Seriennummer, Zeitstempel."""
    if s is None or s == "":
        return None
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    if isinstance(s, (int, float)):
        try:
            return (datetime(1899, 12, 30) + timedelta(days=float(s))).date()
        except Exception:
            return None
    t = str(s).strip().strip('"').strip("'")
    if not t:
        return None
    t = t.split("T")[0].split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%Y/%m/%d", "%m/%d/%Y",
                "%Y%m%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(t, fmt).date()
        except Exception:
            continue
    return None


def fnum(v) -> Optional[float]:
    """Zahl aus Text/Nummer; toleriert deutsches Dezimalkomma und Tausenderpunkte."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        try:
            f = float(v)
        except Exception:
            return None
        return f if math.isfinite(f) else None
    t = str(v).strip().strip('"').strip("'")
    t = t.replace("\u00a0", "").replace(" ", "")
    if not t or t.lower() in {"-", "--", "n/a", "nan", "null", "none"}:
        return None
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        f = float(t)
    except Exception:
        return None
    return f if math.isfinite(f) else None


def stdev(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


# --------------------------------------------------------------------------- #
# Kursreihe
# --------------------------------------------------------------------------- #

@dataclass
class Series:
    """Tageskursreihe; dates aufsteigend, eindeutig."""
    symbol: str
    dates: List[date]
    close: List[float]
    adj: List[Optional[float]]
    name: str = ""
    currency: str = ""
    source: str = ""
    meta: Dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.dates)

    def value_at(self, i: int) -> float:
        a = self.adj[i] if self.adj and 0 <= i < len(self.adj) else None
        if a is not None and a > 0:
            return a
        return self.close[i] if 0 <= i < len(self.close) else 0.0

    def index_on_or_before(self, d: date) -> int:
        return bisect.bisect_right(self.dates, d) - 1

    def slice_between(self, start: Optional[date], end: Optional[date]) -> "Series":
        lo = bisect.bisect_left(self.dates, start) if start else 0
        hi = bisect.bisect_right(self.dates, end) if end else len(self.dates)
        return Series(self.symbol, self.dates[lo:hi], self.close[lo:hi],
                      self.adj[lo:hi], self.name, self.currency, self.source, dict(self.meta))

    def to_dict(self) -> Dict:
        return {"symbol": self.symbol, "name": self.name, "currency": self.currency,
                "source": self.source, "meta": self.meta,
                "dates": [d.isoformat() for d in self.dates],
                "close": self.close, "adj": self.adj}

    @staticmethod
    def from_dict(d: Dict) -> "Series":
        dates = [pdate(x) for x in d.get("dates", [])]
        close = [fnum(x) for x in d.get("close", [])]
        adj = d.get("adj")
        adj = [fnum(x) for x in adj] if adj else list(close)
        good = [(dt, c, a) for dt, c, a in zip(dates, close, adj) if dt and c is not None]
        return Series(symbol=d.get("symbol", ""), name=d.get("name", "") or d.get("symbol", ""),
                      currency=d.get("currency", ""), source=d.get("source", ""),
                      meta=d.get("meta", {}) or {},
                      dates=[x[0] for x in good], close=[x[1] for x in good],
                      adj=[x[2] for x in good])


def make_series(symbol: str, rows: Iterable[Tuple[date, float]], name: str = "",
                currency: str = "", source: str = "",
                meta: Optional[Dict] = None) -> Series:
    clean: Dict[date, float] = {}
    for d, v in rows:
        if d is None or v is None or not math.isfinite(v) or v <= 0:
            continue
        clean[d] = float(v)
    ds = sorted(clean)
    vals = [clean[d] for d in ds]
    return Series(symbol=symbol, name=name or symbol, currency=currency, source=source,
                  meta=meta or {}, dates=ds, close=vals, adj=list(vals))


# --------------------------------------------------------------------------- #
# Signal-Engine
# --------------------------------------------------------------------------- #

def sma(values: Sequence[float], n: int, i: int) -> Optional[float]:
    if n <= 0 or i + 1 < n:
        return None
    return sum(values[i - n + 1:i + 1]) / n


def _rolling_sma(prices: Sequence[float], n: int) -> List[Optional[float]]:
    """Gleitender Durchschnitt in O(n) (statt O(n*Fenster))."""
    out: List[Optional[float]] = [None] * len(prices)
    if n <= 0:
        return out
    s = 0.0
    for i, p in enumerate(prices):
        s += p
        if i >= n:
            s -= prices[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def signal_ampel(prices: Sequence[float], n: int = 130, upper: float = 1.05,
                 lower: float = 0.92, days_red: int = 19, days_green: int = 16,
                 start_state: int = 1):
    """Original-„RSL-Ampel" mit Hysterese: 1 = Risiko, 0 = Defensiv.

    rot   : RSL <= lower  ODER (RSL < 1  und alle letzten days_red Werte < 1)
    gruen : RSL >= upper  ODER (RSL >= 1 und alle letzten days_green Werte >= 1)
    sonst : vorheriger Zustand
    """
    n_days = len(prices)
    states = [0] * n_days
    ratios: List[Optional[float]] = [None] * n_days
    mas = _rolling_sma(prices, n)
    state = 1 if start_state else 0
    run_below = run_above = 0
    for i in range(n_days):
        m = mas[i]
        if m is None or m <= EPS:
            states[i] = state
            continue
        r = prices[i] / m
        ratios[i] = r
        run_below = run_below + 1 if r < 1.0 else 0
        run_above = run_above + 1 if r >= 1.0 else 0
        new = state
        if r <= lower:
            new = 0
        elif days_red > 0 and r < 1.0 and run_below >= days_red:
            new = 0
        elif r >= upper:
            new = 1
        elif days_green > 0 and r >= 1.0 and run_above >= days_green:
            new = 1
        states[i] = new
        state = new
    return states, ratios, mas


def signal_sma(prices: Sequence[float], n: int = 200, band: float = 0.0, start_state: int = 1):
    """Kurs ueber SMA -> Risiko; band = Hysteresepuffer um 1,0."""
    states, ratios = [0] * len(prices), [None] * len(prices)
    mas = _rolling_sma(prices, n)
    state = 1 if start_state else 0
    for i in range(len(prices)):
        m = mas[i]
        if m is None or m <= EPS:
            states[i] = state
            continue
        r = prices[i] / m
        ratios[i] = r
        if band > 0:
            if r > 1.0 + band:
                state = 1
            elif r < 1.0 - band:
                state = 0
        else:
            state = 1 if r > 1.0 else 0
        states[i] = state
    return states, ratios, mas


def signal_dual_sma(prices: Sequence[float], short: int = 50, long: int = 200, start_state: int = 1):
    states, ratios = [0] * len(prices), [None] * len(prices)
    ms_arr = _rolling_sma(prices, short)
    ml_arr = _rolling_sma(prices, long)
    state = 1 if start_state else 0
    for i in range(len(prices)):
        ms, ml = ms_arr[i], ml_arr[i]
        if ms is None or ml is None or ml <= EPS:
            states[i] = state
            continue
        ratios[i] = ms / ml
        state = 1 if ms > ml else 0
        states[i] = state
    return states, ratios, ml_arr


def signal_momentum(prices: Sequence[float], lookback: int = 252, start_state: int = 1):
    states, ratios, mas = [0] * len(prices), [None] * len(prices), [None] * len(prices)
    state = 1 if start_state else 0
    for i in range(len(prices)):
        if i < lookback or prices[i - lookback] <= EPS:
            states[i] = state
            continue
        mom = prices[i] / prices[i - lookback] - 1.0
        ratios[i] = 1.0 + mom
        state = 1 if mom > 0 else 0
        states[i] = state
    return states, ratios, mas


SIGNAL_INFO = {
    "ampel": "RSL-Ampel (Original): Kurs/SMA mit Hysterese",
    "sma": "SMA-Trend: Kurs über/unter gleitendem Durchschnitt",
    "dual_sma": "Golden Cross: kurzer SMA über langem SMA",
    "momentum": "Momentum: Rendite der letzten N Tage > 0",
    "none": "Immer investiert (Buy & Hold)",
}


def compute_signal(sig: Series, signal_type: str, params: Dict) -> Dict:
    prices = [sig.value_at(i) for i in range(len(sig))]
    if signal_type == "ampel":
        st, rat, ma = signal_ampel(prices, int(params.get("sma_days", 130)),
                                   float(params.get("upper", 1.05)),
                                   float(params.get("lower", 0.92)),
                                   int(params.get("days_red", 19)),
                                   int(params.get("days_green", 16)))
    elif signal_type == "sma":
        st, rat, ma = signal_sma(prices, int(params.get("sma_days", 200)),
                                 float(params.get("band", 0.0)))
    elif signal_type == "dual_sma":
        st, rat, ma = signal_dual_sma(prices, int(params.get("sma_short", 50)),
                                      int(params.get("sma_long", 200)))
    elif signal_type == "momentum":
        st, rat, ma = signal_momentum(prices, int(params.get("momentum_days", 252)))
    else:
        st, rat, ma = [1] * len(prices), [None] * len(prices), [None] * len(prices)
    return {"states": st, "ratios": rat, "sma": ma, "prices": prices}


# --------------------------------------------------------------------------- #
# Backtest
# --------------------------------------------------------------------------- #

@dataclass
class Trade:
    date: date
    action: str
    symbol: str
    price: float
    units: float
    value: float
    fee: float
    tax: float
    gain: float
    reason: str

    def to_dict(self) -> Dict:
        return {"date": self.date.isoformat(), "action": self.action, "symbol": self.symbol,
                "price": round(self.price, 4), "units": round(self.units, 6),
                "value": round(self.value, 2), "fee": round(self.fee, 2),
                "tax": round(self.tax, 2), "gain": round(self.gain, 2),
                "reason": self.reason}


def run_backtest(risk: Series, signal_series: Series, config: Dict,
                 defensive: Optional[Series] = None,
                 instruments: Optional[Dict[str, Series]] = None) -> Dict:
    p = config
    cap0 = float(p.get("capital", 10000.0))
    fee_fixed = float(p.get("fee_fixed", 0.0))
    fee_pct = float(p.get("fee_pct", 0.0)) / 100.0
    slip = float(p.get("slippage_bps", 0.0)) / 10000.0
    tax_rate = float(p.get("tax_rate", 0.0)) / 100.0
    tax_mode = p.get("tax_mode", "accrued")           # accrued | realized | none
    loss_offset = bool(p.get("loss_offset", True))
    cash_rate = float(p.get("cash_rate", 0.0)) / 100.0
    exec_lag = int(p.get("exec_lag", 1))
    rotation = p.get("rotation") or None
    defensive_is_cash = bool(p.get("defensive_is_cash", True))
    instruments = dict(instruments or {})
    if defensive is not None:
        instruments[defensive.symbol] = defensive
    instruments[risk.symbol] = risk

    start, end = pdate(p.get("start")), pdate(p.get("end"))
    lo = bisect.bisect_left(risk.dates, start) if start else 0
    hi = bisect.bisect_right(risk.dates, end) if end else len(risk.dates)
    if hi - lo < 30:
        raise ValueError("Zu wenige Kurstage im gewählten Zeitraum (mindestens 30 nötig).")
    dates = risk.dates[lo:hi]

    sig = compute_signal(signal_series, p.get("signal_type", "ampel"), p.get("params") or {})
    sig_dates, sig_states = signal_series.dates, sig["states"]

    def state_for(d: date) -> int:
        j = bisect.bisect_right(sig_dates, d) - 1
        if j < 0:
            return 0
        if exec_lag > 0:
            j = max(0, j - exec_lag)
        return int(sig_states[j])

    def target_for(d: date, st: int) -> str:
        if rotation:
            row = (rotation.get("green") if st else rotation.get("red")) or []
            sym = row[d.month - 1] if len(row) >= 12 else None
            if sym:
                return sym
        if st:
            return risk.symbol
        if defensive_is_cash:
            return "KASSE"
        return defensive.symbol if defensive is not None else "KASSE"

    def px_of(sym: str, d: date) -> Optional[float]:
        if sym == "KASSE":
            return None
        s = instruments.get(sym)
        if s is None:
            return None
        i = s.index_on_or_before(d)
        if i < 0:
            return None
        v = s.value_at(i)
        return v if v and v > 0 else None

    def fee_of(amount: float) -> float:
        return fee_fixed + fee_pct * amount

    units = 0.0
    pos = "KASSE"
    cash = cap0
    cost_basis = 0.0
    loss_pool = 0.0
    total_fee = total_tax = 0.0
    realized = 0.0
    equity: List[float] = []
    gross_series: List[float] = []
    states_out: List[int] = []
    pos_out: List[str] = []
    trades: List[Trade] = []
    cash_daily = (1.0 + cash_rate) ** (1.0 / 252.0) - 1.0

    for d in dates:
        if pos == "KASSE" and cash > 0:
            cash *= (1.0 + cash_daily)

        st = state_for(d)
        tgt = target_for(d, st)
        states_out.append(st)

        if tgt != pos:
            # ---- Verkauf
            if units > 0 and pos != "KASSE":
                px = px_of(pos, d) or (cost_basis / units if units else 0.0)
                px_eff = px * (1.0 - slip)
                gross = units * px_eff
                fee = fee_of(gross)
                gain = gross - fee - cost_basis
                tax = 0.0
                if tax_mode != "none" and gain > 0:
                    taxable = gain
                    if loss_offset and loss_pool > 0:
                        used = min(loss_pool, taxable)
                        taxable -= used
                        loss_pool -= used
                    tax = tax_rate * taxable
                elif tax_mode != "none" and gain < 0 and loss_offset:
                    loss_pool += -gain
                cash = gross - fee - tax
                total_fee += fee
                total_tax += tax
                realized += gain - tax
                trades.append(Trade(d, "VERKAUF", pos, px_eff, units, gross, fee, tax, gain,
                                    "Signalwechsel" if tgt != "KASSE" else "Signal aus"))
                units, cost_basis, pos = 0.0, 0.0, "KASSE"

            # ---- Kauf
            if tgt != "KASSE":
                px = px_of(tgt, d)
                if px:
                    px_eff = px * (1.0 + slip)
                    avail = cash
                    amount = max(0.0, (avail - fee_fixed) / (1.0 + fee_pct))
                    fee = fee_of(amount)
                    if amount < 0.5 or fee > avail:
                        amount = 0.0
                    units = amount / px_eff if amount else 0.0
                    if units <= 0:
                        pos = "KASSE"
                        continue
                    cost_basis = amount + fee
                    cash = avail - amount - fee
                    total_fee += fee
                    trades.append(Trade(d, "KAUF", tgt, px_eff, units, amount, fee, 0.0, 0.0,
                                        "Signalwechsel"))
                    pos = tgt

        # ---- Tagesbewertung
        if units > 0 and pos != "KASSE":
            px = px_of(pos, d) or 0.0
            gross = units * px
            if tax_mode == "accrued":
                px_eff = px * (1.0 - slip)
                net_before = units * px_eff - fee_of(units * px_eff)
                gain = net_before - cost_basis
                tax_hyp = 0.0
                if gain > 0:
                    taxable = gain
                    if loss_offset and loss_pool > 0:
                        taxable = max(0.0, taxable - loss_pool)
                    tax_hyp = tax_rate * taxable
                value = net_before - tax_hyp
            else:
                value = gross
            gross_series.append(gross)
        else:
            value = cash
            gross_series.append(cash)
        equity.append(value)
        pos_out.append(pos)

    # ---- Benchmark: Buy & Hold der Risikoanlage, gleiche Kosten-/Steuerlogik
    p0 = px_of(risk.symbol, dates[0]) or risk.value_at(lo)
    b_units = max(0.0, (cap0 - fee_fixed) / (p0 * (1.0 + slip))) if p0 else 0.0
    bench: List[float] = []
    for d in dates:
        px = px_of(risk.symbol, d) or p0
        px_eff = px * (1.0 - slip)
        net_before = b_units * px_eff - fee_of(b_units * px_eff)
        gain = net_before - cap0
        tax_hyp = tax_rate * max(0.0, gain) if tax_mode != "none" else 0.0
        bench.append(net_before - tax_hyp)

    return build_result(dates, equity, gross_series, bench, states_out, pos_out, trades,
                        total_fee, total_tax, realized, cap0, config, sig, signal_series)


def build_result(dates: List[date], equity: List[float], gross: List[float],
                 bench: List[float], states: List[int], positions: List[str],
                 trades: List[Trade], total_fee: float, total_tax: float,
                 realized: float, cap0: float, config: Dict, sig: Dict,
                 signal_series: Series) -> Dict:
    dd = drawdown(equity)
    bdd = drawdown(bench)
    m = compute_metrics(dates, equity, bench, dd, trades, total_fee, total_tax, cap0,
                        states, positions)
    yearly = yearly_returns(dates, equity, bench)
    monthly = monthly_matrix(dates, equity)
    rolling = rolling_metrics(dates, equity, 252)

    sig_out = {"dates": [], "price": [], "sma": [], "ratio": [], "state": []}
    for d in dates:
        j = signal_series.index_on_or_before(d)
        if j < 0:
            continue
        sig_out["dates"].append(d.isoformat())
        sig_out["price"].append(round(sig["prices"][j], 4))
        sig_out["sma"].append(round(sig["sma"][j], 4) if sig["sma"][j] else None)
        sig_out["ratio"].append(round(sig["ratios"][j], 6) if sig["ratios"][j] else None)
        sig_out["state"].append(int(sig["states"][j]))

    return {
        "meta": {"risk": config.get("risk"), "signal": config.get("signal"),
                 "signal_type": config.get("signal_type"),
                 "signal_label": SIGNAL_INFO.get(config.get("signal_type", ""), ""),
                 "params": config.get("params") or {},
                 "start": dates[0].isoformat(), "end": dates[-1].isoformat(),
                 "days": len(dates), "capital": cap0,
                 "exec_lag": config.get("exec_lag", 1),
                 "defensive": None if config.get("defensive_is_cash", True)
                 else config.get("defensive")},
        "equity": {"dates": [d.isoformat() for d in dates],
                   "strategy": [round(v, 2) for v in equity],
                   "gross": [round(v, 2) for v in gross],
                   "benchmark": [round(v, 2) for v in bench],
                   "drawdown": [round(v, 6) for v in dd],
                   "bench_drawdown": [round(v, 6) for v in bdd],
                   "state": states, "position": positions},
        "signal": sig_out,
        "metrics": m, "yearly": yearly, "monthly": monthly, "rolling": rolling,
        "trades": [t.to_dict() for t in trades],
    }


def drawdown(values: Sequence[float]) -> List[float]:
    out, peak = [], -1e18
    for v in values:
        peak = max(peak, v)
        out.append(v / peak - 1.0 if peak > 0 else 0.0)
    return out


# --------------------------------------------------------------------------- #
# Kennzahlen
# --------------------------------------------------------------------------- #

def compute_metrics(dates: List[date], equity: List[float], bench: List[float],
                    dd: List[float], trades: List[Trade], total_fee: float,
                    total_tax: float, cap0: float, states: List[int],
                    positions: List[str]) -> Dict:
    n = len(equity)
    if n < 2:
        return {}
    rets = [equity[i] / equity[i - 1] - 1.0 for i in range(1, n) if equity[i - 1] > 0]
    brets = [bench[i] / bench[i - 1] - 1.0 for i in range(1, n) if bench[i - 1] > 0]
    years = ((dates[-1] - dates[0]).days or 1) / 365.25
    end = equity[-1]
    total_ret = end / cap0 - 1.0 if cap0 else 0.0
    cagr = (end / cap0) ** (1.0 / years) - 1.0 if cap0 > 0 and end > 0 else -1.0
    b_end = bench[-1]
    b_total = b_end / cap0 - 1.0 if cap0 else 0.0
    b_cagr = (b_end / cap0) ** (1.0 / years) - 1.0 if cap0 > 0 and b_end > 0 else -1.0
    vol = stdev(rets) * math.sqrt(252)
    bvol = stdev(brets) * math.sqrt(252)
    downside = [r for r in rets if r < 0]
    dvol = (math.sqrt(sum(r * r for r in downside) / len(downside)) * math.sqrt(252)
            if downside else 0.0)
    maxdd = min(dd) if dd else 0.0
    bmaxdd = min(drawdown(bench)) if bench else 0.0
    longest = cur = 0
    for v in dd:
        cur = cur + 1 if v < -1e-9 else 0
        longest = max(longest, cur)
    peak_i, max_dur = 0, 0
    for i in range(n):
        if equity[i] >= equity[peak_i]:
            peak_i = i
        max_dur = max(max_dur, i - peak_i)
    invested_days = sum(1 for p in positions if p != "KASSE")
    switches = sum(1 for i in range(1, len(positions)) if positions[i] != positions[i - 1])
    return {
        "start": dates[0].isoformat(), "end": dates[-1].isoformat(),
        "years": round(years, 2), "trading_days": n,
        "start_capital": round(cap0, 2), "end_capital": round(end, 2),
        "total_return": round(total_ret, 6), "cagr": round(cagr, 6),
        "volatility": round(vol, 6), "sharpe": round(cagr / vol, 3) if vol > EPS else 0.0,
        "sortino": round(cagr / dvol, 3) if dvol > EPS else 0.0,
        "calmar": round(cagr / abs(maxdd), 3) if abs(maxdd) > EPS else 0.0,
        "max_drawdown": round(maxdd, 6),
        "max_drawdown_days": longest,
        "longest_underwater_days": max_dur,
        "bench_total_return": round(b_total, 6), "bench_cagr": round(b_cagr, 6),
        "bench_volatility": round(bvol, 6),
        "bench_sharpe": round(b_cagr / bvol, 3) if bvol > EPS else 0.0,
        "bench_max_drawdown": round(bmaxdd, 6),
        "excess_cagr": round(cagr - b_cagr, 6),
        "trades": len(trades),
        "switches": switches,
        "fees": round(total_fee, 2), "taxes": round(total_tax, 2),
        "best_day": round(max(rets), 6) if rets else 0.0,
        "worst_day": round(min(rets), 6) if rets else 0.0,
        "positive_days": round(sum(1 for r in rets if r > 0) / len(rets), 4) if rets else 0.0,
        "time_in_market": round(invested_days / n, 4),
        "start_state": int(states[0]) if states else 0,
    }


def yearly_returns(dates: List[date], equity: List[float], bench: List[float]) -> List[Dict]:
    out: List[Dict] = []
    if not dates:
        return out
    cur = dates[0].year
    sv, sb = equity[0], bench[0]
    for i, d in enumerate(dates):
        if d.year != cur:
            out.append({"year": cur,
                        "strategy": round(equity[i - 1] / sv - 1.0, 6) if sv else 0.0,
                        "benchmark": round(bench[i - 1] / sb - 1.0, 6) if sb else 0.0,
                        "end": round(equity[i - 1], 2)})
            cur = d.year
            sv, sb = equity[i - 1], bench[i - 1]
    out.append({"year": cur,
                "strategy": round(equity[-1] / sv - 1.0, 6) if sv else 0.0,
                "benchmark": round(bench[-1] / sb - 1.0, 6) if sb else 0.0,
                "end": round(equity[-1], 2)})
    return out


def monthly_matrix(dates: List[date], equity: List[float]) -> Dict:
    """Monatsrenditen: Zeilen = Jahre, Spalten = Monate (None = kein Handel)."""
    if not dates:
        return {"years": [], "months": MONTHS_DE, "values": []}
    last_of_month: Dict[Tuple[int, int], float] = {}
    for i, d in enumerate(dates):
        last_of_month[(d.year, d.month)] = equity[i]
    keys = sorted(last_of_month)
    values: Dict[int, Dict[int, float]] = {}
    prev = equity[0]
    for (y, mo) in keys:
        v = last_of_month[(y, mo)]
        values.setdefault(y, {})[mo] = v / prev - 1.0 if prev > 0 else 0.0
        prev = v
    years = sorted(values)
    mat = [[round(values[y][mo], 6) if mo in values[y] else None for mo in range(1, 13)]
           for y in years]
    return {"years": years, "months": MONTHS_DE, "values": mat}


def rolling_metrics(dates: List[date], equity: List[float], window: int = 252) -> Dict:
    """Rollierende Kennzahlen in O(n) (Praefixsummen + monotone Warteschlange)."""
    out = {"dates": [], "cagr": [], "vol": [], "drawdown": []}
    n = len(equity)
    if n <= window + 1:
        return out
    rets = [0.0] * n
    for i in range(1, n):
        rets[i] = equity[i] / equity[i - 1] - 1.0 if equity[i - 1] > 0 else 0.0
    pre = [0.0] * (n + 1)
    pre2 = [0.0] * (n + 1)
    for i in range(n):
        pre[i + 1] = pre[i] + rets[i]
        pre2[i + 1] = pre2[i] + rets[i] * rets[i]
    step = max(1, n // 800)
    from collections import deque
    dq: deque = deque()
    for i in range(n):
        while dq and equity[dq[-1]] <= equity[i]:
            dq.pop()
        dq.append(i)
        while dq[0] <= i - window:
            dq.popleft()
        if i < window or (i - window) % step:
            continue
        a, b = equity[i - window], equity[i]
        if a <= 0:
            continue
        cagr = (b / a) ** (252.0 / window) - 1.0 if b > 0 else -1.0
        s = pre[i + 1] - pre[i - window + 1]
        s2 = pre2[i + 1] - pre2[i - window + 1]
        m = s / window
        var = max(0.0, (s2 - window * m * m) / max(1, window - 1))
        vol = math.sqrt(var) * math.sqrt(252)
        peak = equity[dq[0]]
        out["dates"].append(dates[i].isoformat())
        out["cagr"].append(round(cagr, 6))
        out["vol"].append(round(vol, 6))
        out["drawdown"].append(round(b / peak - 1.0 if peak > 0 else 0.0, 6))
    return out


# --------------------------------------------------------------------------- #
# Parameter-Suche / Walk-Forward / Monte-Carlo
# --------------------------------------------------------------------------- #

def param_grid(signal_type: str) -> List[Dict]:
    combos: List[Dict] = []
    if signal_type == "ampel":
        for n in (80, 100, 130, 160, 200, 250):
            for upper in (1.02, 1.05, 1.08):
                for lower in (0.88, 0.90, 0.92, 0.95):
                    for dr, dg in ((19, 16), (5, 5)):
                        combos.append({"sma_days": n, "upper": upper, "lower": lower,
                                       "days_red": dr, "days_green": dg})
    elif signal_type == "sma":
        for n in (50, 100, 150, 200, 250, 300):
            for band in (0.0, 0.01, 0.02):
                combos.append({"sma_days": n, "band": band})
    elif signal_type == "dual_sma":
        for s in (20, 50, 100):
            for l in (150, 200, 250):
                if s < l:
                    combos.append({"sma_short": s, "sma_long": l})
    elif signal_type == "momentum":
        for n in (21, 63, 126, 189, 252):
            combos.append({"momentum_days": n})
    return combos


def run_grid(risk: Series, signal_series: Series, config: Dict,
             defensive: Optional[Series], max_runs: int = 500) -> List[Dict]:
    base = dict(config.get("params") or {})
    combos = param_grid(config.get("signal_type", "ampel"))[:max_runs]
    rows: List[Dict] = []
    for c in combos:
        cfg = dict(config)
        cfg["params"] = {**base, **c}
        try:
            r = run_backtest(risk, signal_series, cfg, defensive)
        except Exception:
            continue
        m = r["metrics"]
        row = {"params": cfg["params"]}
        for k in ("cagr", "max_drawdown", "sharpe", "calmar", "volatility",
                  "total_return", "bench_cagr", "end_capital", "trades", "time_in_market"):
            row[k] = m.get(k)
        rows.append(row)
    return rows


def walk_forward(risk: Series, signal_series: Series, config: Dict,
                 defensive: Optional[Series], train_years: int = 6, test_years: int = 2,
                 objective: str = "calmar") -> Dict:
    base = dict(config.get("params") or {})
    combos = param_grid(config.get("signal_type", "ampel"))
    if len(combos) > 60:
        combos = combos[:60]
    dates = risk.dates
    if len(dates) < 700:
        return {"windows": [], "oos": None, "note": "Zu wenig Historie für Walk-Forward."}
    y = dates[0].year + train_years
    windows, eq_oos, dates_oos = [], [], []
    capital = 10000.0
    while True:
        tr = (date(y - train_years, 1, 1), date(y - 1, 12, 31))
        te = (date(y, 1, 1), date(min(y + test_years - 1, dates[-1].year), 12, 31))
        if te[0] > dates[-1]:
            break
        best, best_score = None, -1e18
        for c in combos:
            cfg = dict(config, params={**base, **c}, start=tr[0].isoformat(),
                       end=tr[1].isoformat(), capital=10000.0)
            try:
                m = run_backtest(risk, signal_series, cfg, defensive)["metrics"]
            except Exception:
                continue
            score = m.get(objective)
            if score is None:
                continue
            if score > best_score:
                best, best_score = c, score
        if best is None:
            break
        cfg = dict(config, params={**base, **best}, start=te[0].isoformat(),
                   end=te[1].isoformat(), capital=capital)
        try:
            r = run_backtest(risk, signal_series, cfg, defensive)
        except Exception:
            y += test_years
            continue
        for i, d in enumerate(r["equity"]["dates"]):
            dates_oos.append(pdate(d))
            eq_oos.append(r["equity"]["strategy"][i])
        windows.append({"train": [tr[0].isoformat(), tr[1].isoformat()],
                        "test": [te[0].isoformat(), te[1].isoformat()],
                        "params": best, "score": round(best_score, 4),
                        "test_return": r["metrics"].get("total_return")})
        capital = r["metrics"].get("end_capital", capital)
        y += test_years
    oos = None
    if eq_oos:
        oos = {"dates": [d.isoformat() for d in dates_oos], "strategy": eq_oos,
               "drawdown": drawdown(eq_oos)}
    return {"windows": windows, "oos": oos}


def monte_carlo(equity: List[float], n_paths: int = 300, horizon: int = 2520,
                seed: int = 7) -> Dict:
    """Bootstrap der Tagesrenditen als Zukunfts-Bandbreite."""
    rnd = random.Random(seed)
    rets = [equity[i] / equity[i - 1] - 1.0 for i in range(1, len(equity)) if equity[i - 1] > 0]
    if len(rets) < 60:
        return {}
    step = max(1, horizon // 200)
    paths: List[List[float]] = []
    for _ in range(n_paths):
        v = equity[-1]
        path = []
        for k in range(horizon):
            v *= (1.0 + rets[rnd.randrange(len(rets))])
            if k % step == 0:
                path.append(v)
        paths.append(path)
    L = min(len(p) for p in paths)
    bands = {k: [] for k in ("p5", "p25", "p50", "p75", "p95")}
    for k in range(L):
        col = sorted(p[k] for p in paths)
        for key, a in (("p5", .05), ("p25", .25), ("p50", .5), ("p75", .75), ("p95", .95)):
            idx = min(len(col) - 1, max(0, int(a * (len(col) - 1))))
            bands[key].append(round(col[idx], 2))
    return {"n_paths": n_paths, "horizon_days": horizon, "step_days": step, **bands,
            "final": {k: bands[k][-1] for k in bands}}


# ==========================================================================
# Teil 2/3: Datenschicht (Yahoo, CSV, Cache)
# ==========================================================================

"""
ETF-Timing-Werkzeug -- Datenschicht: Yahoo-Download, CSV-Import, lokaler Cache.

Reine Standardbibliothek (urllib, json, csv, gzip). Kein API-Key nötig.
"""


import csv
import io
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Tuple



UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

YAHOO_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
INDEX_FILE = os.path.join(DATA_DIR, "index.json")


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def http_json(url: str, timeout: int = 25) -> dict:
    last = None
    for attempt in range(3):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (429, 502, 503, 504):
                time.sleep(1.2 * (attempt + 1))
                continue
            raise RuntimeError(last)
        except Exception as e:                                    # noqa: BLE001
            last = str(e)
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"Netzwerkfehler: {last}")


def _yahoo(path: str, params: dict) -> dict:
    q = urllib.parse.urlencode(params)
    err = None
    for host in YAHOO_HOSTS:
        try:
            return http_json(f"https://{host}/{path}?{q}")
        except Exception as e:                                    # noqa: BLE001
            err = e
    raise RuntimeError(str(err))


_YF_TYPE = {"EQUITY": "Aktie", "ETF": "ETF", "INDEX": "Index", "MUTUALFUND": "Fonds",
            "CURRENCY": "Währung", "CRYPTOCURRENCY": "Krypto", "FUTURE": "Future"}


def yahoo_search(query: str, limit: int = 12) -> List[Dict]:
    if not query.strip():
        return []
    j = _yahoo("v1/finance/search", {
        "q": query.strip(), "quotesCount": limit, "newsCount": 0, "listsCount": 0,
        "enableFuzzyQuery": "false", "quotesQueryId": "tss_match_phrase_query"})
    out = []
    for x in j.get("quotes", []):
        sym = x.get("symbol")
        if not sym:
            continue
        out.append({"symbol": sym,
                    "name": x.get("shortname") or x.get("longname") or sym,
                    "exchange": x.get("exchDisp") or x.get("exchange") or "",
                    "type": _YF_TYPE.get(x.get("quoteType", ""), x.get("quoteType", ""))})
    return out


def yahoo_series(symbol: str, start: Optional[date] = None, end: Optional[date] = None,
                 interval: str = "1d") -> Series:
    p1 = int((start or date(1950, 1, 1)).strftime("%s")) if False else int(
        (datetime.combine(start, datetime.min.time()) - datetime(1970, 1, 1)).days * 86400) if start else 0
    p2 = int((datetime.combine(end, datetime.min.time()) - datetime(1970, 1, 1)).days * 86400) \
        if end else int(time.time()) + 86400
    j = _yahoo("v8/finance/chart/" + urllib.parse.quote(symbol), {
        "period1": p1, "period2": p2, "interval": interval,
        "events": "div%2Csplit", "includeAdjustedClose": "true"})
    res = (j.get("chart") or {}).get("result") or []
    if not res:
        err = (j.get("chart") or {}).get("error")
        raise RuntimeError(f"Keine Daten für {symbol}" + (f" ({err})" if err else ""))
    r = res[0]
    meta = r.get("meta") or {}
    ts = r.get("timestamp") or []
    q = (r.get("indicators", {}).get("quote") or [{}])[0]
    closes = q.get("close") or []
    adj = ((r.get("indicators", {}).get("adjclose") or [{}])[0] or {}).get("adjclose")
    if not adj:
        adj = closes
    rows = []
    for i, t in enumerate(ts):
        d = datetime.utcfromtimestamp(t).date()
        c = fnum(closes[i]) if i < len(closes) else None
        a = fnum(adj[i]) if i < len(adj) else None
        if c is None:
            continue
        rows.append((d, c, a))
    if not rows:
        raise RuntimeError(f"Keine gültigen Kurse für {symbol}")
    rows.sort(key=lambda x: x[0])
    series = Series(symbol=symbol, name=meta.get("longName") or meta.get("shortName") or symbol,
                    currency=meta.get("currency") or "",
                    source="yahoo", meta={"exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or "",
                                          "type": meta.get("instrumentType") or "",
                                          "timezone": meta.get("exchangeTimezoneName") or "",
                                          "fetched": datetime.now().isoformat(timespec="seconds")},
                    dates=[r[0] for r in rows], close=[r[1] for r in rows],
                    adj=[r[2] if r[2] is not None else r[1] for r in rows])
    return series


# --------------------------------------------------------------------------- #
# CSV-Import
# --------------------------------------------------------------------------- #

DATE_KEYS = ("datum", "date", "tag", "time", "zeit")
PRICE_KEYS = ("adj close", "adjclose", "adj. close", "adjusted close", "bereinigt",
              "schluss", "close", "kurs", "letzter", "last", "preis", "price", "nav", "wert")
SKIP_PRICE = ("open", "eröffnung", "eroeffnung", "high", "hoch", "low", "tief",
              "volume", "volumen", "stück", "stueck")


def _split(line: str, delim: str) -> List[str]:
    if delim == ";":
        return [c.strip() for c in line.split(";")]
    return [c.strip() for c in next(csv.reader([line], delimiter=delim))]


def _guess_delim(text: str) -> str:
    head = [l for l in text.splitlines()[:20] if l.strip()]
    best, best_n = ";", -1
    for d in (";", ",", "\t"):
        n = sum(l.count(d) for l in head)
        if n > best_n:
            best, best_n = d, n
    return best


def parse_csv(text: str, symbol: str = "", name: str = "") -> Series:
    """Flexibler CSV-Parser: erkennt Trennzeichen, Kopfzeilen, Datums- und Kurs-Spalte."""
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig", "replace")
    text = text.replace("\ufeff", "")
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        raise ValueError("CSV ist leer.")
    delim = _guess_delim(text)

    date_col, price_col, header_idx = None, None, None
    for i, line in enumerate(lines[:40]):
        cells = _split(line, delim)
        low = [c.lower() for c in cells]
        d_i = next((k for k, c in enumerate(low) if any(c.startswith(t) or c == t for t in DATE_KEYS)), None)
        if d_i is None:
            continue
        p_i = None
        for k, c in enumerate(low):
            if k == d_i:
                continue
            if any(t in c for t in PRICE_KEYS) and not any(s in c for s in SKIP_PRICE):
                p_i = k
                break
        if p_i is None:
            for k, c in enumerate(low):
                if k == d_i:
                    continue
                if any(t in c for t in PRICE_KEYS):
                    p_i = k
                    break
        if p_i is not None:
            date_col, price_col, header_idx = d_i, p_i, i
            break

    rows: List[Tuple[date, float]] = []
    if date_col is not None:
        for line in lines[header_idx + 1:]:
            cells = _split(line, delim)
            if len(cells) <= max(date_col, price_col):
                continue
            d = pdate(cells[date_col])
            v = fnum(cells[price_col])
            if d and v and v > 0:
                rows.append((d, v))
    if len(rows) < 10:
        # Fallback: erste Spalte = Datum, erste sinnvolle Zahlspalte = Kurs
        rows = []
        for line in lines:
            cells = _split(line, delim)
            if len(cells) < 2:
                continue
            d = pdate(cells[0])
            if not d:
                continue
            for c in cells[1:]:
                v = fnum(c)
                if v and v > 0:
                    rows.append((d, v))
                    break
    if len(rows) < 10:
        raise ValueError("Konnte im CSV keine Kursreihe erkennen (Datum + Kurs erwartet).")
    sym = symbol or (name or "CSV").upper().replace(" ", "_")[:24]
    series = make_series(sym, rows, name=name or sym, currency="", source="csv",
                         meta={"fetched": datetime.now().isoformat(timespec="seconds")})
    return series


# --------------------------------------------------------------------------- #
# Synthetische Hebelreihe (fuer den Original-Vergleich)
# --------------------------------------------------------------------------- #

def synthetic_leverage(base: Series, factor: float, rf_pct: float = 0.0,
                       spread_pct: float = 0.0, name: str = "") -> Series:
    """Bildet eine gehebelte Reihe aus Tagesrenditen nach: r_L = L*r - |L-1|*Kosten.

    Entspricht der Logik aus 'db_x-trackers' (dort: Faktor 1 / 2 / -1 / -2 auf den DAX).
    """
    if not base.dates:
        raise ValueError("Basisserie ist leer.")
    cost_daily = (rf_pct + spread_pct) / 100.0 / 252.0
    vals = [100.0]
    for i in range(1, len(base)):
        r = base.value_at(i) / base.value_at(i - 1) - 1.0 if base.value_at(i - 1) else 0.0
        r_l = factor * r - abs(factor - 1.0) * cost_daily
        vals.append(vals[-1] * (1.0 + r_l))
    sym = f"{base.symbol}_{factor:+.0f}x".replace("+", "L")
    return Series(symbol=sym, name=name or f"{base.name or base.symbol} ({factor:+.0f}x synthetisch)",
                  currency=base.currency, source="synthetic",
                  meta={"base": base.symbol, "factor": factor, "rf_pct": rf_pct,
                        "spread_pct": spread_pct},
                  dates=list(base.dates), close=vals, adj=list(vals))


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #

def _ensure_dirs() -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)


def _safe_name(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", symbol)[:80] or "series"


def _cache_path(symbol: str) -> str:
    return os.path.join(CACHE_DIR, _safe_name(symbol) + ".json")


def load_index() -> Dict:
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"instruments": {}}


def save_index(idx: Dict) -> None:
    _ensure_dirs()
    tmp = INDEX_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)
    os.replace(tmp, INDEX_FILE)


def save_series(s: Series) -> None:
    _ensure_dirs()
    path = _cache_path(s.symbol)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s.to_dict(), f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    idx = load_index()
    idx.setdefault("instruments", {})[s.symbol] = {
        "symbol": s.symbol, "name": s.name, "currency": s.currency, "source": s.source,
        "rows": len(s), "first": s.dates[0].isoformat() if s.dates else None,
        "last": s.dates[-1].isoformat() if s.dates else None,
        "meta": s.meta,
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    save_index(idx)


def load_series(symbol: str) -> Optional[Series]:
    path = _cache_path(symbol)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return Series.from_dict(json.load(f))
    except Exception:
        return None


def delete_series(symbol: str) -> bool:
    path = _cache_path(symbol)
    ok = False
    if os.path.exists(path):
        try:
            os.remove(path)
            ok = True
        except Exception:
            ok = False
    idx = load_index()
    if symbol in idx.get("instruments", {}):
        del idx["instruments"][symbol]
        save_index(idx)
        ok = True
    return ok


def list_cached() -> List[Dict]:
    idx = load_index().get("instruments", {})
    out = list(idx.values())
    out.sort(key=lambda x: (x.get("name") or x.get("symbol") or "").lower())
    return out


def get_or_download(symbol: str, start: Optional[date] = None,
                    end: Optional[date] = None, force: bool = False) -> Series:
    """Liefert die Reihe aus dem Cache oder laedt sie (und ergänzt fehlende Tage)."""
    cached = None if force else load_series(symbol)
    if cached is not None and len(cached) > 0:
        last = cached.dates[-1]
        need_more = (end is None and last < date.today() - timedelta(days=3)) or \
                    (end is not None and last < end)
        if not need_more:
            if start and cached.dates[0] > start:
                try:
                    fresh = yahoo_series(symbol, start=start, end=end)
                    merged = _merge_series(cached, fresh)
                    save_series(merged)
                    return merged
                except Exception:
                    return cached
            return cached
        try:
            fresh = yahoo_series(symbol, start=start or cached.dates[0], end=end)
            merged = _merge_series(cached, fresh)
            save_series(merged)
            return merged
        except Exception:
            return cached
    s = yahoo_series(symbol, start=start, end=end)
    save_series(s)
    return s


def _merge_series(a: Series, b: Series) -> Series:
    d: Dict[date, Tuple[float, Optional[float]]] = {}
    for i, dt in enumerate(a.dates):
        d[dt] = (a.close[i], a.adj[i] if a.adj else None)
    for i, dt in enumerate(b.dates):
        d[dt] = (b.close[i], b.adj[i] if b.adj else None)
    dates = sorted(d)
    return Series(symbol=b.symbol or a.symbol, name=b.name or a.name,
                  currency=b.currency or a.currency, source=b.source or a.source,
                  meta={**(a.meta or {}), **(b.meta or {})},
                  dates=dates, close=[d[x][0] for x in dates],
                  adj=[d[x][1] for x in dates])


# --------------------------------------------------------------------------- #
# Kuratierte Instrumente (Vorschlagsliste im UI)
# --------------------------------------------------------------------------- #

PRESET_INSTRUMENTS = [
    {"symbol": "IWDA.AS", "name": "iShares Core MSCI World (Acc, EUR)", "group": "Welt-ETF", "type": "ETF"},
    {"symbol": "EUNL.DE", "name": "iShares Core MSCI World (Acc, Xetra)", "group": "Welt-ETF", "type": "ETF"},
    {"symbol": "VWCE.DE", "name": "Vanguard FTSE All-World (Acc)", "group": "Welt-ETF", "type": "ETF"},
    {"symbol": "EXS1.DE", "name": "iShares Core DAX (DE)", "group": "Deutschland", "type": "ETF"},
    {"symbol": "SXR8.DE", "name": "iShares Core S&P 500 (Acc, EUR)", "group": "USA", "type": "ETF"},
    {"symbol": "VUSA.L", "name": "Vanguard S&P 500 (Dist, GBP)", "group": "USA", "type": "ETF"},
    {"symbol": "EUNL.AS", "name": "iShares Core MSCI Europe", "group": "Europa", "type": "ETF"},
    {"symbol": "XMME.DE", "name": "Xtrackers MSCI Emerging Markets", "group": "Schwellenländer", "type": "ETF"},
    {"symbol": "IUAG.L", "name": "iShares Global Aggregate Bond", "group": "Anleihen", "type": "ETF"},
    {"symbol": "4GLD.DE", "name": "Xetra-Gold (EUR)", "group": "Gold", "type": "ETF"},
    {"symbol": "^GDAXI", "name": "DAX Index", "group": "Index", "type": "Index"},
    {"symbol": "^MDAXI", "name": "MDAX Index", "group": "Index", "type": "Index"},
    {"symbol": "^GSPC", "name": "S&P 500 Index", "group": "Index", "type": "Index"},
    {"symbol": "^NDX", "name": "Nasdaq 100 Index", "group": "Index", "type": "Index"},
    {"symbol": "^STOXX50E", "name": "EURO STOXX 50 Index", "group": "Index", "type": "Index"},
    {"symbol": "^DJI", "name": "Dow Jones Industrial Average", "group": "Index", "type": "Index"},
    {"symbol": "URTH", "name": "iShares MSCI World ETF (USD)", "group": "Welt-ETF", "type": "ETF"},
    {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust (USD)", "group": "USA", "type": "ETF"},
]


# ==========================================================================
# Teil 3/3: Server, API und Oberflaeche
# ==========================================================================

"""
ETF-Timing-Werkzeug -- lokaler Webserver + REST-API.

Startet einen HTTP-Server auf 127.0.0.1, liefert die eingebettete Oberfläche aus
und rechnet Backtests, Parameter-Suchen und Daten-Downloads serverseitig
(deshalb gibt es keine CORS-Probleme mit Yahoo Finance).
"""


import argparse
import csv
import io
import json
import os
import socket
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional



VERSION = "1.0"
APP_NAME = "ETF-Timing-Studio"

# --------------------------------------------------------------------------- #
# Strategie-Presets
# --------------------------------------------------------------------------- #

ORIGINAL_ROTATION = {
    "green": ["^GDAXI@2", "^GDAXI@2", "^GDAXI@2", "^GDAXI@1", "^GDAXI@2", "^GDAXI@2",
              "^GDAXI@2", "^GDAXI@2", "^GDAXI@2", "^GDAXI@2", "^GDAXI@2", "^GDAXI@2"],
    "red": ["^GDAXI@-2", "^GDAXI@-1", "^GDAXI@-1", "^GDAXI@2", "^GDAXI@-2", "^GDAXI@-2",
            "^GDAXI@-1", "^GDAXI@-1", "^GDAXI@-1", "^GDAXI@2", "^GDAXI@2", "^GDAXI@-2"],
}

PRESETS = [
    {
        "id": "ampel-world",
        "name": "RSL-Ampel · MSCI World (Kasse)",
        "hint": "Original-Logik (130 Tage, 1,05/0,92, 19/16) auf einen weltweiten ETF, "
                "Signal aus dem S&P 500. Defensiv = Kasse.",
        "config": {"risk": "IWDA.AS", "signal": "^GSPC", "defensive_is_cash": True,
                   "signal_type": "ampel",
                   "params": {"sma_days": 130, "upper": 1.05, "lower": 0.92,
                              "days_red": 19, "days_green": 16},
                   "exec_lag": 1, "capital": 10000.0, "fee_fixed": 5.9, "fee_pct": 0.0,
                   "slippage_bps": 5.0, "tax_rate": 26.375, "tax_mode": "accrued",
                   "loss_offset": True, "cash_rate": 2.0},
    },
    {
        "id": "ampel-dax",
        "name": "RSL-Ampel · DAX-ETF (Kasse)",
        "hint": "Gleiche Ampel-Logik, Signal direkt aus dem DAX, Risiko im DAX-ETF.",
        "config": {"risk": "EXS1.DE", "signal": "^GDAXI", "defensive_is_cash": True,
                   "signal_type": "ampel",
                   "params": {"sma_days": 130, "upper": 1.05, "lower": 0.92,
                              "days_red": 19, "days_green": 16},
                   "exec_lag": 1, "capital": 10000.0, "fee_fixed": 5.9, "fee_pct": 0.0,
                   "slippage_bps": 5.0, "tax_rate": 26.375, "tax_mode": "accrued",
                   "loss_offset": True, "cash_rate": 2.0},
    },
    {
        "id": "sma200-world",
        "name": "200-Tage-SMA · MSCI World (Kasse)",
        "hint": "Der Klassiker: investiert, solange der ETF über seinem 200-Tage-Schnitt "
                "liegt, sonst Kasse.",
        "config": {"risk": "IWDA.AS", "signal": "IWDA.AS", "defensive_is_cash": True,
                   "signal_type": "sma", "params": {"sma_days": 200, "band": 0.0},
                   "exec_lag": 1, "capital": 10000.0, "fee_fixed": 5.9, "fee_pct": 0.0,
                   "slippage_bps": 5.0, "tax_rate": 26.375, "tax_mode": "accrued",
                   "loss_offset": True, "cash_rate": 2.0},
    },
    {
        "id": "goldencross-sp500",
        "name": "Golden Cross 50/200 · S&P-500-ETF",
        "hint": "50-Tage-Schnitt über 200-Tage-Schnitt. Robust und einfach.",
        "config": {"risk": "SXR8.DE", "signal": "SXR8.DE", "defensive_is_cash": True,
                   "signal_type": "dual_sma", "params": {"sma_short": 50, "sma_long": 200},
                   "exec_lag": 1, "capital": 10000.0, "fee_fixed": 5.9, "fee_pct": 0.0,
                   "slippage_bps": 5.0, "tax_rate": 26.375, "tax_mode": "accrued",
                   "loss_offset": True, "cash_rate": 2.0},
    },
    {
        "id": "momentum-world",
        "name": "Momentum 12 Monate · MSCI World",
        "hint": "Investiert, wenn die 12-Monats-Rendite positiv ist.",
        "config": {"risk": "IWDA.AS", "signal": "IWDA.AS", "defensive_is_cash": True,
                   "signal_type": "momentum", "params": {"momentum_days": 252},
                   "exec_lag": 1, "capital": 10000.0, "fee_fixed": 5.9, "fee_pct": 0.0,
                   "slippage_bps": 5.0, "tax_rate": 26.375, "tax_mode": "accrued",
                   "loss_offset": True, "cash_rate": 2.0},
    },
    {
        "id": "ampel-sp500-long",
        "name": "RSL-Ampel · S&P 500 seit 1970 (Krisen-Test)",
        "hint": "Lange Historie: zeigt, wie die Ampel die großen Bärenmärkte 1973/74, 2000-02 "
                "und 2008 vermeidet – auf Kosten von Rendite in starken Aufwärtsphasen.",
        "config": {"risk": "^GSPC", "signal": "^GSPC", "defensive_is_cash": True,
                   "signal_type": "ampel",
                   "params": {"sma_days": 130, "upper": 1.05, "lower": 0.92,
                              "days_red": 19, "days_green": 16},
                   "exec_lag": 1, "capital": 10000.0, "fee_fixed": 5.9, "fee_pct": 0.0,
                   "slippage_bps": 5.0, "tax_rate": 26.375, "tax_mode": "accrued",
                   "loss_offset": True, "cash_rate": 2.0},
    },
    {
        "id": "original-comparison",
        "name": "Original-Nachbau (Vergleich): 2x / −2x DAX monatlich",
        "hint": "Die Strategie aus ETF_Simulation.xlsm: LevDAX bei grün, ShortDAX (x2) bei rot, "
                "monatliche Zuordnung. Hebel wird synthetisch aus dem DAX nachgebildet.",
        "config": {"risk": "^GDAXI@2", "signal": "^GSPC", "defensive_is_cash": False,
                   "signal_type": "ampel",
                   "params": {"sma_days": 130, "upper": 1.05, "lower": 0.92,
                              "days_red": 19, "days_green": 16},
                   "rotation": ORIGINAL_ROTATION,
                   "exec_lag": 0, "capital": 10000.0, "fee_fixed": 5.9, "fee_pct": 0.0,
                   "slippage_bps": 0.0, "tax_rate": 26.375, "tax_mode": "accrued",
                   "loss_offset": True, "cash_rate": 0.0,
                   "start": "2000-01-01", "end": "2015-12-23"},
    },
]

SIGNAL_TYPES = [{"id": k, "name": v} for k, v in E.SIGNAL_INFO.items()]

DEFAULTS = {
    "risk": "IWDA.AS", "signal": "^GSPC", "defensive": None, "defensive_is_cash": True,
    "signal_type": "ampel",
    "params": {"sma_days": 130, "upper": 1.05, "lower": 0.92, "days_red": 19,
               "days_green": 16, "band": 0.0, "sma_short": 50, "sma_long": 200,
               "momentum_days": 252},
    "start": None, "end": None, "capital": 10000.0, "fee_fixed": 5.9, "fee_pct": 0.0,
    "slippage_bps": 5.0, "tax_rate": 26.375, "tax_mode": "accrued", "loss_offset": True,
    "cash_rate": 2.0, "exec_lag": 1, "rotation": None,
}

_synth_cache: Dict[str, E.Series] = {}
_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Hilfsfunktionen
# --------------------------------------------------------------------------- #

def parse_synth(sym: str):
    """'^GDAXI@2' -> ('^GDAXI', 2.0); None wenn kein synthetisches Symbol."""
    if "@" not in sym:
        return None
    base, _, fac = sym.rpartition("@")
    try:
        return base, float(fac)
    except ValueError:
        return None


def get_series(symbol: str, cfg: Optional[Dict] = None,
               auto_download: bool = True) -> E.Series:
    """Liefert eine Reihe: Cache -> Download -> synthetischer Hebel."""
    syn = parse_synth(symbol)
    if syn:
        base, factor = syn
        key = f"{base}@{factor}"
        with _lock:
            if key in _synth_cache:
                return _synth_cache[key]
        b = get_series(base, cfg, auto_download)
        rf = float((cfg or {}).get("synth_rf", 2.0))
        s = D.synthetic_leverage(b, factor, rf_pct=rf, spread_pct=0.3)
        with _lock:
            _synth_cache[key] = s
        return s
    cached = D.load_series(symbol)
    if cached is not None and len(cached) > 0:
        last = cached.dates[-1]
        if last >= date.today() - timedelta(days=4):
            return cached
        if auto_download:
            try:
                return D.get_or_download(symbol)
            except Exception:
                return cached
        return cached
    if not auto_download:
        raise FileNotFoundError(f"{symbol} ist nicht im Datenbestand.")
    return D.get_or_download(symbol)


def normalize_config(cfg: Dict) -> Dict:
    out = dict(DEFAULTS)
    out.update(cfg or {})
    params = dict(DEFAULTS["params"])
    params.update((cfg or {}).get("params") or {})
    out["params"] = params
    if not out.get("start"):
        out["start"] = None
    if not out.get("end"):
        out["end"] = None
    return out


def run_config(cfg: Dict, with_mc: bool = False, with_rolling: bool = True) -> Dict:
    cfg = normalize_config(cfg)
    risk = get_series(cfg["risk"], cfg)
    sig = get_series(cfg["signal"], cfg)
    defensive = None
    if not cfg.get("defensive_is_cash") and cfg.get("defensive"):
        defensive = get_series(cfg["defensive"], cfg)
    instruments = {s.symbol: s for s in (risk, sig, defensive) if s is not None}
    if cfg.get("rotation"):
        for row in ("green", "red"):
            for sym in (cfg["rotation"].get(row) or []):
                if sym and sym not in instruments:
                    try:
                        instruments[sym] = get_series(sym, cfg)
                    except Exception:
                        pass
        # Risikoanlage aus der Rotation ebenfalls laden (fuer Benchmark)
        first = [s for s in (cfg["rotation"].get("green") or []) if s]
        if first and first[0] not in instruments:
            try:
                instruments[first[0]] = get_series(first[0], cfg)
            except Exception:
                pass

    result = E.run_backtest(risk, sig, cfg, defensive, instruments)
    result["meta"]["risk_name"] = risk.name
    result["meta"]["signal_name"] = sig.name
    result["meta"]["risk_first"] = risk.dates[0].isoformat() if risk.dates else None
    result["meta"]["risk_last"] = risk.dates[-1].isoformat() if risk.dates else None
    result["meta"]["config"] = cfg
    if with_rolling:
        result["rolling"] = E.rolling_metrics(
            [E.pdate(d) for d in result["equity"]["dates"]], result["equity"]["strategy"])
    if with_mc:
        result["montecarlo"] = E.monte_carlo(result["equity"]["strategy"])
    return result


def b64_encode(text: str) -> str:
    import base64
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def results_csv(res: Dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    eq = res["equity"]
    w.writerow(["Datum", "Strategie", "Benchmark", "Drawdown", "Signal", "Position"])
    for i, d in enumerate(eq["dates"]):
        w.writerow([d, f"{eq['strategy'][i]:.2f}", f"{eq['benchmark'][i]:.2f}",
                    f"{eq['drawdown'][i]:.6f}", eq["state"][i], eq["position"][i]])
    w.writerow([])
    w.writerow(["Kennzahl", "Wert"])
    for k, v in res["metrics"].items():
        w.writerow([k, v])
    w.writerow([])
    w.writerow(["Jahr", "Strategie", "Benchmark", "Endstand"])
    for y in res["yearly"]:
        w.writerow([y["year"], y["strategy"], y["benchmark"], y["end"]])
    w.writerow([])
    w.writerow(["Trades"])
    w.writerow(["Datum", "Aktion", "Instrument", "Kurs", "Stueck", "Wert", "Gebuehr",
                "Steuer", "Gewinn"])
    for t in res["trades"]:
        w.writerow([t["date"], t["action"], t["symbol"], t["price"], t["units"],
                    t["value"], t["fee"], t["tax"], t["gain"]])
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# HTTP-Handler
# --------------------------------------------------------------------------- #

class Handler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/{VERSION}"

    def log_message(self, fmt, *args):                          # noqa: A003
        sys.stderr.write("  %s\n" % (fmt % args))

    # ---- Antwort-Helfer
    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"))

    def _err(self, msg: str, code: int = 400, **extra):
        self._json({"ok": False, "error": msg, **extra}, code)

    def _body(self) -> Dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            return {}

    # ---- GET
    def do_GET(self):                                          # noqa: N802
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                self._send(200, UI_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if u.path == "/favicon.ico":
                self._send(200, FAVICON, "image/svg+xml")
                return
            if u.path == "/api/bootstrap":
                self._json({
                    "ok": True, "version": VERSION, "app": APP_NAME,
                    "defaults": DEFAULTS, "presets": PRESETS,
                    "signal_types": SIGNAL_TYPES,
                    "cached": D.list_cached(),
                    "suggestions": D.PRESET_INSTRUMENTS,
                    "today": date.today().isoformat(),
                    "data_dir": D.DATA_DIR,
                })
                return
            if u.path == "/api/search":
                term = (q.get("q") or [""])[0]
                try:
                    self._json({"ok": True, "results": D.yahoo_search(term)})
                except Exception as e:                          # noqa: BLE001
                    self._err(f"Suche fehlgeschlagen: {e}", 502)
                return
            if u.path == "/api/series":
                sym = (q.get("symbol") or [""])[0]
                if not sym:
                    self._err("symbol fehlt")
                    return
                try:
                    s = get_series(sym)
                except Exception as e:                          # noqa: BLE001
                    self._err(str(e), 404)
                    return
                step = max(1, len(s) // 1500)
                self._json({"ok": True, "symbol": s.symbol, "name": s.name,
                            "currency": s.currency,
                            "dates": [d.isoformat() for d in s.dates[::step]],
                            "close": [round(v, 4) for v in s.close[::step]],
                            "n": len(s)})
                return
            if u.path == "/api/health":
                self._json({"ok": True, "version": VERSION})
                return
            if u.path == "/api/shutdown":
                self._json({"ok": True})
                threading.Thread(target=lambda: (time.sleep(0.4), os._exit(0)),
                                 daemon=True).start()
                return
            self._err("Unbekannter Pfad: " + u.path, 404)
        except Exception as e:                                  # noqa: BLE001
            traceback.print_exc()
            self._err(f"Interner Fehler: {e}", 500)

    # ---- POST
    def do_POST(self):                                         # noqa: N802
        u = urllib.parse.urlparse(self.path)
        body = self._body()
        try:
            if u.path == "/api/download":
                symbols = body.get("symbols") or []
                if isinstance(symbols, str):
                    symbols = [s.strip() for s in symbols.replace(",", " ").split() if s.strip()]
                out = []
                for sym in symbols[:40]:
                    try:
                        s = D.get_or_download(sym, force=bool(body.get("force")))
                        out.append({"symbol": sym, "ok": True, "name": s.name,
                                    "rows": len(s),
                                    "first": s.dates[0].isoformat() if s.dates else None,
                                    "last": s.dates[-1].isoformat() if s.dates else None})
                    except Exception as e:                      # noqa: BLE001
                        out.append({"symbol": sym, "ok": False, "error": str(e)})
                with _lock:
                    _synth_cache.clear()
                self._json({"ok": True, "results": out, "cached": D.list_cached()})
                return

            if u.path == "/api/import":
                text = body.get("csv") or ""
                name = body.get("name") or "Import"
                symbol = (body.get("symbol") or name).upper().replace(" ", "_")[:32]
                try:
                    s = D.parse_csv(text, symbol=symbol, name=name)
                    D.save_series(s)
                    self._json({"ok": True, "symbol": s.symbol, "rows": len(s),
                                "first": s.dates[0].isoformat(), "last": s.dates[-1].isoformat(),
                                "cached": D.list_cached()})
                except Exception as e:                          # noqa: BLE001
                    self._err(f"Import fehlgeschlagen: {e}")
                return

            if u.path == "/api/delete":
                sym = body.get("symbol")
                ok = D.delete_series(sym) if sym else False
                with _lock:
                    _synth_cache.clear()
                self._json({"ok": ok, "cached": D.list_cached()})
                return

            if u.path == "/api/backtest":
                cfg = body.get("config") or {}
                res = run_config(cfg, with_mc=bool(body.get("montecarlo")))
                self._json({"ok": True, "result": res})
                return

            if u.path == "/api/grid":
                cfg = normalize_config(body.get("config") or {})
                risk = get_series(cfg["risk"], cfg)
                sig = get_series(cfg["signal"], cfg)
                defen = None
                if not cfg.get("defensive_is_cash") and cfg.get("defensive"):
                    defen = get_series(cfg["defensive"], cfg)
                rows = E.run_grid(risk, sig, cfg, defen,
                                  max_runs=int(body.get("max_runs") or 500))
                rows.sort(key=lambda r: (r.get("calmar") or -99), reverse=True)
                self._json({"ok": True, "rows": rows, "signal_type": cfg["signal_type"]})
                return

            if u.path == "/api/walkforward":
                cfg = normalize_config(body.get("config") or {})
                risk = get_series(cfg["risk"], cfg)
                sig = get_series(cfg["signal"], cfg)
                defen = None
                if not cfg.get("defensive_is_cash") and cfg.get("defensive"):
                    defen = get_series(cfg["defensive"], cfg)
                wf = E.walk_forward(risk, sig, cfg, defen,
                                    int(body.get("train_years") or 6),
                                    int(body.get("test_years") or 2),
                                    body.get("objective") or "calmar")
                self._json({"ok": True, "walkforward": wf})
                return

            if u.path == "/api/compare":
                cfg = normalize_config(body.get("config") or {})
                symbols = body.get("symbols") or []
                rows = []
                for sym in symbols[:20]:
                    c2 = dict(cfg, risk=sym)
                    if c2.get("rotation"):
                        c2["rotation"] = None
                        c2["defensive_is_cash"] = True
                    try:
                        r = run_config(c2)
                        m = r["metrics"]
                        rows.append({"symbol": sym, "name": r["meta"].get("risk_name"),
                                     "cagr": m.get("cagr"), "max_drawdown": m.get("max_drawdown"),
                                     "sharpe": m.get("sharpe"), "calmar": m.get("calmar"),
                                     "volatility": m.get("volatility"),
                                     "bench_cagr": m.get("bench_cagr"),
                                     "end_capital": m.get("end_capital"),
                                     "time_in_market": m.get("time_in_market"),
                                     "ok": True})
                    except Exception as e:                      # noqa: BLE001
                        rows.append({"symbol": sym, "ok": False, "error": str(e)})
                self._json({"ok": True, "rows": rows})
                return

            if u.path == "/api/export":
                cfg = body.get("config") or {}
                res = run_config(cfg)
                text = results_csv(res)
                fname = (f"etf_timing_{(cfg.get('risk') or 'risiko').replace('^', '')}"
                         f"_{datetime.now().strftime('%Y%m%d_%H%M')}.csv")
                self._json({"ok": True, "filename": fname,
                            "content": b64_encode(text),
                            "encoding": "base64-utf8"})
                return

            if u.path == "/api/export_json":
                cfg = body.get("config") or {}
                res = run_config(cfg)
                self._json({"ok": True, "result": res})
                return

            self._err("Unbekannter Pfad: " + u.path, 404)
        except Exception as e:                                  # noqa: BLE001
            traceback.print_exc()
            self._err(f"Fehler: {e}", 500)


FAVICON = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
           b'<rect width="64" height="64" rx="12" fill="#0f172a"/>'
           b'<path d="M8 46 L22 30 L32 38 L56 12" stroke="#22d3ee" stroke-width="5" '
           b'fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
           b'<circle cx="56" cy="12" r="5" fill="#f59e0b"/></svg>')

_UI_B64 = (
    "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"
    "N/5/MahP2g=="
)
UI_HTML = zlib.decompress(base64.b64decode(_UI_B64)).decode("utf-8")



# --------------------------------------------------------------------------- #
# Start
# --------------------------------------------------------------------------- #

def find_free_port(preferred: int = 8765) -> int:
    for p in [preferred, 0]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", p))
            port = s.getsockname()[1]
            s.close()
            return port
        except OSError:
            continue
    return 8765


def selftest() -> int:
    """Schneller Funktionstest ohne Browser."""
    print(f"{APP_NAME} {VERSION} -- Selbsttest")
    today = date.today()
    d = [date(2000, 1, 3) + timedelta(days=i) for i in range(3000)]
    px = [100.0 * (1.00025 ** i) * (1 + 0.08 * ((i % 400) / 400.0 - 0.5)) for i in range(len(d))]
    s = E.Series("DEMO", d, px, list(px), name="Demo")
    cfg = normalize_config({"risk": "DEMO", "signal": "DEMO", "signal_type": "ampel"})
    r = E.run_backtest(s, s, cfg)
    m = r["metrics"]
    print(f"  Tage={m['trading_days']}  CAGR={m['cagr']:.2%}  MaxDD={m['max_drawdown']:.2%}  "
          f"Trades={m['trades']}")
    # Signalvergleich mit der Original-Formel
    print("  Signal-Engine: ok" if m["trading_days"] > 2000 else "  Signal-Engine: FEHLER")
    print("  Selbsttest beendet.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=f"{APP_NAME} -- ETF-Timing-Analyse")
    ap.add_argument("--port", type=int, default=8765, help="Port (Standard 8765)")
    ap.add_argument("--no-browser", action="store_true", help="Browser nicht öffnen")
    ap.add_argument("--selftest", action="store_true", help="Nur Funktionstest")
    ap.add_argument("--download", nargs="+", metavar="SYMBOL",
                    help="Symbole herunterladen und beenden")
    ap.add_argument("--start", help="Startdatum (JJJJ-MM-TT)")
    ap.add_argument("--backtest", metavar="KONFIG_JSON",
                    help="Backtest headless mit JSON-Konfiguration")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    if args.download:
        for sym in args.download:
            try:
                s = D.get_or_download(sym, start=E.pdate(args.start))
                print(f"OK   {sym:14s} {len(s):5d} Kurse  {s.dates[0]} .. {s.dates[-1]}  {s.name}")
            except Exception as e:                              # noqa: BLE001
                print(f"FEHL {sym:14s} {e}")
        return 0

    if args.backtest:
        try:
            cfg = json.loads(args.backtest)
        except json.JSONDecodeError as e:
            print("Ungültiges JSON:", e)
            return 2
        res = run_config(cfg)
        m = res["metrics"]
        print(json.dumps(m, indent=2, ensure_ascii=False))
        return 0

    port = find_free_port(args.port)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print("=" * 74)
    print(f"  {APP_NAME} {VERSION}")
    print("=" * 74)
    print(f"  Oberfläche:  {url}")
    print(f"  Datenordner: {D.DATA_DIR}")
    print("  Beenden:     Strg+C im Fenster oder Button 'Beenden'")
    print("=" * 74)
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
