"""Zweite Runde UI-Feinheiten (idempotent)."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def patch(path, pairs):
    full = os.path.join(ROOT, path)
    with open(full, "r", encoding="utf-8") as f:
        s = f.read()
    changed = 0
    for old, new in pairs:
        if new in s and old not in s:
            continue
        if s.count(old) != 1:
            raise SystemExit(f"{path}: {s.count(old)}x für {old[:60]!r}")
        s = s.replace(old, new)
        changed += 1
    with open(full, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)
    print(f"{path}: {changed} Änderung(en)")


APP = [
    # Status-Pille nach dem Rechnen zurücksetzen
    ("  function busy(on, msg) {\n    S.busy = on;\n    $(\"#overlay\").classList.toggle(\"open\", on);\n"
     "    if (msg) $(\"#overlayMsg\").textContent = msg;\n    if (on) setStatus(\"busy\", msg || \"rechne …\");\n  }",
     "  function busy(on, msg) {\n    S.busy = on;\n    $(\"#overlay\").classList.toggle(\"open\", on);\n"
     "    if (msg) $(\"#overlayMsg\").textContent = msg;\n"
     "    if (on) setStatus(\"busy\", msg || \"rechne …\");\n    else setStatus(\"ok\", \"bereit\");\n  }"),
    # Leer-Hinweise für Diagrammflächen
    ("  function showTab(name) {",
     "  function emptyHint(id, text) {\n"
     "    const el = $(\"#\" + id);\n"
     "    if (el && !el.dataset.filled) { el.innerHTML = `<div class=\"hint\" style=\"padding:14px\">${text}</div>`; }\n"
     "  }\n\n  function showTab(name) {"),
    ("    if (name === \"data\") refreshDataView();",
     "    if (name === \"data\") refreshDataView();\n"
     "    if (name === \"params\" && !S.grid) emptyHint(\"chartGrid\", \"Starte die Parameter-Suche, um die \" +\n"
     "      \"Ergebnismatrix (RSL-Tage × Obergrenze) zu sehen.\");\n"
     "    if (name === \"robust\") {\n"
     "      if (!S.wf) emptyHint(\"chartWf\", \"Walk-Forward starten, um die Out-of-Sample-Kurve zu sehen.\");\n"
     "      if (!S.mc) emptyHint(\"chartMc\", \"Monte-Carlo-Projektion starten.\");\n"
     "    }\n"
     "    if (name === \"compare\") emptyHint(\"chartCompare\", \"Vergleich starten.\");"),
    # Datenbestand: Namen linksbündig
    ("          <td class=\"dim\" style=\"text-align:left\">${r.name || \"\"}</td>",
     "          <td class=\"dim txt\">${r.name || \"\"}</td>"),
    # Kennzeichnung gefüllter Diagramme
    ("  function drawGrid(rows) {",
     "  function drawGrid(rows) {\n    $(\"#chartGrid\").dataset.filled = \"1\";"),
    ("  function drawWf(wf) {\n    const o = wf.oos;",
     "  function drawWf(wf) {\n    $(\"#chartWf\").dataset.filled = \"1\";\n    const o = wf.oos;"),
    ("  function drawMc(mc, origin) {",
     "  function drawMc(mc, origin) {\n    $(\"#chartMc\").dataset.filled = \"1\";"),
    ("  function drawCompare(rows) {",
     "  function drawCompare(rows) {\n    $(\"#chartCompare\").dataset.filled = \"1\";"),
]

CSS = [
    ("td.pos,.pos{color:var(--green)}",
     "td.pos,.pos{color:var(--green)}\ntd.txt{text-align:left !important;white-space:normal}"),
]

if __name__ == "__main__":
    patch("tool_src/ui/app.js", APP)
    patch("tool_src/ui/style.css", CSS)
