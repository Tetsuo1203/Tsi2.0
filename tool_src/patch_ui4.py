"""Vierte Runde: Parameter-Tabelle lesbar machen."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "tool_src", "ui", "app.js")

with open(P, "r", encoding="utf-8") as f:
    s = f.read()

OLD_FN = '''  function renderGrid() {'''
NEW_FN = '''  const PARAM_LABELS = {
    sma_days: "RSL-Tage", upper: "Obergrenze", lower: "Untergrenze",
    days_red: "Wechseltage rot", days_green: "Wechseltage grün",
    band: "Hysterese", sma_short: "kurzer SMA", sma_long: "langer SMA",
    momentum_days: "Momentum-Tage",
  };
  const PARAM_ORDER = {
    ampel: ["sma_days", "upper", "lower", "days_red", "days_green"],
    sma: ["sma_days", "band"],
    dual_sma: ["sma_short", "sma_long"],
    momentum: ["momentum_days"],
    none: [],
  };

  function paramText(p, type) {
    const keys = PARAM_ORDER[type] || Object.keys(p);
    return keys.filter(k => p[k] !== undefined && p[k] !== null)
      .map(k => `${PARAM_LABELS[k] || k} ${typeof p[k] === "number" ? num(p[k], 2) : p[k]}`)
      .join(" · ");
  }

  function renderGrid() {'''

assert s.count(OLD_FN) == 1
s = s.replace(OLD_FN, NEW_FN)

OLD_HEAD = '''    $("\\#gridSummary").innerHTML = `Beste Variante: <b>${JSON.stringify(best.params)}</b> – ` +'''
# Hinweis: In der Datei steht $("#gridSummary") - deshalb ohne Escapes suchen
OLD_HEAD = '''    $("#gridSummary").innerHTML = `Beste Variante: <b>${JSON.stringify(best.params)}</b> – ` +'''
NEW_HEAD = '''    const stype = (S.boot && getVal("signalType")) || "ampel";
    $("#gridSummary").innerHTML = `Beste Variante: <b>${paramText(best.params, stype)}</b> – ` +'''
assert s.count(OLD_HEAD) == 1, s.count(OLD_HEAD)
s = s.replace(OLD_HEAD, NEW_HEAD)

OLD_ROW = '''      const p = Object.keys(r.params).map(k => `${k}=${r.params[k]}`).join(" · ");
      html += `<tr><td>${i + 1}</td><td style="text-align:left">${p}</td>'''
NEW_ROW = '''      const p = paramText(r.params, stype);
      html += `<tr><td>${i + 1}</td><td class="txt" style="max-width:520px">${p}</td>'''
assert s.count(OLD_ROW) == 1, s.count(OLD_ROW)
s = s.replace(OLD_ROW, NEW_ROW)

with open(P, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("app.js: Parameterdarstellung angepasst")
