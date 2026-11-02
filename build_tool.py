"""Baut aus den Quellteilen die einzelne auslieferbare Datei ETF_Tool.py."""
import base64
import os
import re
import zlib

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "tool_src")
UI = os.path.join(SRC, "ui")
OUT = os.path.join(ROOT, "ETF_Tool.py")


def read(p):
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def strip_future(code):
    return re.sub(r"^from __future__ import annotations\s*$", "", code, flags=re.M)


def build_ui() -> str:
    html = read(os.path.join(UI, "index.html"))
    css = read(os.path.join(UI, "style.css"))
    chart = read(os.path.join(UI, "chart.js"))
    app = read(os.path.join(UI, "app.js"))
    html = html.replace('<link rel="stylesheet" href="style.css">',
                        "<style>\n" + css + "\n</style>")
    html = html.replace('<script src="chart.js"></script>\n<script src="app.js"></script>',
                        "<script>\n" + chart + "\n</script>\n<script>\n" + app + "\n</script>")
    if "src=\"app.js\"" in html or "style.css" in html:
        raise SystemExit("UI-Platzhalter nicht ersetzt!")
    return html


def main():
    engine = strip_future(read(os.path.join(SRC, "engine.py")))
    data = strip_future(read(os.path.join(SRC, "data.py")))
    server = strip_future(read(os.path.join(SRC, "server.py")))
    data = re.sub(r"^from engine import .*$", "", data, flags=re.M)
    server = re.sub(r"^import data as D\s*$", "", server, flags=re.M)
    server = re.sub(r"^import engine as E\s*$", "", server, flags=re.M)

    ui = build_ui()
    blob = base64.b64encode(zlib.compress(ui.encode("utf-8"), 9)).decode("ascii")
    chunks = [blob[i:i + 96] for i in range(0, len(blob), 96)]
    ui_py = ("_UI_B64 = (\n" + "\n".join('    "%s"' % c for c in chunks) + "\n)\n"
             "UI_HTML = zlib.decompress(base64.b64decode(_UI_B64)).decode(\"utf-8\")\n")

    header = '''#!/usr/bin/env python3
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

'''

    parts = [header, "# " + "=" * 74 + "\n# Teil 1/3: Analyse-Engine\n# " + "=" * 74 + "\n\n",
             engine, "\n\n# " + "=" * 74 + "\n# Teil 2/3: Datenschicht (Yahoo, CSV, Cache)\n# " + "=" * 74 + "\n\n",
             data, "\n\n# " + "=" * 74 + "\n# Teil 3/3: Server, API und Oberflaeche\n# " + "=" * 74 + "\n\n",
             server.replace('UI_HTML = "<!doctype html><html><body>UI nicht eingebettet</body></html>"',
                            ui_py)]

    code = "".join(parts)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(code)
    print(f"geschrieben: {OUT}  ({len(code):,} Zeichen, {code.count(chr(10)):,} Zeilen)")
    print(f"UI: {len(ui):,} Zeichen -> {len(blob):,} Zeichen Base64")


if __name__ == "__main__":
    main()
