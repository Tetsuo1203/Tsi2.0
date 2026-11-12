#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETF-Timing-Studio 1.1
=====================

Ein eigenständiges Analyse-Werkzeug für Trendfolge-Strategien auf Aktien, ETFs und
Krypto (z. B. Bitcoin). Nachbau und Erweiterung der Arbeitsmappe "ETF_Simulation.xlsm"
(RSL-Ampel: Kurs / gleitender Durchschnitt mit Hysterese, monatliche Rotation,
Ordergebühren, Abgeltungsteuer) – mit realistischem Steuermodell (Teilfreistellung,
Sparerpauschbetrag, §23-Haltefrist), Risiko-/Benchmark-Kennzahlen, rollierenden
Renditen, Walk-Forward, Monte-Carlo und Datenqualitäts-Prüfung.

Start:      python ETF_Tool.py
Optional:   python ETF_Tool.py --port 9000 --no-browser
            python ETF_Tool.py --selftest
            python ETF_Tool.py --download IWDA.AS ^GDAXI BTC-EUR
            python ETF_Tool.py --quality SXR8.DE
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
  * weitere Signale: SMA-Trend, Golden Cross, Momentum
  * Umschaltung zwischen Risiko-Anlage und Defensiv-Anlage (Kasse/ETF)
  * Handels- und Steuerlogik: Gebuehren, Slippage, Abgeltungsteuer mit
    Teilfreistellung, Sparerpauschbetrag und Verlusttopf, private
    Veraeusserungsgeschaefte (§23 EStG, z. B. Bitcoin/Gold-ETC) mit Haltefrist
  * Kennzahlen (inkl. Benchmark-Bezug, VaR/CVaR, Ulcer), Jahres-/Monatsrenditen,
    rollierende Renditen, Trades, Drawdowns, Parameter-Suche, Walk-Forward,
    Monte-Carlo (Block-Bootstrap), Datenqualitaets-Pruefung

Kalender: Annualisierungen nutzen die tatsaechliche Zahl der Kurse je Jahr
(Boerse ~252, Krypto ~365). Kassenzinsen laufen kalendertaggenau.

