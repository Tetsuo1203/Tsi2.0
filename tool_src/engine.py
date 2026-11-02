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

from __future__ import annotations

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
