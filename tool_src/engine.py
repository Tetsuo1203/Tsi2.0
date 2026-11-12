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

from __future__ import annotations

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