Bewertung: Im Steuermodus "anteilig" ist der ausgewiesene Vermoegenswert -- wie
im Original-Sheet "Berechnungsblatt" Spalte I -- der Netto-Liquidationswert
(nach Ordergebuehr, Slippage und Steuer auf den noch nicht realisierten Gewinn).
Die Benchmark (Buy & Hold der Risiko-Anlage) wird in jedem Modus exakt gleich
behandelt, damit der Vergleich fair ist.
"""


import bisect
import math
import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

EPS = 1e-12
MONTHS_DE = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
             "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


# --------------------------------------------------------------------------- #
# Parsing-Helfer
# --------------------------------------------------------------------------- #

_FMT_ISO = ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y.%m.%d")
_FMT_DE = ("%d.%m.%Y", "%d.%m.%y")
_FMT_DAYFIRST = ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y")
_FMT_MONTHFIRST = ("%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%m-%d-%y")


def _excel_serial(v: float) -> Optional[date]:
    try:
        return (datetime(1899, 12, 30) + timedelta(days=float(v))).date()
    except Exception:                                           # noqa: BLE001
        return None


def pdate(s, dayfirst: Optional[bool] = None) -> Optional[date]:
    """Robuste Datumserkennung: ISO, deutsch, TT/MM bzw. MM/TT, Excel-Seriennummer.

    dayfirst: True = TT/MM/JJJJ, False = MM/TT/JJJJ (US), None = TT/MM bevorzugt.
    Der CSV-Import bestimmt die Reihenfolge je Datei (siehe data.parse_csv).
    """
    if s is None or s == "":
        return None
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    if isinstance(s, (int, float)):
        return _excel_serial(float(s))
    t = str(s).strip().strip('"').strip("'")
    if not t:
        return None
    t = t.split("T")[0].split(" ")[0]
    order = (_FMT_MONTHFIRST + _FMT_DAYFIRST) if dayfirst is False else (_FMT_DAYFIRST + _FMT_MONTHFIRST)
    for fmt in _FMT_ISO + _FMT_DE + order:
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    if re.fullmatch(r"\d{5}(\.\d+)?", t):                       # Excel-Serial als Text
        return _excel_serial(float(t))
    return None


def fnum(v, decimal: Optional[str] = None) -> Optional[float]:
    """Zahl aus Text/Nummer; toleriert Dezimalkomma, Tausenderpunkte, Waehrungszeichen.

    decimal: "," oder "." erzwingt das Dezimaltrennzeichen (CSV-Import bestimmt es je
    Spalte); None = Heuristik je Wert.
    """
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        try:
            f = float(v)
        except Exception:                                       # noqa: BLE001
            return None
        return f if math.isfinite(f) else None
    t = str(v).strip().strip('"').strip("'")
    for ch in ("\u00a0", "\u202f", " ", "'", "€", "$", "£"):
        t = t.replace(ch, "")
    if not t or t.lower() in {"-", "--", "n/a", "na", "nan", "null", "none"}:
        return None
    if decimal == ",":
        t = t.replace(".", "").replace(",", ".")
    elif decimal == ".":
        t = t.replace(",", "")
    elif "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        f = float(t)
    except Exception:                                           # noqa: BLE001
        return None
    return f if math.isfinite(f) else None


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def stdev(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def quantile(sorted_xs: Sequence[float], q: float) -> Optional[float]:
    """Quantil mit linearer Interpolation (wie Excel QUANTIL.INKL); Eingabe aufsteigend."""
    n = len(sorted_xs)
    if not n:
        return None
    if n == 1:
        return sorted_xs[0]
    pos = max(0.0, min(1.0, q)) * (n - 1)
    lo = int(math.floor(pos))
    hi = min(n - 1, lo + 1)
    frac = pos - lo
    return sorted_xs[lo] * (1.0 - frac) + sorted_xs[hi] * frac


def add_years(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:                                          # 29. Februar
        return d.replace(year=d.year + n, day=28)


def periods_per_year(dates: Sequence[date]) -> float:
    """Kurse je Jahr aus den Daten (Boerse ~252, Krypto ~365, Wochendaten ~52)."""
    if len(dates) < 2:
        return 252.0
    yrs = (dates[-1] - dates[0]).days / 365.25
    if yrs <= 0.05:
        return 252.0
    return max(1.0, min(366.0, (len(dates) - 1) / yrs))


def _r(x: Optional[float], nd: int = 6) -> Optional[float]:
    if x is None:
        return None
    try:
        if not math.isfinite(x):
            return None
    except TypeError:
        return None
    return round(x, nd)


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
# Signal-Engine (Logik der RSL-Ampel gegen die Original-Arbeitsmappe validiert)
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


def first_valid_index(sig: Dict) -> Optional[int]:
    """Erster Index mit gueltigem Signalwert (nach dem Vorlauf des Durchschnitts)."""
    for k, r in enumerate(sig.get("ratios") or []):
        if r is not None:
            return k
    return None


def signal_ready_date(sig_dates: Sequence[date], sig: Dict, signal_type: str,
                      exec_lag: int) -> Optional[date]:
    """Frühester Handelstag, an dem das Signal inkl. Ausführungs-Lag gültig ist.

    None = keine Einschränkung (Signaltyp "none"). Wirft ValueError, wenn der
    Signalgeber für die Parameter zu kurz ist.
    """
    if signal_type == "none":
        return None
    fv = first_valid_index(sig)
    if fv is None:
        raise ValueError("Der Signalgeber hat zu wenig Kurse für die gewählten Signal-Parameter "
                         "(Vorlauf des Durchschnitts bzw. Momentum-Zeitraums).")
    if exec_lag <= 0:
        return sig_dates[fv]
    k = fv + exec_lag - 1
    if k >= len(sig_dates):
        raise ValueError("Der Signalgeber hat zu wenig Kurse nach dem Vorlauf.")
    return sig_dates[k] + timedelta(days=1)


# --------------------------------------------------------------------------- #
# Steuern (vereinfachtes deutsches Recht, Privatanleger)
# --------------------------------------------------------------------------- #

# Regel -> (Bezeichnung, Steuerart, Teilfreistellung)
TAX_RULES: Dict[str, Tuple[str, str, float]] = {
    "fund_equity": ("Aktienfonds/-ETF: Abgeltungsteuer, 30 % Teilfreistellung", "abgst", 0.30),
    "fund_mixed": ("Mischfonds: Abgeltungsteuer, 15 % Teilfreistellung", "abgst", 0.15),
    "securities": ("Wertpapier ohne Teilfreistellung (Aktie, Anleihe-ETF): Abgeltungsteuer",
                   "abgst", 0.0),
    "private_sale": ("Privates Veräußerungsgeschäft §23 EStG (Krypto, Gold-ETC): "
                     "steuerfrei nach 1 Jahr Haltedauer", "p23", 0.0),
}
TAX_RULE_CHOICES = ([{"id": "auto", "name": "automatisch je Instrument"}] +
                    [{"id": k, "name": v[0]} for k, v in TAX_RULES.items()])

P23_SYMBOLS = {"4GLD.DE", "BTCE.DE", "VBTC.DE", "XAD5.DE", "EWG2.SG"}
_RX_P23 = re.compile(r"\b(bitcoin|ethereum|krypto\w*|crypto\w*|solana|litecoin|ripple|xrp|"
                     r"cardano|gold|silber|silver|platin\w*|palladium|edelmetall\w*|"
                     r"precious metals?)\b")
_RX_P23_EXCL = re.compile(r"\b(miners?|mining|minen|aktien?|equity|equities|stocks?|blockchain|"
                          r"companies|producers?|royalt\w*)\b")
_RX_BOND = re.compile(r"\b(bonds?|anleihen?\w*|renten\w*|treasur\w*|aggregate|govt|government|"
                      r"corporates?|geldmarkt\w*|money market|t-bills?|loans?|floating rate|"
                      r"inflation[- ]linked)\b")
_RX_MIXED = re.compile(r"\b(multi[- ]asset|mischfonds|balanced|allocation)\b")
_RX_CRYPTO_SYM = re.compile(r"^(BTC|ETH|SOL|XRP|ADA|LTC|DOGE|DOT|BNB)([-_/]?(EUR|USD|USDT|CHF|GBP))?$")


def classify_tax_rule(symbol: str, name: str = "", itype: str = "", source: str = "",
                      meta: Optional[Dict] = None) -> str:
    """Steuerregel aus Instrumenttyp, Name und Symbol ableiten (Näherung, überschreibbar)."""
    sym = (symbol or "").upper().strip()
    t = (itype or "").upper()
    nm = (name or "").lower()
    meta = meta or {}
    if source == "synthetic" or "@" in sym:
        try:
            fac = float(meta.get("factor", 1.0))
        except (TypeError, ValueError):
            fac = 1.0
        return "securities" if fac < 0 else "fund_equity"
    if t == "CRYPTOCURRENCY" or sym in P23_SYMBOLS or _RX_CRYPTO_SYM.match(sym):
        return "private_sale"
    if _RX_P23.search(nm) and not _RX_P23_EXCL.search(nm):
        return "private_sale"
    if _RX_BOND.search(nm):
        return "securities"
    if _RX_MIXED.search(nm):
        return "fund_mixed"
    if t == "EQUITY":
        return "securities"
    return "fund_equity"                     # ETF, Index (als ETF-Stellvertreter), CSV


def series_tax_rule(s: Optional[Series]) -> str:
    if s is None:
        return "fund_equity"
    return classify_tax_rule(s.symbol, s.name, (s.meta or {}).get("type", ""), s.source, s.meta)


def tax_rule_label(rule: Optional[str]) -> str:
    return TAX_RULES.get(rule or "", ("–", "", 0.0))[0]


def taxable_amount(gain: float, rule: Optional[str], buy_date: Optional[date],
                   d: date) -> Tuple[float, str]:
    """Steuerlich relevanter Betrag eines (fiktiven) Verkaufs und die Steuerart.

    abgst: Gewinn/Verlust nach Teilfreistellung.
    p23  : Gewinn/Verlust nur innerhalb der Haltefrist (Verkauf bis 1 Jahr nach Kauf),
           danach steuerfrei (und Verluste unbeachtlich).
    """
    _label, kind, tf = TAX_RULES.get(rule or "securities", TAX_RULES["securities"])
    if kind == "p23":
        if buy_date is not None and d > add_years(buy_date, 1):
            return 0.0, kind
        return gain, kind
    return gain * (1.0 - tf), kind


class TaxLedger:
    """Steuer-Buchhaltung je Kalenderjahr.

    abgst: Abgeltungsteuer. Saldo des Jahres, Verlusttopf mit Vortrag in Folgejahre,
           Sparerpauschbetrag je Jahr (nach Verlustverrechnung), unterjährige
           Erstattung bei späteren Verlusten (wie Bankpraxis).
    p23  : private Veräußerungsgeschäfte. Persönlicher Steuersatz, Freigrenze je Jahr
           (Gesamtgewinn unter der Grenze bleibt steuerfrei), Verluste nur mit
           §23-Gewinnen verrechenbar (Vortrag).
    Steuer wird inkrementell gebucht: realize() liefert die Zahlung (negativ = Erstattung).
    """

    def __init__(self, rate: float = 0.0, rate23: float = 0.35, allowance: float = 0.0,
                 exemption23: float = 0.0, loss_offset: bool = True):
        self.rate = max(0.0, rate)
        self.rate23 = max(0.0, rate23)
        self.allowance = max(0.0, allowance)
        self.exemption23 = max(0.0, exemption23)
        self.loss_offset = loss_offset
        self.year: Optional[int] = None
        self.net = self.paid = self.carry = 0.0
        self.net23 = self.paid23 = self.carry23 = 0.0

    def _roll(self, year: int) -> None:
        if self.year is None:
            self.year = year
            return
        if year <= self.year:
            return
        if self.loss_offset:
            if self.net >= 0.0:
                self.carry = max(0.0, self.carry - self.net)
            else:
                self.carry += -self.net
            if self.net23 >= 0.0:
                if self.net23 >= self.exemption23:
                    self.carry23 = max(0.0, self.carry23 - self.net23)
            else:
                self.carry23 += -self.net23
        self.net = self.paid = self.net23 = self.paid23 = 0.0
        self.year = year

    def _due(self, net: float) -> float:
        if net <= 0.0:
            return 0.0
        c = self.carry if self.loss_offset else 0.0
        return self.rate * max(0.0, net - c - self.allowance)

    def _due23(self, net23: float) -> float:
        if net23 <= 0.0 or net23 < self.exemption23:
            return 0.0
        c = self.carry23 if self.loss_offset else 0.0
        return self.rate23 * max(0.0, net23 - c)

    def realize(self, d: date, amount: float, kind: str = "abgst") -> float:
        self._roll(d.year)
        if amount < 0.0 and not self.loss_offset:
            amount = 0.0
        if kind == "p23":
            self.net23 += amount
            due = self._due23(self.net23)
            delta, self.paid23 = due - self.paid23, due
            return delta
        self.net += amount
        due = self._due(self.net)
        delta, self.paid = due - self.paid, due
        return delta

    def hypothetical(self, d: date, amount: float, kind: str = "abgst") -> float:
        """Zusätzliche Steuer, wenn 'amount' heute realisiert würde (ohne Buchung)."""
        self._roll(d.year)
        if amount < 0.0 and not self.loss_offset:
            amount = 0.0
        if kind == "p23":
            return self._due23(self.net23 + amount) - self.paid23
        return self._due(self.net + amount) - self.paid


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
    holding_days: Optional[int] = None

    def to_dict(self) -> Dict:
        return {"date": self.date.isoformat(), "action": self.action, "symbol": self.symbol,
                "price": round(self.price, 4), "units": round(self.units, 6),
                "value": round(self.value, 2), "fee": round(self.fee, 2),
                "tax": round(self.tax, 2), "gain": round(self.gain, 2),
                "reason": self.reason, "holding_days": self.holding_days}


def _f(p: Dict, key: str, default: float) -> float:
    v = p.get(key)
    if v is None or v == "":
        return float(default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _new_ledger(p: Dict) -> TaxLedger:
    return TaxLedger(rate=_f(p, "tax_rate", 0.0) / 100.0,
                     rate23=_f(p, "tax_rate_private", 35.0) / 100.0,
                     allowance=_f(p, "tax_allowance", 0.0),
                     exemption23=_f(p, "tax_exemption_private", 1000.0),
                     loss_offset=bool(p.get("loss_offset", True)))


def run_backtest(risk: Series, signal_series: Series, config: Dict,
                 defensive: Optional[Series] = None,
                 instruments: Optional[Dict[str, Series]] = None,
                 signal: Optional[Dict] = None,
                 schedule: Optional[List[Tuple[date, Dict]]] = None,
                 lean: bool = False) -> Dict:
    """Simuliert Strategie und Benchmark (Buy & Hold der Risiko-Anlage, gleiche Kosten/Steuer).

    signal   : vorberechnetes Signal (compute_signal) – spart Zeit bei Serienläufen.
    schedule : [(ab_datum, signal), ...] – Signal wechselt an Stichtagen (Walk-Forward).
    lean     : nur Kennzahlen der Strategie (Parameter-Suche, Walk-Forward-Training).
    """
    p = config
    cap0 = _f(p, "capital", 10000.0)
    fee_fixed = _f(p, "fee_fixed", 0.0)
    fee_pct = _f(p, "fee_pct", 0.0) / 100.0
    slip = _f(p, "slippage_bps", 0.0) / 10000.0
    tax_mode = p.get("tax_mode") or "accrued"
    if tax_mode not in ("accrued", "realized", "none"):
        tax_mode = "accrued"
    cash_rate = _f(p, "cash_rate", 0.0) / 100.0
    exec_lag = max(0, int(_f(p, "exec_lag", 1)))
    rotation = p.get("rotation") or None
    defensive_is_cash = bool(p.get("defensive_is_cash", True))
    signal_type = p.get("signal_type") or "ampel"
    tax_rule_cfg = p.get("tax_rule") or "securities"
    tax_interest = bool(p.get("tax_interest", True))
    instruments = dict(instruments or {})
    if defensive is not None:
        instruments[defensive.symbol] = defensive
    instruments[risk.symbol] = risk
    if not risk.dates:
        raise ValueError(f"{risk.symbol}: keine Kurse vorhanden.")

    # ---- Signal und Vorlauf (Backtest beginnt erst, wenn das Signal gültig ist)
    if signal is None:
        signal = compute_signal(signal_series, signal_type, p.get("params") or {})
    plans = sorted(schedule, key=lambda x: x[0]) if schedule else [(None, signal)]
    sig_dates = signal_series.dates
    ready = signal_ready_date(sig_dates, plans[0][1], signal_type, exec_lag)
    start, end = pdate(p.get("start")), pdate(p.get("end"))
    requested = start or risk.dates[0]
    eff_start = start
    clamped = False
    if ready is not None and requested < ready:
        eff_start, clamped = ready, True
    lo = bisect.bisect_left(risk.dates, eff_start) if eff_start else 0
    hi = bisect.bisect_right(risk.dates, end) if end else len(risk.dates)
    if hi - lo < 30:
        extra = f" Das Signal ist erst ab {ready.isoformat()} gültig (Vorlauf)." if clamped else ""
        raise ValueError("Zu wenige Kurstage im gewählten Zeitraum (mindestens 30 nötig)." + extra)
    dates = risk.dates[lo:hi]
    n = len(dates)
    rpx = [risk.value_at(i) for i in range(lo, hi)]
    ppy = periods_per_year(dates)

    # ---- Zustand je Handelstag: Signal vom letzten Signaltag VOR dem Handelstag (Lag >= 1)
    if signal_type == "none":
        state_at = [1] * n
    else:
        starts = [pl[0] for pl in plans]
        state_at = []
        for d in dates:
            k = max(0, bisect.bisect_right(starts, d) - 1) if schedule else 0
            arr = plans[k][1]["states"]
            if exec_lag == 0:
                j = bisect.bisect_right(sig_dates, d) - 1
            else:
                j = bisect.bisect_left(sig_dates, d) - exec_lag
            state_at.append(int(arr[j]) if j >= 0 else 0)

    def target_for(d: date, st: int) -> str:
        if rotation:
            row = (rotation.get("green") if st else rotation.get("red")) or []
            sym = row[d.month - 1] if len(row) >= 12 else None
            if sym:
                return sym
        if st:
            return risk.symbol
        if defensive_is_cash or defensive is None:
            return "KASSE"
        return defensive.symbol

    cursors: Dict[str, int] = {}

    def px_other(sym: str, d: date) -> Optional[float]:
        s = instruments.get(sym)
        if s is None or not s.dates:
            return None
        c = cursors.get(sym)
        if c is None:
            c = bisect.bisect_right(s.dates, d) - 1
        else:
            nd = len(s.dates)
            while c + 1 < nd and s.dates[c + 1] <= d:
                c += 1
        cursors[sym] = c
        if c < 0:
            return None
        v = s.value_at(c)
        return v if v and v > 0 else None

    def fee_of(amount: float) -> float:
        return fee_fixed + fee_pct * amount

    rule_cache: Dict[str, str] = {}

    def rule_for(sym: str) -> str:
        r = rule_cache.get(sym)
        if r is None:
            r = tax_rule_cfg if tax_rule_cfg in TAX_RULES else series_tax_rule(instruments.get(sym))
            rule_cache[sym] = r
        return r

    grow_cache: Dict[int, float] = {}

    def grow(days: int) -> float:
        g = grow_cache.get(days)
        if g is None:
            g = (1.0 + cash_rate) ** (days / 365.0) - 1.0
            grow_cache[days] = g
        return g

    ledger = _new_ledger(p) if tax_mode != "none" else None
    units = cost_basis = 0.0
    pos, pos_rule, buy_d = "KASSE", None, None
    cash = cap0
    total_fee = total_tax = total_interest = realized = pending_int = last_hyp = 0.0
    n_trades = 0
    equity: List[float] = []
    gross_series: List[float] = []
    states_out: List[int] = []
    pos_out: List[str] = []
    trades: List[Trade] = []
    prev_d: Optional[date] = None

    def settle_interest(d: date) -> None:
        """Monatliche Zinsgutschrift: Abgeltungsteuer auf Zinsen (Pauschbetrag/Verlusttopf)."""
        nonlocal pending_int, cash, total_tax
        if pending_int > 0.0 and ledger is not None and tax_interest:
            t = ledger.realize(d, pending_int, "abgst")
            cash -= t
            total_tax += t
        pending_int = 0.0

    for i in range(n):
        d = dates[i]
        if prev_d is not None:
            if cash > 0.0 and cash_rate:
                days = (d - prev_d).days
                if days > 0:
                    it = cash * grow(days)
                    cash += it
                    total_interest += it
                    pending_int += it
            if d.month != prev_d.month:
                settle_interest(prev_d)
        prev_d = d
        if i == n - 1:
            settle_interest(d)

        st = state_at[i]
        states_out.append(st)
        tgt = target_for(d, st)
        reason = "Rotation" if rotation else ("Signal grün" if st else "Signal rot")
        if tgt != pos:
            # ---- Verkauf
            if units > 0.0 and pos != "KASSE":
                px = rpx[i] if pos == risk.symbol else px_other(pos, d)
                if not px:
                    px = cost_basis / units
                px_eff = px * (1.0 - slip)
                gross = units * px_eff
                fee = fee_of(gross)
                gain = gross - fee - cost_basis
                tax = 0.0
                if ledger is not None:
                    amt, kind = taxable_amount(gain, pos_rule, buy_d, d)
                    tax = ledger.realize(d, amt, kind)
                cash += gross - fee - tax
                total_fee += fee
                total_tax += tax
                realized += gain - tax
                n_trades += 1
                if not lean:
                    trades.append(Trade(d, "VERKAUF", pos, px_eff, units, gross, fee, tax, gain,
                                        reason, (d - buy_d).days if buy_d else None))
                units, cost_basis, pos, pos_rule, buy_d = 0.0, 0.0, "KASSE", None, None
            # ---- Kauf (scheitert er, bleibt das Geld in der Kasse)
            if tgt != "KASSE":
                settle_interest(d)
                px = rpx[i] if tgt == risk.symbol else px_other(tgt, d)
                if px:
                    px_eff = px * (1.0 + slip)
                    avail = cash
                    amount = max(0.0, (avail - fee_fixed) / (1.0 + fee_pct))
                    fee = fee_of(amount)
                    if amount >= 0.5 and fee <= avail + 1e-9:
                        units = amount / px_eff
                        cost_basis = amount + fee
                        cash = avail - amount - fee
                        if abs(cash) < 1e-7:
                            cash = 0.0
                        total_fee += fee
                        n_trades += 1
                        pos, pos_rule, buy_d = tgt, rule_for(tgt), d
                        if not lean:
                            trades.append(Trade(d, "KAUF", tgt, px_eff, units, amount, fee,
                                                0.0, 0.0, reason, None))

        # ---- Tagesbewertung
        if units > 0.0 and pos != "KASSE":
            px = rpx[i] if pos == risk.symbol else (px_other(pos, d) or 0.0)
            gross = units * px
            if tax_mode == "accrued":
                proceeds = gross * (1.0 - slip)
                net_before = proceeds - fee_of(proceeds)
                hyp = 0.0
                if ledger is not None:
                    amt, kind = taxable_amount(net_before - cost_basis, pos_rule, buy_d, d)
                    hyp = ledger.hypothetical(d, amt, kind)
                last_hyp = hyp
                value = net_before - hyp + cash
            else:
                value = gross + cash
            gross_series.append(gross + cash)
        else:
            value = cash
            last_hyp = 0.0
            if tax_mode == "accrued" and ledger is not None and tax_interest and pending_int > 0.0:
                last_hyp = ledger.hypothetical(d, pending_int, "abgst")
                value -= last_hyp
            gross_series.append(cash)
        equity.append(value)
        pos_out.append(pos)

    if lean:
        return {"metrics": quick_metrics(dates, equity, cap0, ppy, cash_rate, states_out,
                                         pos_out, n_trades),
                "start": dates[0].isoformat(), "end": dates[-1].isoformat()}

    def latent_at_end(lg: Optional[TaxLedger], u: float, basis: float, rule: Optional[str],
                      bdate: Optional[date], px: float) -> float:
        if lg is None or u <= 0.0:
            return 0.0
        proceeds = u * px * (1.0 - slip)
        amt, kind = taxable_amount(proceeds - fee_of(proceeds) - basis, rule, bdate, dates[-1])
        return lg.hypothetical(dates[-1], amt, kind)

    if tax_mode == "accrued":
        latent = last_hyp
    elif tax_mode == "realized" and pos != "KASSE":
        lpx = rpx[-1] if pos == risk.symbol else (px_other(pos, dates[-1]) or 0.0)
        latent = latent_at_end(ledger, units, cost_basis, pos_rule, buy_d, lpx)
    else:
        latent = 0.0

    # ---- Benchmark: Buy & Hold der Risiko-Anlage, identische Kosten-/Steuerlogik
    b_rule = rule_for(risk.symbol)
    b_ledger = _new_ledger(p) if tax_mode != "none" else None
    b_amount = max(0.0, (cap0 - fee_fixed) / (1.0 + fee_pct))
    b_fee = fee_of(b_amount)
    if rpx[0] > 0 and b_amount >= 0.5 and b_fee <= cap0 + 1e-9:
        b_units = b_amount / (rpx[0] * (1.0 + slip))
        b_cost = b_amount + b_fee
        b_cash = cap0 - b_amount - b_fee
        if abs(b_cash) < 1e-7:
            b_cash = 0.0
    else:
        b_units, b_cost, b_cash = 0.0, 0.0, cap0
    bench: List[float] = []
    b_hyp = 0.0
    for i in range(n):
        gross = b_units * rpx[i]
        if tax_mode == "accrued" and b_units > 0.0:
            proceeds = gross * (1.0 - slip)
            net_before = proceeds - fee_of(proceeds)
            b_hyp = 0.0
            if b_ledger is not None:
                amt, kind = taxable_amount(net_before - b_cost, b_rule, dates[0], dates[i])
                b_hyp = b_ledger.hypothetical(dates[i], amt, kind)
            bench.append(net_before - b_hyp + b_cash)
        else:
            bench.append(gross + b_cash)
    if tax_mode == "accrued":
        b_latent = b_hyp
    elif tax_mode == "realized":
        b_latent = latent_at_end(b_ledger, b_units, b_cost, b_rule, dates[0], rpx[-1])
    else:
        b_latent = 0.0

    totals = {"fees": total_fee, "taxes": total_tax, "interest": total_interest,
              "realized": realized, "tax_latent": latent, "bench_tax_latent": b_latent}
    extra_meta = {
        "periods_per_year": round(ppy, 2),
        "warmup": {"signal_ready": ready.isoformat() if ready else None,
                   "requested_start": start.isoformat() if start else None,
                   "clamped": clamped},
        "tax": {"mode": tax_mode, "rule": tax_rule_cfg, "risk_rule": b_rule,
                "risk_rule_label": tax_rule_label(b_rule),
                "rules_used": {s: tax_rule_label(r) for s, r in rule_cache.items()},
                "rate": _f(p, "tax_rate", 0.0), "rate_private": _f(p, "tax_rate_private", 35.0),
                "allowance": _f(p, "tax_allowance", 0.0),
                "exemption_private": _f(p, "tax_exemption_private", 1000.0),
                "interest_taxed": tax_interest},
    }
    return build_result(dates, equity, gross_series, bench, states_out, pos_out, trades, totals,
                        cap0, p, plans[0][1], signal_series, ppy, cash_rate, extra_meta)


# --------------------------------------------------------------------------- #
# Ergebnisaufbau
# --------------------------------------------------------------------------- #

def build_result(dates: List[date], equity: List[float], gross: List[float],
                 bench: List[float], states: List[int], positions: List[str],
                 trades: List[Trade], totals: Dict, cap0: float, config: Dict, sig: Dict,
                 signal_series: Series, ppy: float, rf: float, extra_meta: Dict) -> Dict:
    dd = drawdown(equity)
    bdd = drawdown(bench)
    m = compute_metrics(dates, equity, bench, trades, totals, cap0, states, positions, ppy, rf)

    sig_out = {"dates": [], "price": [], "sma": [], "ratio": [], "state": []}
    for d in dates:
        j = signal_series.index_on_or_before(d)
        if j < 0:
            continue
        sma_v, rat_v = sig["sma"][j], sig["ratios"][j]
        sig_out["dates"].append(d.isoformat())
        sig_out["price"].append(round(sig["prices"][j], 4))
        sig_out["sma"].append(round(sma_v, 4) if sma_v else None)
        sig_out["ratio"].append(round(rat_v, 6) if rat_v else None)
        sig_out["state"].append(int(sig["states"][j]))

    meta = {"risk": config.get("risk"), "signal": config.get("signal"),
            "signal_type": config.get("signal_type"),
            "signal_label": SIGNAL_INFO.get(config.get("signal_type", ""), ""),
            "params": config.get("params") or {},
            "start": dates[0].isoformat(), "end": dates[-1].isoformat(),
            "days": len(dates), "capital": cap0,
            "exec_lag": config.get("exec_lag", 1),
            "defensive": None if config.get("defensive_is_cash", True) else config.get("defensive")}
    meta.update(extra_meta)
    return {
        "meta": meta,
        "equity": {"dates": [d.isoformat() for d in dates],
                   "strategy": [round(v, 2) for v in equity],
                   "gross": [round(v, 2) for v in gross],
                   "benchmark": [round(v, 2) for v in bench],
                   "drawdown": [round(v, 6) for v in dd],
                   "bench_drawdown": [round(v, 6) for v in bdd],
                   "state": states, "position": positions},
        "signal": sig_out,
        "metrics": m,
        "yearly": yearly_returns(dates, equity, bench, cap0),
        "monthly": monthly_matrix(dates, equity, cap0),
        "rolling": rolling_analysis(dates, equity, bench, ppy),
        "drawdowns": drawdown_episodes(dates, equity),
        "bench_drawdowns": drawdown_episodes(dates, bench),
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

def _returns(values: Sequence[float]) -> List[float]:
    out: List[float] = []
    for i in range(1, len(values)):
        a = values[i - 1]
        out.append(values[i] / a - 1.0 if a > 0 else 0.0)
    return out


def _month_ends(dates: Sequence[date], values: Sequence[float]):
    keys: List[Tuple[int, int]] = []
    vals: List[float] = []
    for d, v in zip(dates, values):
        k = (d.year, d.month)
        if keys and keys[-1] == k:
            vals[-1] = v
        else:
            keys.append(k)
            vals.append(v)
    return keys, vals


def _period_returns(vals: Sequence[float], base: float) -> List[float]:
    out, prev = [], base
    for v in vals:
        out.append(v / prev - 1.0 if prev > 0 else 0.0)
        prev = v
    return out


def _dd_scan(values: Sequence[float]):
    """Verlustphasen (Hoch, Tief, Erholung|None, Tiefe), max. Drawdown, Ulcer-Index."""
    eps = []
    peak_i, trough_i, depth = 0, None, 0.0
    sq, maxdd = 0.0, 0.0
    for i, v in enumerate(values):
        pv = values[peak_i]
        if v >= pv:
            if trough_i is not None:
                eps.append((peak_i, trough_i, i, depth))
                trough_i, depth = None, 0.0
            peak_i = i
            continue
        dd = v / pv - 1.0 if pv > 0 else 0.0
        sq += dd * dd
        if trough_i is None or dd < depth:
            trough_i, depth = i, dd
        if dd < maxdd:
            maxdd = dd
    if trough_i is not None:
        eps.append((peak_i, trough_i, None, depth))
    ulcer = math.sqrt(sq / len(values)) if values else 0.0
    return eps, maxdd, ulcer


def series_stats(dates: List[date], values: List[float], cap0: float, ppy: float,
                 rf: float) -> Dict:
    """Kennzahlen einer Vermögenskurve. Sharpe/Sortino mit risikolosem Zins rf (p. a.)."""
    n = len(values)
    rets = _returns(values)
    nr = len(rets)
    years = max(1, (dates[-1] - dates[0]).days) / 365.25
    end = values[-1]
    total = end / cap0 - 1.0 if cap0 else 0.0
    cagr = (end / cap0) ** (1.0 / years) - 1.0 if cap0 > 0 and end > 0 else -1.0
    rf_p = (1.0 + rf) ** (1.0 / ppy) - 1.0 if rf > -1.0 else 0.0
    mu = mean(rets)
    sd = stdev(rets)
    ex = mu - rf_p
    sharpe = ex / sd * math.sqrt(ppy) if sd > EPS else 0.0
    dsd = math.sqrt(sum(min(0.0, r - rf_p) ** 2 for r in rets) / nr) if nr else 0.0
    sortino = ex / dsd * math.sqrt(ppy) if dsd > EPS else 0.0
    eps, maxdd, ulcer = _dd_scan(values)
    calmar = cagr / abs(maxdd) if abs(maxdd) > EPS else 0.0
    martin = (cagr - rf) / ulcer if ulcer > EPS else 0.0
    srt = sorted(rets)
    var95 = quantile(srt, 0.05) if srt else 0.0
    tail = [r for r in rets if r <= var95]
    cvar95 = mean(tail) if tail else var95
    skew = kurt = 0.0
    if nr > 3:
        m2 = sum((r - mu) ** 2 for r in rets) / nr
        if m2 > EPS:
            skew = (sum((r - mu) ** 3 for r in rets) / nr) / m2 ** 1.5
            kurt = (sum((r - mu) ** 4 for r in rets) / nr) / (m2 * m2) - 3.0
    dd_peak = dd_trough = dd_rec = None
    dd_days = dd_rec_days = None
    longest = 0
    if eps:
        pk, tr, rc, _dep = min(eps, key=lambda e: e[3])
        dd_peak, dd_trough = dates[pk].isoformat(), dates[tr].isoformat()
        dd_days = ((dates[rc] if rc is not None else dates[-1]) - dates[pk]).days
        if rc is not None:
            dd_rec, dd_rec_days = dates[rc].isoformat(), (dates[rc] - dates[tr]).days
        longest = max(((dates[e[2]] if e[2] is not None else dates[-1]) - dates[e[0]]).days
                      for e in eps)
    _keys, mv = _month_ends(dates, values)
    mrets = _period_returns(mv, cap0)
    return {
        "start": dates[0].isoformat(), "end": dates[-1].isoformat(),
        "years": round(years, 2), "trading_days": n,
        "end_capital": round(end, 2), "total_return": _r(total), "cagr": _r(cagr),
        "volatility": _r(sd * math.sqrt(ppy)), "sharpe": _r(sharpe, 3),
        "sortino": _r(sortino, 3), "calmar": _r(calmar, 3), "max_drawdown": _r(maxdd),
        "max_drawdown_peak": dd_peak, "max_drawdown_trough": dd_trough,
        "max_drawdown_recovery": dd_rec, "max_drawdown_days": dd_days,
        "max_drawdown_recovery_days": dd_rec_days, "longest_underwater_days": longest,
        "ulcer_index": _r(ulcer), "martin": _r(martin, 3),
        "var95": _r(var95), "cvar95": _r(cvar95),
        "skewness": _r(skew, 3), "kurtosis": _r(kurt, 3),
        "best_day": _r(max(rets)) if rets else 0.0,
        "worst_day": _r(min(rets)) if rets else 0.0,
        "positive_days": _r(sum(1 for r in rets if r > 0) / nr, 4) if nr else 0.0,
        "positive_months": _r(sum(1 for r in mrets if r > 0) / len(mrets), 4) if mrets else None,
        "best_month": _r(max(mrets)) if mrets else None,
        "worst_month": _r(min(mrets)) if mrets else None,
        "months": len(mrets),
    }


def relative_stats(dates: List[date], eq: List[float], bench: List[float], cap0: float,
                   ppy: float, rf: float) -> Dict:
    """Bezug zur Benchmark: Beta, Korrelation, Jensen-Alpha, Tracking Error, IR, Capture."""
    out: Dict = {"beta": None, "correlation": None, "alpha": None, "tracking_error": None,
                 "information_ratio": None, "up_capture": None, "down_capture": None,
                 "months_up": 0, "months_down": 0}
    rs, rb = _returns(eq), _returns(bench)
    n = min(len(rs), len(rb))
    if n < 3:
        return out
    rs, rb = rs[:n], rb[:n]
    ms, mb = mean(rs), mean(rb)
    cov = sum((a - ms) * (b - mb) for a, b in zip(rs, rb)) / (n - 1)
    vs = sum((a - ms) ** 2 for a in rs) / (n - 1)
    vb = sum((b - mb) ** 2 for b in rb) / (n - 1)
    rf_p = (1.0 + rf) ** (1.0 / ppy) - 1.0 if rf > -1.0 else 0.0
    if vb > EPS:
        beta = cov / vb
        out["beta"] = _r(beta, 3)
        out["alpha"] = _r(((ms - rf_p) - beta * (mb - rf_p)) * ppy)
    if vs > EPS and vb > EPS:
        out["correlation"] = _r(cov / math.sqrt(vs * vb), 3)
    diff = [a - b for a, b in zip(rs, rb)]
    te = stdev(diff) * math.sqrt(ppy)
    out["tracking_error"] = _r(te)
    if te > EPS:
        out["information_ratio"] = _r(mean(diff) * ppy / te, 3)

    def geo(xs: List[float]) -> float:
        prod = 1.0
        for x in xs:
            prod *= 1.0 + x
        return prod ** (1.0 / len(xs)) - 1.0 if prod > 0 else -1.0

    sm = _period_returns(_month_ends(dates, eq)[1], cap0)
    bm = _period_returns(_month_ends(dates, bench)[1], cap0)
    up = [(a, b) for a, b in zip(sm, bm) if b > 0]
    dn = [(a, b) for a, b in zip(sm, bm) if b < 0]
    out["months_up"], out["months_down"] = len(up), len(dn)
    if up:
        gb = geo([b for _, b in up])
        if abs(gb) > EPS:
            out["up_capture"] = _r(geo([a for a, _ in up]) / gb, 4)
    if dn:
        gb = geo([b for _, b in dn])
        if abs(gb) > EPS:
            out["down_capture"] = _r(geo([a for a, _ in dn]) / gb, 4)
    return out


def trade_stats(trades: List[Trade]) -> Dict:
    sells = [t for t in trades if t.action == "VERKAUF"]
    gains = [t.gain for t in sells]
    wins = [g for g in gains if g > 0]
    losses = [g for g in gains if g <= 0]
    streak = longest = 0
    for g in gains:
        streak = streak + 1 if g <= 0 else 0
        longest = max(longest, streak)
    holds = [t.holding_days for t in sells if t.holding_days is not None]
    sl = sum(losses)
    return {
        "round_trips": len(sells),
        "win_rate": _r(len(wins) / len(sells), 4) if sells else None,
        "profit_factor": _r(sum(wins) / -sl, 3) if sl < -EPS else None,
        "avg_win": _r(mean(wins), 2) if wins else None,
        "avg_loss": _r(mean(losses), 2) if losses else None,
        "best_trade": _r(max(gains), 2) if gains else None,
        "worst_trade": _r(min(gains), 2) if gains else None,
        "max_loss_streak": longest,
        "avg_holding_days": _r(mean(holds), 1) if holds else None,
        "whipsaws": sum(1 for h in holds if h < 30),
    }


def quick_metrics(dates: List[date], values: List[float], cap0: float, ppy: float, rf: float,
                  states: List[int], positions: List[str], n_trades: int) -> Dict:
    """Schlanke Kennzahlen in einem Durchlauf (Parameter-Suche, Walk-Forward)."""
    n = len(values)
    if n < 3:
        return {}
    years = max(1, (dates[-1] - dates[0]).days) / 365.25
    end = values[-1]
    cagr = (end / cap0) ** (1.0 / years) - 1.0 if cap0 > 0 and end > 0 else -1.0
    rf_p = (1.0 + rf) ** (1.0 / ppy) - 1.0 if rf > -1.0 else 0.0
    s = s2 = d2 = sq = maxdd = 0.0
    peak = prev = values[0]
    for i in range(1, n):
        v = values[i]
        r = v / prev - 1.0 if prev > 0 else 0.0
        prev = v
        s += r
        s2 += r * r
        x = r - rf_p
        if x < 0.0:
            d2 += x * x
        if v >= peak:
            peak = v
        else:
            dd = v / peak - 1.0 if peak > 0 else 0.0
            sq += dd * dd
            if dd < maxdd:
                maxdd = dd
    k = n - 1
    mu = s / k
    sd = math.sqrt(max(0.0, (s2 - k * mu * mu) / (k - 1))) if k > 1 else 0.0
    dsd = math.sqrt(d2 / k)
    ulcer = math.sqrt(sq / n)
    tim = sum(1 for st, p in zip(states, positions) if st and p != "KASSE") / n
    return {
        "cagr": _r(cagr), "total_return": _r(end / cap0 - 1.0 if cap0 else 0.0),
        "max_drawdown": _r(maxdd), "volatility": _r(sd * math.sqrt(ppy)),
        "sharpe": _r((mu - rf_p) / sd * math.sqrt(ppy), 3) if sd > EPS else 0.0,
        "sortino": _r((mu - rf_p) / dsd * math.sqrt(ppy), 3) if dsd > EPS else 0.0,
        "calmar": _r(cagr / abs(maxdd), 3) if abs(maxdd) > EPS else 0.0,
        "ulcer_index": _r(ulcer), "martin": _r((cagr - rf) / ulcer, 3) if ulcer > EPS else 0.0,
        "end_capital": round(end, 2), "trades": n_trades, "time_in_market": _r(tim, 4),
        "years": round(years, 2),
    }


def compute_metrics(dates: List[date], equity: List[float], bench: List[float],
                    trades: List[Trade], totals: Dict, cap0: float, states: List[int],
                    positions: List[str], ppy: float, rf: float) -> Dict:
    n = len(equity)
    if n < 2:
        return {}
    s = series_stats(dates, equity, cap0, ppy, rf)
    b = series_stats(dates, bench, cap0, ppy, rf)
    m: Dict = dict(s)
    for k, v in b.items():
        if k not in ("start", "end", "years", "trading_days"):
            m["bench_" + k] = v
    m.update(relative_stats(dates, equity, bench, cap0, ppy, rf))
    m.update(trade_stats(trades))
    risk_days = sum(1 for st, p in zip(states, positions) if st and p != "KASSE")
    inv_days = sum(1 for p in positions if p != "KASSE")
    switches = sum(1 for i in range(1, len(positions)) if positions[i] != positions[i - 1])
    yrs = s["years"] or 1.0
    m.update({
        "start_capital": round(cap0, 2),
        "excess_cagr": _r((s["cagr"] or 0.0) - (b["cagr"] or 0.0)),
        "trades": len(trades), "switches": switches,
        "switches_per_year": _r(switches / yrs, 2),
        "fees": round(totals.get("fees", 0.0), 2), "taxes": round(totals.get("taxes", 0.0), 2),
        "interest": round(totals.get("interest", 0.0), 2),
        "tax_latent": round(totals.get("tax_latent", 0.0), 2),
        "bench_tax_latent": round(totals.get("bench_tax_latent", 0.0), 2),
        "time_in_market": _r(risk_days / n, 4), "time_invested": _r(inv_days / n, 4),
        "start_state": int(states[0]) if states else 0,
        "periods_per_year": round(ppy, 2), "risk_free": _r(rf),
    })
    return m


# --------------------------------------------------------------------------- #
# Perioden, rollierende Renditen, Verlustphasen
# --------------------------------------------------------------------------- #

def yearly_returns(dates: List[date], equity: List[float], bench: List[float],
                   cap0: Optional[float] = None) -> List[Dict]:
    """Kalenderjahresrenditen; das erste Jahr bezieht sich auf das Startkapital."""
    out: List[Dict] = []
    if not dates:
        return out
    cur = dates[0].year
    first_i = 0
    sv = cap0 if cap0 else equity[0]
    sb = cap0 if cap0 else bench[0]

    def row(year: int, i_from: int, i_to: int, sv_: float, sb_: float) -> Dict:
        d0, d1 = dates[i_from], dates[i_to]
        partial = (i_from == 0 and (d0.month, d0.day) > (1, 7)) or \
                  (i_to == len(dates) - 1 and (d1.month, d1.day) < (12, 20))
        return {"year": year,
                "strategy": _r(equity[i_to] / sv_ - 1.0) if sv_ else 0.0,
                "benchmark": _r(bench[i_to] / sb_ - 1.0) if sb_ else 0.0,
                "end": round(equity[i_to], 2), "bench_end": round(bench[i_to], 2),
                "partial": partial}

    for i, d in enumerate(dates):
        if d.year != cur:
            out.append(row(cur, first_i, i - 1, sv, sb))
            cur, first_i = d.year, i
            sv, sb = equity[i - 1], bench[i - 1]
    out.append(row(cur, first_i, len(dates) - 1, sv, sb))
    return out


def monthly_matrix(dates: List[date], equity: List[float], cap0: Optional[float] = None) -> Dict:
    """Monatsrenditen: Zeilen = Jahre, Spalten = Monate (None = kein Handel)."""
    if not dates:
        return {"years": [], "months": MONTHS_DE, "values": []}
    keys, vals = _month_ends(dates, equity)
    rets = _period_returns(vals, cap0 if cap0 else equity[0])
    values: Dict[int, Dict[int, float]] = {}
    for (y, mo), r in zip(keys, rets):
        values.setdefault(y, {})[mo] = r
    years = sorted(values)
    mat = [[_r(values[y][mo]) if mo in values[y] else None for mo in range(1, 13)]
           for y in years]
    return {"years": years, "months": MONTHS_DE, "values": mat}


def rolling_analysis(dates: List[date], equity: List[float], bench: List[float], ppy: float,
                     horizons: Sequence[int] = (1, 3, 5), max_points: int = 600) -> Dict:
    """Rollierende annualisierte Renditen (Strategie vs. Benchmark) je Anlagehorizont.

    Für jeden möglichen Einstiegstag wird die Rendite über h Jahre berechnet
    (überlappende Fenster). Ausgewertet: Spannweite, Median, Anteil Zeiträume mit
    Verlust und Anteil Zeiträume, in denen die Strategie die Benchmark schlägt.
    """
    out = []
    n = len(equity)
    for h in horizons:
        w = int(round(h * ppy))
        if w < 2 or n <= w:
            out.append({"years": h, "window": w, "n": 0})
            continue
        ds: List[date] = []
        sv: List[float] = []
        bv: List[float] = []
        for i in range(w, n):
            a, b0 = equity[i - w], bench[i - w]
            yrs = (dates[i] - dates[i - w]).days / 365.25
            if a <= 0 or b0 <= 0 or yrs <= 0:
                continue
            sv.append((equity[i] / a) ** (1.0 / yrs) - 1.0 if equity[i] > 0 else -1.0)
            bv.append((bench[i] / b0) ** (1.0 / yrs) - 1.0 if bench[i] > 0 else -1.0)
            ds.append(dates[i])
        cnt = len(sv)
        if not cnt:
            out.append({"years": h, "window": w, "n": 0})
            continue

        def summ(v: List[float]) -> Dict:
            srt = sorted(v)
            return {"min": _r(srt[0]), "median": _r(quantile(srt, 0.5)), "max": _r(srt[-1]),
                    "p10": _r(quantile(srt, 0.10)), "mean": _r(mean(v)),
                    "neg_share": _r(sum(1 for x in v if x < 0) / cnt, 4)}

        step = max(1, cnt // max_points)
        idx = list(range(0, cnt, step))
        if idx[-1] != cnt - 1:
            idx.append(cnt - 1)
        out.append({
            "years": h, "window": w, "n": cnt,
            "beats_share": _r(sum(1 for x, y in zip(sv, bv) if x > y) / cnt, 4),
            "strategy": summ(sv), "benchmark": summ(bv),
            "series": {"dates": [ds[k].isoformat() for k in idx],
                       "strategy": [_r(sv[k]) for k in idx],
                       "benchmark": [_r(bv[k]) for k in idx]},
        })
    return {"horizons": out}


def drawdown_episodes(dates: List[date], values: List[float], top: int = 5) -> List[Dict]:
    """Die tiefsten Verlustphasen mit Kalendertagen (Hoch -> Tief -> Erholung)."""
    if not values:
        return []
    eps, _maxdd, _ulcer = _dd_scan(values)
    eps.sort(key=lambda e: e[3])
    out = []
    for pk, tr, rc, depth in eps[:top]:
        end_d = dates[rc] if rc is not None else dates[-1]
        out.append({"peak": dates[pk].isoformat(), "trough": dates[tr].isoformat(),
                    "recovery": dates[rc].isoformat() if rc is not None else None,
                    "depth": _r(depth), "days": (end_d - dates[pk]).days,
                    "decline_days": (dates[tr] - dates[pk]).days,
                    "recovery_days": (dates[rc] - dates[tr]).days if rc is not None else None})
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
        for n in (21, 63, 126, 189, 252, 365):
            combos.append({"momentum_days": n})
    return combos


GRID_KEYS = ("cagr", "max_drawdown", "sharpe", "sortino", "calmar", "martin", "ulcer_index",
             "volatility", "total_return", "end_capital", "trades", "time_in_market")


def _prepare_signals(signal_series: Series, signal_type: str, base: Dict, combos: List[Dict],
                     exec_lag: int):
    """Signale je Kombination einmal berechnen; dazu der späteste Vorlauf aller Kombinationen."""
    sigs: List[Optional[Dict]] = []
    latest: Optional[date] = None
    for c in combos:
        try:
            sd = compute_signal(signal_series, signal_type, {**base, **c})
            ready = signal_ready_date(signal_series.dates, sd, signal_type, exec_lag)
        except ValueError:
            sigs.append(None)
            continue
        sigs.append(sd)
        if ready is not None and (latest is None or ready > latest):
            latest = ready
    return sigs, latest


def run_grid(risk: Series, signal_series: Series, config: Dict,
             defensive: Optional[Series] = None, max_runs: int = 500,
             instruments: Optional[Dict[str, Series]] = None,
             objective: str = "calmar") -> List[Dict]:
    """Alle Kombinationen über denselben Zeitraum (gemeinsamer Vorlauf) testen."""
    base = dict(config.get("params") or {})
    stype = config.get("signal_type", "ampel")
    combos = param_grid(stype)[:max_runs]
    exec_lag = max(0, int(_f(config, "exec_lag", 1)))
    sigs, ready = _prepare_signals(signal_series, stype, base, combos, exec_lag)
    start = pdate(config.get("start"))
    if ready is not None and (start is None or start < ready):
        start = ready
    rows: List[Dict] = []
    for c, sd in zip(combos, sigs):
        if sd is None:
            continue
        params = {**base, **c}
        cfg = dict(config, params=params, start=start.isoformat() if start else None)
        try:
            r = run_backtest(risk, signal_series, cfg, defensive, instruments, signal=sd, lean=True)
        except Exception:                                       # noqa: BLE001
            continue
        m = r["metrics"]
        row = {"params": params, "start": r["start"], "end": r["end"]}
        for k in GRID_KEYS:
            row[k] = m.get(k)
        rows.append(row)
    rows.sort(key=lambda x: x.get(objective) if x.get(objective) is not None else -1e18,
              reverse=True)
    return rows


def walk_forward(risk: Series, signal_series: Series, config: Dict,
                 defensive: Optional[Series] = None, train_years: int = 6, test_years: int = 2,
                 objective: str = "calmar",
                 instruments: Optional[Dict[str, Series]] = None) -> Dict:
    """Rollierende Optimierung über das volle Parameterraster.

    Je Trainingsfenster (Kalenderjahre) wird die beste Kombination gewählt und im
    folgenden Testfenster angewendet. Die Testfenster werden als EINE durchgehende
    Simulation gerechnet (Parameterwechsel an den Fenstergrenzen, keine
    Zwangsliquidation); Buy & Hold läuft über denselben Zeitraum mit.
    """
    stype = config.get("signal_type", "ampel")
    combos = param_grid(stype)
    base = dict(config.get("params") or {})
    train_years, test_years = max(1, int(train_years)), max(1, int(test_years))
    info: Dict = {"windows": [], "oos": None, "objective": objective, "combos": len(combos),
                  "train_years": train_years, "test_years": test_years}
    if not combos:
        info["note"] = "Für diesen Signaltyp gibt es keinen Parameterraum."
        return info
    exec_lag = max(0, int(_f(config, "exec_lag", 1)))
    sigs, ready = _prepare_signals(signal_series, stype, base, combos, exec_lag)
    lo_d, hi_d = risk.dates[0], risk.dates[-1]
    cs, ce = pdate(config.get("start")), pdate(config.get("end"))
    if cs and cs > lo_d:
        lo_d = cs
    if ready and ready > lo_d:
        lo_d = ready
    if ce and ce < hi_d:
        hi_d = ce
    y0 = lo_d.year if (lo_d.month, lo_d.day) <= (1, 7) else lo_d.year + 1
    if date(y0 + train_years, 1, 1) > hi_d:
        info["note"] = (f"Zu wenig Historie für Walk-Forward: nötig sind {train_years} volle "
                        f"Trainingsjahre ab {y0} plus ein Testzeitraum (Daten bis {hi_d.isoformat()}).")
        return info
    windows: List[Dict] = []
    schedule: List[Tuple[date, Dict]] = []
    y = y0 + train_years
    while date(y, 1, 1) <= hi_d:
        tr0, tr1 = date(y - train_years, 1, 1), date(y - 1, 12, 31)
        te0, te1 = date(y, 1, 1), min(date(y + test_years - 1, 12, 31), hi_d)
        best, best_score = None, -1e18
        for k, c in enumerate(combos):
            if sigs[k] is None:
                continue
            cfg = dict(config, params={**base, **c}, start=tr0.isoformat(), end=tr1.isoformat(),
                       capital=10000.0)
            try:
                m = run_backtest(risk, signal_series, cfg, defensive, instruments,
                                 signal=sigs[k], lean=True)["metrics"]
            except Exception:                                   # noqa: BLE001
                continue
            score = m.get(objective)
            if score is not None and score > best_score:
                best, best_score = k, score
        if best is not None:
            windows.append({"train": [tr0.isoformat(), tr1.isoformat()],
                            "test": [te0.isoformat(), te1.isoformat()],
                            "params": combos[best], "score": _r(best_score, 4)})
            schedule.append((te0, sigs[best]))
        y += test_years
    if not schedule:
        info["note"] = "Kein Trainingsfenster auswertbar."
        return info

    cfg = dict(config, start=schedule[0][0].isoformat(), end=hi_d.isoformat(), capital=10000.0)
    res = run_backtest(risk, signal_series, cfg, defensive, instruments,
                       signal=schedule[0][1], schedule=schedule)
    eq = res["equity"]
    ds = eq["dates"]
    for w in windows:
        i0 = bisect.bisect_left(ds, w["test"][0])
        i1 = bisect.bisect_right(ds, w["test"][1]) - 1
        if i0 >= len(ds) or i1 < i0:
            w["test_return"] = w["bench_return"] = None
            continue
        bs = eq["strategy"][i0 - 1] if i0 > 0 else 10000.0
        bb = eq["benchmark"][i0 - 1] if i0 > 0 else 10000.0
        w["test_return"] = _r(eq["strategy"][i1] / bs - 1.0) if bs else None
        w["bench_return"] = _r(eq["benchmark"][i1] / bb - 1.0) if bb else None
    try:
        ref = run_backtest(risk, signal_series, cfg, defensive, instruments, lean=True)["metrics"]
    except Exception:                                           # noqa: BLE001
        ref = None
    m = res["metrics"]
    keep = ("start", "end", "years", "cagr", "bench_cagr", "total_return", "bench_total_return",
            "max_drawdown", "bench_max_drawdown", "calmar", "bench_calmar", "sharpe",
            "bench_sharpe", "martin", "bench_martin", "end_capital", "bench_end_capital",
            "trades", "time_in_market", "up_capture", "down_capture")
    info.update({
        "windows": windows,
        "oos": {"dates": ds, "strategy": eq["strategy"], "benchmark": eq["benchmark"],
                "drawdown": eq["drawdown"], "bench_drawdown": eq["bench_drawdown"]},
        "metrics": {k: m.get(k) for k in keep},
        "reference": ({k: ref.get(k) for k in ("cagr", "max_drawdown", "calmar", "sharpe",
                                               "end_capital")} if ref else None),
        "positive_windows": sum(1 for w in windows if (w.get("test_return") or 0) > 0),
        "beat_windows": sum(1 for w in windows
                            if w.get("test_return") is not None and w.get("bench_return") is not None
                            and w["test_return"] > w["bench_return"]),
        "params_base": base,
    })
    return info


def monte_carlo(equity: List[float], bench: Optional[List[float]] = None, ppy: float = 252.0,
                years: float = 10.0, n_paths: int = 500, block: Optional[int] = None,
                seed: int = 7, start: Optional[float] = None) -> Dict:
    """Block-Bootstrap der historischen Tagesrenditen (Blöcke ≈ 1 Monat erhalten Krisen-
    Häufungen). Strategie und Buy & Hold ziehen dieselben Blöcke (gepaarter Vergleich)."""
    rs = _returns(equity)
    if len(rs) < 60:
        return {}
    rb = _returns(bench) if bench is not None and len(bench) == len(equity) else None
    rnd = random.Random(seed)
    ppy = ppy or 252.0
    horizon = max(20, int(round(years * ppy)))
    blk = max(1, min(int(block) if block else max(5, int(round(ppy / 12.0))), len(rs)))
    step = max(1, horizon // 200)
    v0 = float(start) if start else 10000.0
    nb = len(rs) - blk + 1
    paths_s: List[List[float]] = []
    paths_b: List[List[float]] = []
    ends_s, ends_b, mdd_s, mdd_b = [], [], [], []
    for _ in range(n_paths):
        vs = vb = pks = pkb = v0
        mds = mdb = 0.0
        ps, pb = [v0], [v0]
        k = 0
        while k < horizon:
            j0 = rnd.randrange(nb)
            for t in range(blk):
                if k >= horizon:
                    break
                vs *= 1.0 + rs[j0 + t]
                if vs > pks:
                    pks = vs
                elif pks > 0 and vs / pks - 1.0 < mds:
                    mds = vs / pks - 1.0
                if rb is not None:
                    vb *= 1.0 + rb[j0 + t]
                    if vb > pkb:
                        pkb = vb
                    elif pkb > 0 and vb / pkb - 1.0 < mdb:
                        mdb = vb / pkb - 1.0
                k += 1
                if k % step == 0:
                    ps.append(vs)
                    pb.append(vb)
        paths_s.append(ps)
        paths_b.append(pb)
        ends_s.append(vs)
        ends_b.append(vb)
        mdd_s.append(mds)
        mdd_b.append(mdb)
    L = min(len(p) for p in paths_s)
    qs = (("p5", .05), ("p25", .25), ("p50", .5), ("p75", .75), ("p95", .95))
    bands: Dict[str, List[float]] = {k: [] for k, _ in qs}
    bbands: Dict[str, List[float]] = {"bench_p5": [], "bench_p50": [], "bench_p95": []}
    for k in range(L):
        col = sorted(p[k] for p in paths_s)
        for key, q in qs:
            bands[key].append(round(quantile(col, q), 2))
        if rb is not None:
            colb = sorted(p[k] for p in paths_b)
            for key, q in (("bench_p5", .05), ("bench_p50", .5), ("bench_p95", .95)):
                bbands[key].append(round(quantile(colb, q), 2))
    final = {k: bands[k][-1] for k, _ in qs}
    out: Dict = {"n_paths": n_paths, "horizon_days": horizon, "years": years, "step_days": step,
                 "block_days": blk, "periods_per_year": round(ppy, 2), "start": v0,
                 **bands, "final": final,
                 "prob_loss": _r(sum(1 for v in ends_s if v < v0) / n_paths, 4),
                 "cagr_p50": _r((final["p50"] / v0) ** (1.0 / years) - 1.0),
                 "maxdd_p50": _r(quantile(sorted(mdd_s), 0.5)),
                 "maxdd_p5": _r(quantile(sorted(mdd_s), 0.05))}
    if rb is not None:
        bfin = {k: bbands[k][-1] for k in bbands}
        out.update(bbands)
        out.update({"bench_final": bfin,
                    "bench_prob_loss": _r(sum(1 for v in ends_b if v < v0) / n_paths, 4),
                    "bench_cagr_p50": _r((bfin["bench_p50"] / v0) ** (1.0 / years) - 1.0),
                    "bench_maxdd_p50": _r(quantile(sorted(mdd_b), 0.5)),
                    "bench_maxdd_p5": _r(quantile(sorted(mdd_b), 0.05)),
                    "prob_beat": _r(sum(1 for a, b in zip(ends_s, ends_b) if a > b) / n_paths, 4)})
    return out


# --------------------------------------------------------------------------- #
# Datenqualität
# --------------------------------------------------------------------------- #

# Kursindizes ohne Dividenden -> Total-Return-Alternative bei Yahoo ("" = keine bekannt)
PRICE_INDEX_TR = {"^GSPC": "^SP500TR", "^NDX": "", "^DJI": "", "^STOXX50E": "",
                  "^IXIC": "", "^RUT": "", "^FTSE": "", "^N225": "", "^FCHI": "", "^SSMI": "",
                  "^HSI": "", "^STOXX": ""}


def _pct_de(x: float, nd: int = 1) -> str:
    s = f"{x * 100:+.{nd}f}".replace(".", ",")
    return s + " %"


def _date_de(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def quality_report(s: Series, start: Optional[date] = None, end: Optional[date] = None,
                   ref: Optional[Series] = None, check_age: bool = True,
                   price_index_hint: bool = True, today: Optional[date] = None,
                   max_items: int = 25) -> Dict:
    """Prüft eine Kursreihe auf typische Datenfehler.

    * Sprünge über max(15 %, 10 robuste Sigma) – Krypto: max(25 %, 10 Sigma):
      „Ausreißer" = am Folgetag zurück (Fehlkurs); „Sprung" = ohne Gegenbewegung in
      ruhigem Umfeld (Datenfehler, Split, Währungswechsel). In unruhigem Umfeld oder bei
      gleichgerichteter Bewegung der Referenzreihe gilt er als echte Marktbewegung (Info).
    * Lücken (> 7 Kalendertage, Krypto > 3), eingefrorene Kurse (>= 5 gleiche in Folge),
      Aktualität, Kursindex ohne Dividenden.
    """
    today = today or date.today()
    n = len(s)
    rep: Dict = {"symbol": s.symbol, "name": s.name, "rows": n,
                 "first": s.dates[0].isoformat() if n else None,
                 "last": s.dates[-1].isoformat() if n else None,
                 "issues": [], "level": "ok",
                 "counts": {"jumps": 0, "spikes": 0, "gaps": 0, "stale": 0}}
    issues: List[Dict] = rep["issues"]

    def add(level: str, typ: str, d: Optional[date], text: str) -> None:
        if d is not None and ((start and d < start) or (end and d > end)):
            return
        issues.append({"level": level, "type": typ, "date": d.isoformat() if d else None,
                       "text": text})

    if n < 3:
        add("warn", "Umfang", None, "Weniger als 3 Kurse – keine Auswertung möglich.")
    else:
        vals = [s.value_at(i) for i in range(n)]
        ppy = periods_per_year(s.dates)
        seven = ppy > 300
        rep["periods_per_year"] = round(ppy, 1)
        rep["calendar"] = ("7 Tage je Woche (z. B. Krypto)" if seven else
                           "Börsenhandelstage" if ppy > 150 else "Wochen-/Monatsdaten")
        rets = [vals[i] / vals[i - 1] - 1.0 if vals[i - 1] > 0 else 0.0 for i in range(1, n)]
        med = quantile(sorted(rets), 0.5) or 0.0
        mad = quantile(sorted(abs(r - med) for r in rets), 0.5) or 0.0
        thr = max(0.25 if seven else 0.15, 10.0 * 1.4826 * mad)
        rep["jump_threshold"] = _r(thr, 4)
        ref_ret: Dict[date, float] = {}
        if ref is not None and ref.symbol != s.symbol and len(ref) > 2:
            for i in range(1, len(ref)):
                a = ref.value_at(i - 1)
                if a > 0:
                    ref_ret[ref.dates[i]] = ref.value_at(i) / a - 1.0
        skip = False
        for i in range(1, n):
            r = rets[i - 1]
            if skip:
                skip = False
                continue
            if abs(r) <= thr:
                continue
            d = s.dates[i]
            rr = ref_ret.get(d)
            if rr is not None and r * rr > 0 and abs(rr) >= 0.25 * abs(r):
                add("info", "Marktbewegung", d, f"{_pct_de(r)} am {_date_de(d)} – plausibel "
                    f"(Referenz {ref.symbol}: {_pct_de(rr)}).")
                continue
            back = vals[i + 1] / vals[i - 1] - 1.0 if i + 1 < n and vals[i - 1] > 0 else None
            if back is not None and abs(back) < 0.3 * abs(r):
                rep["counts"]["spikes"] += 1
                skip = True
                add("warn", "Ausreißer", d, f"Kursspitze {_pct_de(r)} am {_date_de(d)}, am "
                    "nächsten Kurstag zurück – vermutlich Fehlkurs.")
                continue
            # Echte Crashs passieren in unruhigen Phasen, Datenfehler (Währungs-/Splitwechsel)
            # meist in ruhigen: Sprung relativ zur Schwankung der 10 Tage davor und danach.
            c = i - 1
            local = [abs(x - med) for x in rets[max(0, c - 10):c] + rets[c + 1:c + 11]]
            lsig = 1.4826 * (quantile(sorted(local), 0.5) or 0.0) if local else 0.0
            if lsig > 0 and abs(r) <= 12.0 * lsig:
                add("info", "Marktbewegung", d, f"{_pct_de(r)} am {_date_de(d)} – starke Bewegung "
                    "in einer Phase hoher Schwankung (vermutlich echt).")
            else:
                rep["counts"]["jumps"] += 1
                add("warn", "Sprung", d, f"Sprung {_pct_de(r)} am {_date_de(d)} ohne "
                    "Gegenbewegung in ruhigem Umfeld – Datenfehler, Split oder Währungswechsel? "
                    "Kursverlauf prüfen, ggf. Zeitraum danach wählen.")
        if ppy > 150:
            gap_thr = 3 if seven else 7
            for i in range(1, n):
                g = (s.dates[i] - s.dates[i - 1]).days
                if g > gap_thr:
                    rep["counts"]["gaps"] += 1
                    add("warn" if g > 14 else "info", "Lücke", s.dates[i],
                        f"{g} Tage ohne Kurs ({_date_de(s.dates[i - 1])} – {_date_de(s.dates[i])}).")
            run = 1
            for i in range(1, n + 1):
                if i < n and vals[i] == vals[i - 1]:
                    run += 1
                    continue
                if run >= 5:
                    rep["counts"]["stale"] += 1
                    k0 = i - run
                    if k0 < 130:                            # erste ~6 Monate nach Auflage
                        add("info", "eingefroren", s.dates[k0],
                            f"{run} gleiche Kurse in Folge ab {_date_de(s.dates[k0])} – typisch "
                            "für die Anfangsphase nach Auflage (wenig Handel).")
                    else:
                        add("warn", "eingefroren", s.dates[k0],
                            f"{run} gleiche Kurse in Folge ab {_date_de(s.dates[k0])} – "
                            "Handelspause oder fehlende Daten.")
                run = 1
        if check_age:
            age = (today - s.dates[-1]).days
            if age > 7:
                issues.append({"level": "info", "type": "Aktualität", "date": s.dates[-1].isoformat(),
                               "text": f"Letzter Kurs vom {_date_de(s.dates[-1])} ({age} Tage alt)."})
    if price_index_hint and s.symbol.upper() in PRICE_INDEX_TR:
        alt = PRICE_INDEX_TR[s.symbol.upper()]
        issues.append({"level": "info", "type": "Kursindex", "date": None,
                       "text": f"{s.symbol} ist ein Kursindex ohne Dividenden – Buy & Hold und "
                               "Strategie werden um die Dividendenrendite (S&P 500 historisch "
                               "ca. 2–3 % p. a.) unterschätzt." +
                               (f" Für Renditevergleiche besser {alt} (Total Return) verwenden."
                                if alt else " Für Renditevergleiche besser einen "
                                            "thesaurierenden ETF verwenden.")})
    warn = [x for x in issues if x["level"] == "warn"]
    info = [x for x in issues if x["level"] != "warn"]
    rep["n_warn"], rep["n_info"] = len(warn), len(info)
    rep["issues"] = (warn + info)[:max_items]
    rep["level"] = "warn" if warn else ("info" if info else "ok")
    return rep


# --------------------------------------------------------------------------- #
# Selbsttest: Rechenregeln und behobene Fehler (ohne Internet und ohne Excel)
# --------------------------------------------------------------------------- #

def _weekdays(start: date, n: int) -> List[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def self_checks() -> List[Dict]:
    """Prüft Invarianten und Regressionsfälle. Liefert [{name, ok, detail}]."""
    res: List[Dict] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        res.append({"name": name, "ok": bool(cond), "detail": detail})

    def safe(name: str, fn) -> None:
        try:
            fn()
        except Exception as e:                                  # noqa: BLE001
            check(name, False, f"Ausnahme {type(e).__name__}: {e}")

    base = {"signal_type": "none", "params": {}, "capital": 10000.0, "fee_fixed": 0.0,
            "fee_pct": 0.0, "slippage_bps": 0.0, "tax_rate": 0.0, "tax_mode": "none",
            "exec_lag": 0, "defensive_is_cash": True, "cash_rate": 0.0}
    cal = [date(2001, 1, 1) + timedelta(days=i) for i in range(61)]
    wave_d = _weekdays(date(2010, 1, 4), 1600)
    wave_p = [100.0 * (1.0002 ** i) * (1.0 + 0.12 * math.sin(i / 55.0)) for i in range(1600)]
    wave = Series("WAVE", wave_d, wave_p, list(wave_p), name="Test-ETF", meta={"type": "ETF"})
    wave_cfg = dict(base, signal_type="ampel", exec_lag=1, fee_fixed=5.9, slippage_bps=5.0,
                    tax_mode="accrued", tax_rate=26.375, cash_rate=2.0,
                    params={"sma_days": 50, "upper": 1.03, "lower": 0.95,
                            "days_red": 5, "days_green": 5})

    def t_bh():
        ds = [date(2000, 1, 3) + timedelta(days=i) for i in range(800)]
        px = [100.0 * (1.0004 ** i) for i in range(800)]
        s = Series("T1", ds, px, list(px))
        got = run_backtest(s, s, base)["equity"]["strategy"][-1] / 10000.0
        exp = px[-1] / px[0]
        check("Buy & Hold ohne Kosten = Kursverhältnis", abs(got - exp) < 2e-6,
              f"{got:.6f} / {exp:.6f}")
    safe("Buy & Hold ohne Kosten", t_bh)

    def t_fee():
        px = [100.0 + i for i in range(31)] + [130.0 - (i + 1) for i in range(30)]
        s = Series("T2", cal, px, list(px))
        r = run_backtest(s, s, dict(base, fee_fixed=5.0, signal_type="momentum",
                                    params={"momentum_days": 1}))
        tr = r["trades"]
        fees = sum(t["fee"] for t in tr)
        exp = tr[0]["units"] * tr[-1]["price"] - 5.0 if tr else 0.0
        end = r["equity"]["strategy"][-1]
        ok = (len(tr) == 2 and tr[0]["action"] == "KAUF" and tr[1]["action"] == "VERKAUF"
              and abs(fees - 10.0) < 1e-9 and abs(end - exp) < 0.05)
        check("Kauf + Verkauf: 2 Orders, 2 × 5 € Gebühr, Endwert exakt", ok,
              f"{len(tr)} Orders, Gebühren {fees:.2f} €, Ende {end:.2f} € (erwartet {exp:.2f} €)")
    safe("Kauf + Verkauf", t_fee)

    def t_tax():
        def day2(price: float, **kw) -> float:
            up = [100.0, price] + [price] * 59
            s = Series("T3", cal, up, list(up))
            cfg = dict(base, tax_mode="accrued", tax_rate=26.375, **kw)
            return run_backtest(s, s, cfg)["equity"]["strategy"][1]
        a = day2(110.0)
        b = day2(110.0, tax_rule="fund_equity")
        c = day2(110.0, tax_allowance=1000.0)
        d_ = day2(130.0, tax_allowance=1000.0)
        check("Steuer anteilig: 1.000 € Gewinn = 263,75 € Steuer (wie Original)",
              abs(a - 10736.25) < 0.011, f"{a:.2f} €")
        check("Teilfreistellung 30 %: 1.000 € Gewinn = 184,63 € Steuer",
              abs(b - 10815.375) < 0.011, f"{b:.2f} €")
        check("Sparerpauschbetrag 1.000 €: steuerfrei bzw. nur Überschuss versteuert",
              abs(c - 11000.0) < 0.011 and abs(d_ - 12472.5) < 0.011, f"{c:.2f} € / {d_:.2f} €")
    safe("Steuer", t_tax)

    def t_ledger():
        lg = TaxLedger(rate=0.26375)
        a = lg.realize(date(2020, 3, 1), 1000.0)
        b = lg.realize(date(2020, 6, 1), -400.0)
        c = lg.realize(date(2021, 2, 1), -500.0)
        d_ = lg.realize(date(2022, 2, 1), 800.0)
        check("Verlusttopf: unterjährige Erstattung und Vortrag ins Folgejahr",
              abs(a - 263.75) < 1e-9 and abs(b + 105.5) < 1e-9 and abs(c) < 1e-9
              and abs(d_ - 79.125) < 1e-9, f"{a:.2f} / {b:.2f} / {c:.2f} / {d_:.3f} €")
        lg23 = TaxLedger(rate23=0.35, exemption23=1000.0)
        e = lg23.realize(date(2021, 5, 1), 999.0, "p23")
        f = lg23.realize(date(2021, 8, 1), 1.0, "p23")
        h1 = taxable_amount(500.0, "private_sale", date(2020, 1, 15), date(2021, 1, 15))[0]
        h2 = taxable_amount(500.0, "private_sale", date(2020, 1, 15), date(2021, 1, 16))[0]
        check("§23: Freigrenze 1.000 € und Haltefrist 1 Jahr",
              e == 0.0 and abs(f - 350.0) < 1e-9 and h1 == 500.0 and h2 == 0.0,
              f"999 € Gewinn: {e:.2f} € Steuer, 1.000 €: {f:.2f} €, Haltefrist {h1:.0f} / {h2:.0f}")
    safe("Steuerbuchhaltung", t_ledger)

    def t_p23_backtest():
        ds = [date(2020, 1, 1) + timedelta(days=i) for i in range(400)]
        px = [100.0 + 0.1 * i for i in range(400)]
        s = Series("BTC-T", ds, px, list(px), name="Bitcoin Test",
                   meta={"type": "CRYPTOCURRENCY"})
        r = run_backtest(s, s, dict(base, tax_mode="accrued", tax_rule="auto",
                                    tax_rate_private=35.0, tax_exemption_private=0.0))
        eq = r["equity"]["strategy"]
        g300 = 10000.0 * px[300] / px[0] - 10000.0
        ok = (r["meta"]["tax"]["risk_rule"] == "private_sale"
              and abs(eq[300] - (10000.0 + 0.65 * g300)) < 0.02
              and abs(eq[-1] - 10000.0 * px[-1] / px[0]) < 0.02)
        check("Krypto: latente Steuer bis 1 Jahr, danach steuerfrei", ok,
              f"Tag 300: {eq[300]:.2f} €, Ende: {eq[-1]:.2f} €")
    safe("Krypto-Steuer", t_p23_backtest)

    def t_fair():
        for mode in ("accrued", "realized", "none"):
            cfg = dict(base, tax_mode=mode, tax_rate=26.375, fee_fixed=5.9, fee_pct=0.25,
                       slippage_bps=5.0, cash_rate=2.0, exec_lag=1, tax_rule="auto",
                       tax_allowance=1000.0)
            r = run_backtest(wave, wave, cfg)
            diff = max(abs(a - b) for a, b in zip(r["equity"]["strategy"],
                                                  r["equity"]["benchmark"]))
            check(f"Benchmark = Strategie bei „immer investiert“ (Steuer: {mode})",
                  diff < 0.011, f"max. Differenz {diff:.4f} €")
    safe("Benchmark-Fairness", t_fair)

    def t_chain():
        r = run_backtest(wave, wave, wave_cfg)
        tot = 1.0 + r["metrics"]["total_return"]
        prod = 1.0
        for y in r["yearly"]:
            prod *= 1.0 + y["strategy"]
        mprod = 1.0
        for row in r["monthly"]["values"]:
            for v in row:
                if v is not None:
                    mprod *= 1.0 + v
        check("Jahres- und Monatsrenditen verkettet = Gesamtrendite (mit Kosten)",
              abs(prod - tot) < 1e-4 and abs(mprod - tot) < 1e-4,
              f"Jahre {prod:.6f} / Monate {mprod:.6f} / gesamt {tot:.6f}")
        lean = run_backtest(wave, wave, wave_cfg, lean=True)["metrics"]
        m = r["metrics"]
        same = all(abs((lean[k] or 0) - (m[k] or 0)) < 2e-3
                   for k in ("cagr", "max_drawdown", "sharpe", "calmar"))
        check("Schnellrechnung (Parameter-Suche) = volle Kennzahlen", same,
              f"CAGR {lean['cagr']} / {m['cagr']}, Sharpe {lean['sharpe']} / {m['sharpe']}")
    safe("Verkettung", t_chain)

    def t_fail_buy():
        px = [100.0 + i for i in range(40)] + [140.0 - i for i in range(40)]
        ds = [date(2001, 1, 1) + timedelta(days=i) for i in range(80)]
        s = Series("T9", ds, px, list(px))
        r = run_backtest(s, s, dict(base, capital=6.0, fee_fixed=5.9, signal_type="momentum",
                                    params={"momentum_days": 1}))
        lens = {len(r["equity"][k]) for k in ("dates", "strategy", "benchmark", "state",
                                              "position")}
        check("Kauf scheitert (Kapital < Gebühr): kein Absturz, Reihen gleich lang",
              len(lens) == 1 and not r["trades"], f"Längen {sorted(lens)}")
    safe("Kauf scheitert", t_fail_buy)

    def t_cash():
        for label, ds in (("7-Tage-Kalender", [date(2020, 1, 1) + timedelta(days=i)
                                               for i in range(1461)]),
                          ("Börsenkalender", _weekdays(date(2020, 1, 1), 1044))):
            sp = [1000.0 - i * 0.5 for i in range(len(ds))]
            risk = Series("R", ds, [100.0] * len(ds), [100.0] * len(ds))
            sig = Series("S", ds, sp, list(sp))
            r = run_backtest(risk, sig, dict(base, signal_type="momentum", exec_lag=1,
                                             params={"momentum_days": 1}, cash_rate=2.0))
            c = r["metrics"]["cagr"]
            check(f"Kassezins 2 % p. a. kalendertaggenau ({label})", abs(c - 0.02) < 2e-4,
                  f"{c * 100:.3f} %")
    safe("Kassezins", t_cash)

    def t_sharpe():
        rnd = random.Random(3)
        ds = _weekdays(date(2012, 1, 2), 1500)
        px = [100.0]
        for _ in range(1499):
            px.append(px[-1] * (1.0 + rnd.gauss(0.0004, 0.011)))
        s = Series("T11", ds, px, list(px))
        m = run_backtest(s, s, dict(base, cash_rate=2.0))["metrics"]
        rets = [px[i] / px[i - 1] - 1.0 for i in range(1, len(px))]
        ppy = periods_per_year(ds)
        rf_p = 1.02 ** (1.0 / ppy) - 1.0
        mu, sd = mean(rets) - rf_p, stdev(rets)
        sh = mu / sd * math.sqrt(ppy)
        so = mu / math.sqrt(sum(min(0.0, x - rf_p) ** 2 for x in rets) / len(rets)) * math.sqrt(ppy)
        check("Sharpe = (Ø Rendite - risikoloser Zins) / Volatilität",
              abs(m["sharpe"] - sh) < 2e-3, f"{m['sharpe']} / {sh:.3f}")
        check("Sortino mit Downside-Abweichung über alle Tage",
              abs(m["sortino"] - so) < 2e-3, f"{m['sortino']} / {so:.3f}")
    safe("Sharpe/Sortino", t_sharpe)

    def t_lag():
        ds = _weekdays(date(2018, 1, 1), 400)
        sig_ds = [d for i, d in enumerate(ds) if i % 9 != 4]
        sp = [100.0 + 10.0 * math.sin(i / 7.0) + 0.02 * i for i in range(len(sig_ds))]
        rp = [100.0 + 0.01 * i for i in range(400)]
        risk, sig = Series("R", ds, rp, list(rp)), Series("S", sig_ds, sp, list(sp))
        cfg = dict(base, signal_type="sma", params={"sma_days": 10, "band": 0.0}, exec_lag=1)
        r = run_backtest(risk, sig, cfg)
        st = compute_signal(sig, "sma", cfg["params"])["states"]
        exp = [st[bisect.bisect_left(sig_ds, pdate(x)) - 1] for x in r["equity"]["dates"]]
        check("Ausführung T+1: Signal vom letzten Signaltag vor dem Handelstag (Feiertage)",
              exp == r["equity"]["state"], f"{sum(1 for a, b in zip(exp, r['equity']['state']) if a != b)} Abweichungen")
    safe("Lag", t_lag)

    def t_warmup():
        r = run_backtest(wave, wave, dict(base, signal_type="sma", exec_lag=1,
                                          params={"sma_days": 200}))
        first = pdate(r["meta"]["start"])
        check("Vorlauf: Backtest startet erst mit gültigem 200-Tage-Signal",
              first > wave_d[199] and r["meta"]["warmup"]["clamped"],
              f"Start {first}, Signal gültig ab {wave_d[199]}")
    safe("Vorlauf", t_warmup)

    def t_ppy():
        p7 = periods_per_year([date(2020, 1, 1) + timedelta(days=i) for i in range(1000)])
        p5 = periods_per_year(_weekdays(date(2020, 1, 1), 1000))
        check("Kurse je Jahr: Krypto ca. 365, Börse ca. 252–261", 364 < p7 < 366.5 and 250 < p5 < 263,
              f"{p7:.1f} / {p5:.1f}")
    safe("Kalender", t_ppy)

    def t_rules():
        cases = [("BTC-EUR", "Bitcoin EUR", "CRYPTOCURRENCY", "private_sale"),
                 ("4GLD.DE", "Xetra-Gold", "EQUITY", "private_sale"),
                 ("BTCE.DE", "Bitwise Physical Bitcoin ETC", "EQUITY", "private_sale"),
                 ("IWDA.AS", "iShares Core MSCI World UCITS ETF USD (Acc)", "ETF", "fund_equity"),
                 ("IUAG.L", "iShares Global Aggregate Bond", "ETF", "securities"),
                 ("SAP.DE", "SAP SE", "EQUITY", "securities"),
                 ("GDX", "VanEck Gold Miners ETF", "ETF", "fund_equity"),
                 ("^GSPC", "S&P 500", "INDEX", "fund_equity")]
        bad = [c[0] for c in cases if classify_tax_rule(c[0], c[1], c[2]) != c[3]]
        check("Steuerregel automatisch (Krypto/Gold §23, Aktien-ETF 30 %, Anleihe/Aktie 0 %)",
              not bad, "falsch: " + ", ".join(bad) if bad else f"{len(cases)} Fälle")
    safe("Steuerregeln", t_rules)

    def t_wf():
        ds = _weekdays(date(2010, 1, 4), 1300)
        px = [100.0 * (1.0003 ** i) * (1.0 + 0.10 * math.sin(i / 45.0)) for i in range(1300)]
        s = Series("T16", ds, px, list(px))
        wf = walk_forward(s, s, dict(base, signal_type="ampel", exec_lag=1), None,
                          train_years=2, test_years=1)
        ok = wf.get("combos") == len(param_grid("ampel")) == 144 and wf.get("oos") is not None \
            and len(wf.get("windows") or []) >= 1
        check("Walk-Forward: alle 144 Kombinationen, durchgehende OOS-Kurve", ok,
              f"{wf.get('combos')} Kombinationen, {len(wf.get('windows') or [])} Fenster")
    safe("Walk-Forward", t_wf)

    def t_mc():
        eq = [10000.0 * (1.0003 ** i) * (1.0 + 0.05 * math.sin(i / 30.0)) for i in range(800)]
        a = monte_carlo(eq, eq, ppy=252.0, years=2.0, n_paths=60, seed=1)
        b = monte_carlo(eq, eq, ppy=252.0, years=2.0, n_paths=60, seed=1)
        ordered = all(x <= y <= z for x, y, z in zip(a["p5"], a["p50"], a["p95"]))
        check("Monte-Carlo reproduzierbar, Quantile geordnet",
              a["final"] == b["final"] and ordered and a["prob_beat"] == 0.0,
              f"Median {a['final']['p50']:.0f} €")
    safe("Monte-Carlo", t_mc)

    def t_quality():
        ds = _weekdays(date(2015, 1, 1), 300)
        px = [100.0 * (1.0002 ** i) * (1.0 + 0.01 * math.sin(i)) for i in range(300)]
        px[100:] = [v * 0.7 for v in px[100:]]
        px[200] = px[199] * 1.5
        for k in range(250, 257):
            px[k] = px[249]
        q = quality_report(Series("Q", ds, px, list(px)), check_age=False)
        c = q["counts"]
        check("Datenqualität erkennt Sprung, Ausreißer und eingefrorene Kurse",
              c["jumps"] == 1 and c["spikes"] == 1 and c["stale"] == 1, str(c))
    safe("Datenqualität", t_quality)
    return res


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
    epoch = date(1970, 1, 1)
    p1 = (start - epoch).days * 86400 if start else 0
    p2 = ((end - epoch).days + 1) * 86400 if end else int(time.time()) + 86400
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
    # Zeitstempel = Handelsbeginn in UTC; mit dem Börsen-Offset ergibt sich der lokale
    # Handelstag (wichtig z. B. für Asien/Australien). Doppelte Tage: letzter Wert gilt.
    off = int(meta.get("gmtoffset") or 0)
    by_day: Dict[date, Tuple[float, Optional[float]]] = {}
    for i, t in enumerate(ts):
        if t is None:
            continue
        d = (datetime(1970, 1, 1) + timedelta(seconds=int(t) + off)).date()
        c = fnum(closes[i]) if i < len(closes) else None
        a = fnum(adj[i]) if i < len(adj) else None
        if c is None or c <= 0:
            continue
        by_day[d] = (c, a if a is not None and a > 0 else None)
    if not by_day:
        raise RuntimeError(f"Keine gültigen Kurse für {symbol}")
    days = sorted(by_day)
    series = Series(symbol=symbol, name=meta.get("longName") or meta.get("shortName") or symbol,
                    currency=meta.get("currency") or "",
                    source="yahoo", meta={"exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or "",
                                          "type": meta.get("instrumentType") or "",
                                          "timezone": meta.get("exchangeTimezoneName") or "",
                                          "fetched": datetime.now().isoformat(timespec="seconds")},
                    dates=days, close=[by_day[d][0] for d in days],
                    adj=[by_day[d][1] if by_day[d][1] is not None else by_day[d][0] for d in days])
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


def _detect_dayfirst(samples: List[str]) -> Optional[bool]:
    """TT/MM vs. MM/TT einer Datumsspalte anhand der Werte > 12 (None = nicht entscheidbar)."""
    dmy = mdy = 0
    for raw in samples:
        t = (raw or "").strip().strip('"').strip("'").split("T")[0].split(" ")[0]
        m = re.fullmatch(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})", t)
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12 >= b:
            dmy += 1
        elif b > 12 >= a:
            mdy += 1
    if dmy > mdy:
        return True
    if mdy > dmy:
        return False
    return None


def _detect_decimal(samples: List[str]) -> Optional[str]:
    """Dezimaltrennzeichen einer Zahlenspalte ("," oder "."; None = je Wert entscheiden).

    Eindeutig sind Werte mit beiden Zeichen oder mit 1-2/4+ Nachkommastellen; "1.234" allein
    ist mehrdeutig und zählt nicht.
    """
    comma = dot = 0
    for raw in samples:
        t = re.sub(r"[^\d,.\-]", "", raw or "")
        if not t:
            continue
        lc, ld = t.rfind(","), t.rfind(".")
        if lc > ld and (ld >= 0 or len(t) - lc - 1 != 3):
            comma += 1
        elif ld > lc and (lc >= 0 or len(t) - ld - 1 != 3):
            dot += 1
    if comma > dot:
        return ","
    if dot > comma:
        return "."
    return None


def _parse_pairs(raw: List[Tuple[str, str]]):
    """Datum/Kurs-Paare einheitlich je Spalte auswerten (kein Mischen von TT/MM und MM/TT)."""
    dayfirst = _detect_dayfirst([d for d, _ in raw])
    dec = _detect_decimal([p for _, p in raw])
    rows: List[Tuple[date, float]] = []
    for ds, ps in raw:
        d = pdate(ds, dayfirst=dayfirst)
        v = fnum(ps, decimal=dec)
        if d and v and v > 0:
            rows.append((d, v))
    return rows, dayfirst, dec


def parse_csv(text: str, symbol: str = "", name: str = "") -> Series:
    """Flexibler CSV-Parser: erkennt Trennzeichen, Kopfzeilen, Datums- und Kurs-Spalte,
    Datumsformat (TT/MM oder MM/TT) und Dezimaltrennzeichen je Datei."""
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
    dayfirst, dec = None, None
    if date_col is not None:
        raw = []
        for line in lines[header_idx + 1:]:
            cells = _split(line, delim)
            if len(cells) > max(date_col, price_col):
                raw.append((cells[date_col], cells[price_col]))
        rows, dayfirst, dec = _parse_pairs(raw)
    if len(rows) < 10:
        # Fallback: erste Spalte = Datum, erste sinnvolle Zahlspalte = Kurs
        raw = []
        for line in lines:
            cells = _split(line, delim)
            if len(cells) < 2 or not pdate(cells[0]):
                continue
            for c in cells[1:]:
                v = fnum(c)
                if v and v > 0:
                    raw.append((cells[0], c))
                    break
        rows, dayfirst, dec = _parse_pairs(raw)
    if len(rows) < 10:
        raise ValueError("Konnte im CSV keine Kursreihe erkennen (Datum + Kurs erwartet).")
    sym = symbol or (name or "CSV").upper().replace(" ", "_")[:24]
    fmt = {True: "TT/MM/JJJJ", False: "MM/TT/JJJJ", None: "eindeutig"}[dayfirst]
    series = make_series(sym, rows, name=name or sym, currency="", source="csv",
                         meta={"fetched": datetime.now().isoformat(timespec="seconds"),
                               "csv_date_order": fmt, "csv_decimal": dec or "auto"})
    return series


# --------------------------------------------------------------------------- #
# Synthetische Hebelreihe (fuer den Original-Vergleich)
# --------------------------------------------------------------------------- #

def synthetic_leverage(base: Series, factor: float, rf_pct: float = 0.0,
                       spread_pct: float = 0.0, name: str = "") -> Series:
    """Bildet eine täglich gehebelte Long-/Short-Reihe aus Tagesrenditen nach:

        r_L = L·r + (1 − L)·rf·Δt − |L|·Kosten·Δt        (Δt in Kalenderjahren)

    Long-Hebel (L > 1) zahlt Finanzierung auf den geliehenen Teil (L − 1); Short-Produkte
    (L < 0) erhalten Zinsen auf Sicherheit und Leerverkaufserlös (1 − L). Laufende Kosten
    (Swap/TER) skalieren mit dem Exposure |L|. Ohne Zins/Kosten entspricht das der Logik
    aus 'db_x-trackers' (Faktor 1 / 2 / -1 / -2 auf den DAX).
    """
    if not base.dates:
        raise ValueError("Basisserie ist leer.")
    rf, cost = rf_pct / 100.0, spread_pct / 100.0
    vals = [100.0]
    for i in range(1, len(base)):
        prev = base.value_at(i - 1)
        r = base.value_at(i) / prev - 1.0 if prev else 0.0
        dt = (base.dates[i] - base.dates[i - 1]).days / 365.0
        r_l = factor * r + (1.0 - factor) * rf * dt - abs(factor) * cost * dt
        vals.append(max(vals[-1] * (1.0 + r_l), 1e-6))
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
    try:
        q = quality_report(s, check_age=False)
        qsum = {"level": q["level"], "n_warn": q["n_warn"], "n_info": q["n_info"]}
    except Exception:                                           # noqa: BLE001
        qsum = None
    idx = load_index()
    idx.setdefault("instruments", {})[s.symbol] = {
        "symbol": s.symbol, "name": s.name, "currency": s.currency, "source": s.source,
        "rows": len(s), "first": s.dates[0].isoformat() if s.dates else None,
        "last": s.dates[-1].isoformat() if s.dates else None,
        "meta": s.meta, "quality": qsum,
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    save_index(idx)


def update_quality(symbol: str, rep: Dict) -> None:
    """Qualitäts-Kurzinfo im Verzeichnis nach einer manuellen Prüfung aktualisieren."""
    idx = load_index()
    entry = idx.get("instruments", {}).get(symbol)
    if entry is None:
        return
    entry["quality"] = {"level": rep.get("level"), "n_warn": rep.get("n_warn", 0),
                        "n_info": rep.get("n_info", 0)}
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
    """Verzeichnis der geladenen Reihen; fehlende Qualitätsinfos (Bestände aus Version 1.0)
    werden einmalig nachgetragen."""
    idx = load_index()
    inst = idx.get("instruments", {})
    changed = False
    for sym, entry in inst.items():
        if "quality" in entry:
            continue
        s = load_series(sym)
        try:
            q = quality_report(s, check_age=False) if s is not None and len(s) else None
            entry["quality"] = ({"level": q["level"], "n_warn": q["n_warn"], "n_info": q["n_info"]}
                                if q else None)
        except Exception:                                       # noqa: BLE001
            entry["quality"] = None
        changed = True
    if changed:
        try:
            save_index(idx)
        except Exception:                                       # noqa: BLE001
            pass
    out = list(inst.values())
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
    {"symbol": "^SP500TR", "name": "S&P 500 Total Return (mit Dividenden, USD, ab 1988)", "group": "Index", "type": "Index"},
    {"symbol": "URTH", "name": "iShares MSCI World ETF (USD)", "group": "Welt-ETF", "type": "ETF"},
    {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust (USD)", "group": "USA", "type": "ETF"},
    {"symbol": "BTC-EUR", "name": "Bitcoin in Euro (ab 2014, 7 Tage/Woche)", "group": "Krypto", "type": "Krypto"},
    {"symbol": "BTC-USD", "name": "Bitcoin in US-Dollar (ab 2014)", "group": "Krypto", "type": "Krypto"},
    {"symbol": "ETH-EUR", "name": "Ethereum in Euro (ab 2017)", "group": "Krypto", "type": "Krypto"},
    {"symbol": "BTCE.DE", "name": "Bitwise Physical Bitcoin ETC (Xetra, EUR, ab 2020)", "group": "Krypto", "type": "ETC"},
    {"symbol": "VBTC.DE", "name": "VanEck Bitcoin ETN (Xetra, EUR, ab 2020)", "group": "Krypto", "type": "ETN"},
    {"symbol": "IBIT", "name": "iShares Bitcoin Trust ETF (USD, ab 2024)", "group": "Krypto", "type": "ETF"},
]


# --------------------------------------------------------------------------- #
# Selbsttest der Datenschicht (ohne Internet)
# --------------------------------------------------------------------------- #

def data_self_checks() -> List[Dict]:
    res: List[Dict] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        res.append({"name": name, "ok": bool(cond), "detail": detail})

    try:
        d0 = date(2015, 1, 1)
        days = [d0 + timedelta(days=i) for i in range(60)]
        s1 = parse_csv("\n".join(["Datum;Schluss"] + [f"{d.strftime('%d/%m/%Y')};{100 + i},50"
                                                      for i, d in enumerate(days)]), symbol="A")
        s2 = parse_csv("\n".join(["Date,Close"] + [f"{d.strftime('%m/%d/%Y')},{1000 + i}.25"
                                                   for i, d in enumerate(days)]), symbol="B")
        s3 = parse_csv("\n".join(["Datum;Kurs"] + [f"{d.strftime('%d.%m.%Y')};"
                                                   f"{'1.234' if i % 2 else '1.234,50'}"
                                                   for i, d in enumerate(days[:30])]), symbol="C")
        ok = (s1.dates == days and abs(s1.close[0] - 100.5) < 1e-9 and s2.dates == days
              and abs(s2.close[0] - 1000.25) < 1e-9 and all(1233.9 < v < 1234.6 for v in s3.close))
        check("CSV: TT/MM/JJJJ, MM/TT/JJJJ und Tausenderpunkte je Datei korrekt", ok,
              f"{s1.dates[0]} … {s1.dates[-1]} / {s2.dates[0]} / {s3.close[:2]}")
    except Exception as e:                                      # noqa: BLE001
        check("CSV-Import", False, f"Ausnahme {type(e).__name__}: {e}")
    try:
        ds = [date(2020, 1, 1) + timedelta(days=i) for i in range(366)]
        flat = Series("B", ds, [100.0] * 366, [100.0] * 366)
        g_short = synthetic_leverage(flat, -1.0, rf_pct=2.0, spread_pct=0.3).close[-1] / 100.0 - 1.0
        g_long = synthetic_leverage(flat, 2.0, rf_pct=2.0, spread_pct=0.3).close[-1] / 100.0 - 1.0
        check("Synthetischer Hebel: Short erhält Zinsen, Long zahlt Finanzierung",
              0.036 < g_short < 0.039 and -0.027 < g_long < -0.025,
              f"-1x: {g_short * 100:+.2f} % p. a., 2x: {g_long * 100:+.2f} % p. a. (Index unverändert)")
    except Exception as e:                                      # noqa: BLE001
        check("Synthetischer Hebel", False, f"Ausnahme {type(e).__name__}: {e}")
    return res


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



VERSION = "1.1"
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

# Kosten/Steuer für einen deutschen Privatanleger (Wertpapierdepot)
COSTS_DE = {"exec_lag": 1, "capital": 10000.0, "fee_fixed": 5.9, "fee_pct": 0.0,
            "slippage_bps": 5.0, "tax_mode": "accrued", "tax_rate": 26.375,
            "tax_rule": "auto", "tax_allowance": 1000.0, "tax_rate_private": 35.0,
            "tax_exemption_private": 1000.0, "tax_interest": True, "loss_offset": True,
            "cash_rate": 2.0}
# Krypto-Börse/Broker: prozentuale Gebühr, höhere Spreads
COSTS_CRYPTO = dict(COSTS_DE, fee_fixed=0.0, fee_pct=0.25, slippage_bps=15.0)
AMPEL_ORIGINAL = {"sma_days": 130, "upper": 1.05, "lower": 0.92, "days_red": 19,
                  "days_green": 16}

PRESETS = [
    {
        "id": "ampel-world",
        "name": "RSL-Ampel · MSCI World (Kasse)",
        "hint": "Original-Logik (130 Tage, 1,05/0,92, 19/16) auf einen weltweiten ETF, "
                "Signal aus dem S&P 500. Defensiv = Kasse.",
        "config": dict(COSTS_DE, risk="IWDA.AS", signal="^GSPC", defensive_is_cash=True,
                       signal_type="ampel", params=dict(AMPEL_ORIGINAL)),
    },
    {
        "id": "ampel-dax",
        "name": "RSL-Ampel · DAX-ETF (Kasse)",
        "hint": "Gleiche Ampel-Logik, Signal direkt aus dem DAX, Risiko im DAX-ETF.",
        "config": dict(COSTS_DE, risk="EXS1.DE", signal="^GDAXI", defensive_is_cash=True,
                       signal_type="ampel", params=dict(AMPEL_ORIGINAL)),
    },
    {
        "id": "sma200-world",
        "name": "200-Tage-SMA · MSCI World (Kasse)",
        "hint": "Der Klassiker: investiert, solange der ETF über seinem 200-Tage-Schnitt "
                "liegt, sonst Kasse.",
        "config": dict(COSTS_DE, risk="IWDA.AS", signal="IWDA.AS", defensive_is_cash=True,
                       signal_type="sma", params={"sma_days": 200, "band": 0.0}),
    },
    {
        "id": "goldencross-sp500",
        "name": "Golden Cross 50/200 · S&P-500-ETF",
        "hint": "50-Tage-Schnitt über 200-Tage-Schnitt. Start 09/2011: Die Yahoo-Reihe von "
                "SXR8.DE hat am 01.11.2010 einen Datenfehler (−24,6 %), der sonst Signal und "
                "Buy & Hold verfälscht.",
        "config": dict(COSTS_DE, risk="SXR8.DE", signal="SXR8.DE", defensive_is_cash=True,
                       signal_type="dual_sma", params={"sma_short": 50, "sma_long": 200},
                       start="2011-09-01"),
    },
    {
        "id": "momentum-world",
        "name": "Momentum 12 Monate · MSCI World",
        "hint": "Investiert, wenn die 12-Monats-Rendite positiv ist.",
        "config": dict(COSTS_DE, risk="IWDA.AS", signal="IWDA.AS", defensive_is_cash=True,
                       signal_type="momentum", params={"momentum_days": 252}),
    },
    {
        "id": "btc-sma200",
        "name": "Bitcoin · 200-Tage-SMA (Kasse)",
        "hint": "Bitcoin in Euro, investiert über dem 200-Tage-Schnitt (7 Handelstage je Woche). "
                "Krypto-Kosten 0,25 % + 0,15 % Spread; Steuer §23: steuerfrei nach 1 Jahr "
                "Haltedauer, sonst persönlicher Steuersatz.",
        "config": dict(COSTS_CRYPTO, risk="BTC-EUR", signal="BTC-EUR", defensive_is_cash=True,
                       signal_type="sma", params={"sma_days": 200, "band": 0.0}),
    },
    {
        "id": "btc-momentum",
        "name": "Bitcoin · Momentum 365 Tage (Kasse)",
        "hint": "Bitcoin in Euro, investiert bei positiver 1-Jahres-Rendite (365 Kalendertage). "
                "Wenige Wechsel – Gewinne bleiben öfter länger als 1 Jahr liegen (§23).",
        "config": dict(COSTS_CRYPTO, risk="BTC-EUR", signal="BTC-EUR", defensive_is_cash=True,
                       signal_type="momentum", params={"momentum_days": 365}),
    },
    {
        "id": "ampel-sp500-long",
        "name": "RSL-Ampel · S&P 500 seit 1970 (Krisen-Test)",
        "hint": "Lange Historie: zeigt, wie die Ampel die großen Bärenmärkte 1973/74, 2000-02 "
                "und 2008 vermeidet – auf Kosten von Rendite in starken Aufwärtsphasen. "
                "^GSPC ist ein Kursindex ohne Dividenden; mit Dividenden: ^SP500TR (ab 1988).",
        "config": dict(COSTS_DE, risk="^GSPC", signal="^GSPC", defensive_is_cash=True,
                       signal_type="ampel", params=dict(AMPEL_ORIGINAL)),
    },
    {
        "id": "original-comparison",
        "name": "Original-Nachbau (Vergleich): 2x / −2x DAX monatlich",
        "hint": "Die Strategie aus ETF_Simulation.xlsm: LevDAX bei grün, ShortDAX (x2) bei rot, "
                "monatliche Zuordnung. Hebel synthetisch aus dem DAX (Finanzierung 2 % p. a., "
                "Kosten 0,3 % je Hebelfaktor). Steuer wie im Original (ohne Teilfreistellung "
                "und Pauschbetrag).",
        "config": dict(COSTS_DE, risk="^GDAXI@2", signal="^GSPC", defensive_is_cash=False,
                       signal_type="ampel", params=dict(AMPEL_ORIGINAL),
                       rotation=ORIGINAL_ROTATION, exec_lag=0, slippage_bps=0.0,
                       cash_rate=0.0, tax_rule="securities", tax_allowance=0.0,
                       tax_interest=False, start="2000-01-01", end="2015-12-23"),
    },
]

SIGNAL_TYPES = [{"id": k, "name": v} for k, v in E.SIGNAL_INFO.items()]

DEFAULTS = dict(COSTS_DE, **{
    "risk": "IWDA.AS", "signal": "^GSPC", "defensive": None, "defensive_is_cash": True,
    "signal_type": "ampel",
    "params": {"sma_days": 130, "upper": 1.05, "lower": 0.92, "days_red": 19,
               "days_green": 16, "band": 0.0, "sma_short": 50, "sma_long": 200,
               "momentum_days": 252},
    "start": None, "end": None, "rotation": None, "synth_rf": 2.0, "synth_cost": 0.3,
})

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
        rf = float((cfg or {}).get("synth_rf", 2.0) or 0.0)
        cost = float((cfg or {}).get("synth_cost", 0.3) or 0.0)
        key = f"{base}@{factor}@{rf}@{cost}"
        with _lock:
            if key in _synth_cache:
                return _synth_cache[key]
        b = get_series(base, cfg, auto_download)
        s = D.synthetic_leverage(b, factor, rf_pct=rf, spread_pct=cost)
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
            except Exception:                                   # noqa: BLE001
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


def load_instruments(cfg: Dict):
    """Risiko-, Signal-, Defensiv-Reihe und alle Instrumente einer Monatsrotation laden."""
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
                    except Exception:                           # noqa: BLE001
                        pass
    return risk, sig, defensive, instruments


def quality_for(cfg: Dict, risk: E.Series, sig: E.Series, defensive: Optional[E.Series],
                result: Dict) -> List[Dict]:
    """Datenqualität der verwendeten Reihen im Analysezeitraum (Signal inkl. Vorlauf)."""
    start, end = E.pdate(result["meta"]["start"]), E.pdate(result["meta"]["end"])
    p = cfg.get("params") or {}
    look = {"ampel": p.get("sma_days", 130), "sma": p.get("sma_days", 200),
            "dual_sma": p.get("sma_long", 200), "momentum": p.get("momentum_days", 252)}
    lb_days = int(float(look.get(cfg.get("signal_type"), 0) or 0) * 1.5) + 10
    out: List[Dict] = []
    seen = set()

    def add(s: Optional[E.Series], role: str, ref: Optional[E.Series], hint: bool,
            q_start: Optional[date]) -> None:
        if s is None or s.symbol in seen or s.source == "synthetic":
            return
        seen.add(s.symbol)
        rep = E.quality_report(s, start=q_start, end=end, ref=ref, check_age=False,
                               price_index_hint=hint)
        rep["role"] = role
        out.append(rep)

    add(risk, "Risiko-Anlage", sig if sig.symbol != risk.symbol else None, True, start)
    add(sig, "Signalgeber", None, False, start - timedelta(days=lb_days) if start else None)
    add(defensive, "Defensiv-Anlage", None, True, start)
    if sig.dates and end and sig.dates[-1] < end - timedelta(days=5):
        for rep in out:
            if rep["symbol"] == sig.symbol:
                rep["issues"].insert(0, {"level": "warn", "type": "Signalende", "date": sig.dates[-1].isoformat(),
                                         "text": f"Signaldaten enden am {sig.dates[-1].strftime('%d.%m.%Y')} – "
                                                 "danach bleibt das Signal eingefroren."})
                rep["n_warn"] += 1
                rep["level"] = "warn"
    return out


def run_config(cfg: Dict, with_mc: bool = False) -> Dict:
    cfg = normalize_config(cfg)
    risk, sig, defensive, instruments = load_instruments(cfg)
    result = E.run_backtest(risk, sig, cfg, defensive, instruments)
    meta = result["meta"]
    meta["risk_name"] = risk.name
    meta["signal_name"] = sig.name
    meta["risk_currency"] = risk.currency
    meta["signal_currency"] = sig.currency
    meta["risk_first"] = risk.dates[0].isoformat() if risk.dates else None
    meta["risk_last"] = risk.dates[-1].isoformat() if risk.dates else None
    meta["config"] = cfg
    try:
        result["quality"] = quality_for(cfg, risk, sig, defensive, result)
    except Exception:                                           # noqa: BLE001
        traceback.print_exc()
        result["quality"] = []
    if with_mc:
        eq = result["equity"]
        result["montecarlo"] = E.monte_carlo(eq["strategy"], eq["benchmark"],
                                             ppy=meta.get("periods_per_year", 252.0),
                                             years=10.0, start=float(cfg.get("capital") or 10000.0))
    return result


def b64_encode(text: str) -> str:
    import base64
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _de(v, nd: int = 2) -> str:
    """Zahl im deutschen Format für den CSV-Export (Dezimalkomma)."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "ja" if v else "nein"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return f"{v:.{nd}f}".replace(".", ",")
    return str(v)


def results_csv(res: Dict) -> str:
    """Semikolon-CSV mit Dezimalkomma (öffnet sich in Excel/LibreOffice deutsch direkt)."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    eq = res["equity"]
    w.writerow(["Datum", "Strategie", "Benchmark", "Drawdown", "Drawdown Benchmark", "Signal",
                "Position"])
    for i, d in enumerate(eq["dates"]):
        w.writerow([d, _de(eq["strategy"][i]), _de(eq["benchmark"][i]), _de(eq["drawdown"][i], 6),
                    _de(eq["bench_drawdown"][i], 6), eq["state"][i], eq["position"][i]])
    w.writerow([])
    w.writerow(["Kennzahl", "Wert"])
    for k, v in res["metrics"].items():
        w.writerow([k, _de(v, 6) if isinstance(v, float) else _de(v)])
    w.writerow([])
    w.writerow(["Jahr", "Strategie", "Benchmark", "Endstand", "Endstand Benchmark", "Teiljahr"])
    for y in res["yearly"]:
        w.writerow([y["year"], _de(y["strategy"], 6), _de(y["benchmark"], 6), _de(y["end"]),
                    _de(y.get("bench_end")), _de(bool(y.get("partial")))])
    w.writerow([])
    w.writerow(["Trades"])
    w.writerow(["Datum", "Aktion", "Instrument", "Kurs", "Stueck", "Wert", "Gebuehr",
                "Steuer", "Gewinn", "Haltedauer Tage"])
    for t in res["trades"]:
        w.writerow([t["date"], t["action"], t["symbol"], _de(t["price"], 4), _de(t["units"], 6),
                    _de(t["value"]), _de(t["fee"]), _de(t["tax"]), _de(t["gain"]),
                    _de(t.get("holding_days"))])
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
        except Exception:                                       # noqa: BLE001
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
                    "tax_rules": E.TAX_RULE_CHOICES,
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
            if u.path == "/api/quality":
                sym = (q.get("symbol") or [""])[0]
                if not sym:
                    self._err("symbol fehlt")
                    return
                s = D.load_series(sym)
                if s is None:
                    self._err(f"{sym} ist nicht im Datenbestand.", 404)
                    return
                ref_sym = (q.get("ref") or [""])[0]
                ref = D.load_series(ref_sym) if ref_sym else None
                rep = E.quality_report(s, ref=ref)
                if not ref_sym:
                    try:
                        D.update_quality(sym, E.quality_report(s, check_age=False))
                    except Exception:                           # noqa: BLE001
                        pass
                self._json({"ok": True, "report": rep})
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
                                "date_order": s.meta.get("csv_date_order"),
                                "decimal": s.meta.get("csv_decimal"),
                                "quality": E.quality_report(s, check_age=False),
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
                risk, sig, defen, instruments = load_instruments(cfg)
                objective = body.get("objective") or "calmar"
                rows = E.run_grid(risk, sig, cfg, defen,
                                  max_runs=int(body.get("max_runs") or 500),
                                  instruments=instruments, objective=objective)
                self._json({"ok": True, "rows": rows, "signal_type": cfg["signal_type"],
                            "objective": objective})
                return

            if u.path == "/api/walkforward":
                cfg = normalize_config(body.get("config") or {})
                risk, sig, defen, instruments = load_instruments(cfg)
                wf = E.walk_forward(risk, sig, cfg, defen,
                                    int(body.get("train_years") or 6),
                                    int(body.get("test_years") or 2),
                                    body.get("objective") or "calmar",
                                    instruments=instruments)
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
                        row = {"symbol": sym, "name": r["meta"].get("risk_name"), "ok": True,
                               "start": m.get("start"), "end": m.get("end"),
                               "tax_rule": r["meta"]["tax"]["risk_rule_label"],
                               "quality": max((q["level"] for q in r.get("quality") or []),
                                              key=lambda x: {"ok": 0, "info": 1, "warn": 2}[x],
                                              default="ok")}
                        for k in ("cagr", "bench_cagr", "max_drawdown", "bench_max_drawdown",
                                  "sharpe", "bench_sharpe", "calmar", "bench_calmar", "martin",
                                  "volatility", "up_capture", "down_capture", "end_capital",
                                  "time_in_market"):
                            row[k] = m.get(k)
                        rows.append(row)
                    except Exception as e:                      # noqa: BLE001
                        rows.append({"symbol": sym, "ok": False, "error": str(e)})
                self._json({"ok": True, "rows": rows})
                return

            if u.path == "/api/export":
                cfg = body.get("config") or {}
                res = run_config(cfg)
                text = results_csv(res)
                fname = (f"etf_timing_{(cfg.get('risk') or 'risiko').replace('^', '').replace('@', '_')}"
                         f"_{datetime.now().strftime('%Y%m%d_%H%M')}.csv")
                self._json({"ok": True, "filename": fname,
                            "content": b64_encode("\ufeff" + text),
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
    """Prüft Rechenregeln und behobene Fehler – ohne Internet, ohne Excel."""
    print(f"{APP_NAME} {VERSION} -- Selbsttest (ohne Internet und ohne Excel)")
    t0 = time.time()
    res = E.self_checks() + D.data_self_checks()
    for r in res:
        mark = "ok    " if r["ok"] else "FEHLER"
        print(f"  [{mark}] {r['name']}" + (f"  ({r['detail']})" if r["detail"] else ""))
    n_ok = sum(1 for r in res if r["ok"])
    print(f"  Ergebnis: {n_ok} von {len(res)} Prüfungen bestanden ({time.time() - t0:.1f} s).")
    return 0 if n_ok == len(res) else 1


def _print_quality(rep: Dict) -> None:
    print(f"{rep['symbol']}: {rep.get('name', '')} – {rep['rows']} Kurse "
          f"{rep.get('first')} .. {rep.get('last')} · {rep.get('calendar', '')} · Stufe {rep['level']}")
    for it in rep["issues"]:
        print(f"   [{it['level']:4s}] {it['type']}: {it['text']}")


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):                    # Konsole ohne UTF-8: nicht abstürzen
        try:
            stream.reconfigure(errors="replace")
        except Exception:                                       # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description=f"{APP_NAME} -- Timing-Analyse für Aktien, ETFs und Krypto")
    ap.add_argument("--port", type=int, default=8765, help="Port (Standard 8765)")
    ap.add_argument("--no-browser", action="store_true", help="Browser nicht öffnen")
    ap.add_argument("--selftest", action="store_true", help="Rechenregeln prüfen und beenden")
    ap.add_argument("--download", nargs="+", metavar="SYMBOL",
                    help="Symbole herunterladen und beenden")
    ap.add_argument("--start", help="Startdatum (JJJJ-MM-TT) für --download")
    ap.add_argument("--quality", nargs="+", metavar="SYMBOL",
                    help="Datenqualität geladener Reihen prüfen und beenden")
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

    if args.quality:
        for sym in args.quality:
            s = D.load_series(sym)
            if s is None:
                print(f"{sym}: nicht im Datenbestand (erst mit --download laden)")
                continue
            _print_quality(E.quality_report(s))
        return 0

    if args.backtest:
        try:
            cfg = json.loads(args.backtest)
        except json.JSONDecodeError as e:
            print("Ungültiges JSON:", e)
            return 2
        res = run_config(cfg)
        print(json.dumps(res["metrics"], indent=2, ensure_ascii=False))
        for rep in res.get("quality") or []:
            if rep["level"] == "warn":
                _print_quality(rep)
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
