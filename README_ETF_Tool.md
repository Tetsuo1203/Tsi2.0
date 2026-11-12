# ETF-Timing-Studio 1.1

Ein eigenständiges Analyse-Werkzeug für **Trendfolge-Strategien auf Aktien, ETFs,
Indizes und Kryptowährungen wie Bitcoin** – Nachbau und Erweiterung der Arbeitsmappe
`ETF_Simulation.xlsm`.

Läuft auf jedem Rechner mit **Python 3.8+**, ohne Installation von Paketen,
ohne Excel, ohne API-Key. Die Oberfläche öffnet sich im Browser, gerechnet wird
lokal auf dem eigenen Rechner.

```
python ETF_Tool.py
```

Windows: `Start_ETF_Tool.bat` doppelklicken.

> **Ausführliche Bedienungsanleitung:** [Anleitung.md](Anleitung.md) – Oberfläche,
> Reiter, Parameter, Steuern, Bitcoin, alle Kennzahlen mit Formeln, Validierung,
> Datenpflege und Fehlerbehebung.

---

## 1. Was das Tool macht

Es beantwortet die Frage, für die die Original-Arbeitsmappe gebaut wurde:

> „Wann sollte ich investiert sein – und wann lieber nicht?“

Dazu wird eine **Signalregel** auf einen Index oder das Instrument selbst gerechnet,
die daraus **Risiko an / Risiko aus** ableitet, und diese Regel über historische Kurse
**simuliert**: mit Ordergebühren, Slippage, deutscher Steuer (Abgeltungsteuer mit
Teilfreistellung, Sparerpauschbetrag und Verlusttopf bzw. §23-Haltefrist für Bitcoin
und Gold-ETCs), Kassenverzinsung und einem fairen Vergleich gegen Buy & Hold, das mit
exakt denselben Regeln gerechnet wird.

### Die Original-Strategie („RSL-Ampel“)

| Baustein | Regel |
|---|---|
| Signal | Abstand des Index zu seinem **130-Tage-Durchschnitt** (RSL) |
| Sofort rot | RSL ≤ **0,92** |
| Sofort grün | RSL ≥ **1,05** |
| Hysterese rot | RSL < 1 an **19** Tagen in Folge |
| Hysterese grün | RSL ≥ 1 an **16** Tagen in Folge |
| sonst | Zustand bleibt |

Diese Logik ist **eins zu eins** aus der Arbeitsmappe übernommen und gegen deren
gespeicherte Signale auf **14.453 Handelstagen** geprüft: **0 Abweichungen**.
Ebenso die Kontoführung (Stückzahl, Anschaffungskosten, Wert nach Steuern):
**0 Abweichungen** auf 598 geprüften Zeilen.

Im Original wurde bei „grün“ ein **2-fach gehebelter DAX-ETF** (LevDAX) und bei
„rot“ ein **Short-DAX-ETF** gehalten. Diese Produkte sind heute **geschlossen**. Das
Tool bildet sie für den Vergleich **synthetisch** nach (Preset „Original-Nachbau“) und
stellt sonst **real handelbare** ETFs und Bitcoin in den Mittelpunkt. Weitere Signale:
SMA-Trend (200-Tage-Linie), Golden Cross, Momentum.

---

## 2. Schnellstart

1. **Starten** – `python ETF_Tool.py` (oder `Start_ETF_Tool.bat`). Der Browser
   öffnet `http://127.0.0.1:8765/`.
2. **Daten laden** – Reiter „Daten“: Symbole eingeben (`IWDA.AS, ^GSPC, BTC-EUR`) →
   „Laden“, einen Eintrag der Schnellwahl anklicken oder CSV importieren. Beim ersten
   Start genügt „Standard-Daten laden“.
3. **Strategie wählen** – links eine Vorlage auswählen, z. B.
   „RSL-Ampel · MSCI World (Kasse)“ oder „Bitcoin · 200-Tage-SMA (Kasse)“.
4. **Analysieren** – „Analyse starten“; Hinweisbox (Datenqualität, Vorlauf) beachten.
5. **Ansehen** – Reiter „Übersicht“, „Signale“, „Jahre & Monate“, „Trades“.
6. **Belastbarkeit prüfen** – „Parameter-Suche“, „Walk-Forward“, „Monte-Carlo“,
   „Kosten-Sensitivität“.

---

## 3. Die Reiter

| Reiter | Inhalt |
|---|---|
| **Übersicht** | Hinweisbox, zehn Kennzahlenkarten, Vermögensverlauf (linear/log) gegen Buy & Hold, Drawdown-Verlauf, größte Verlustphasen mit Erholung, gruppierte Kennzahlentabelle mit Erklärungen |
| **Signale** | Signalgeber mit gleitendem Durchschnitt und grün/rot hinterlegten Phasen, RSL-Verlauf mit Ober-/Untergrenze, aktueller Signalzustand |
| **Jahre & Monate** | Jahresrenditen (Strategie vs. Buy & Hold), Monatsrenditen-Heatmap, **rollierende 1/3/5-Jahres-Renditen** mit „wie oft vorn / wie oft Verlust“, Jahresübersicht |
| **Trades** | alle Orders mit Kurs, Stück, Gebühr, Steuer, Gewinn, Haltedauer; Gewinnquote, Profit-Faktor, Fehlsignale, Verlustserien |
| **Vergleich** | dieselbe Strategie auf mehreren Instrumenten inkl. Calmar gegen Buy & Hold, Up/Down-Capture und Datenqualität |
| **Parameter** | Suche über das volle Raster, Ziel wählbar (Calmar, Martin, Sharpe, CAGR), Heatmap, **Plateau-Anteil**, Rangliste, „übernehmen“ per Klick |
| **Robustheit** | Walk-Forward (volles Raster, durchgehende Out-of-Sample-Simulation, Buy & Hold und feste Parameter als Referenz), Monte-Carlo (Block-Bootstrap, Verlust- und Outperformance-Wahrscheinlichkeit), Kosten-Sensitivität (inkl. 1 Tag Verzögerung) |
| **Daten** | Datenbestand mit **Qualitätsprüfung**, Yahoo-Download, Schnellwahl, CSV-Import |

---

## 4. Kennzahlen und wie man sie liest

| Kennzahl | Bedeutung | Worauf achten |
|---|---|---|
| **CAGR p. a.** | durchschnittliche Jahresrendite (geometrisch) | mit Buy & Hold vergleichen, nicht isoliert |
| **Max. Drawdown** | größter Verlust vom Hoch zum Tief | *die* Kennzahl für „hält man das durch?“ |
| **Calmar** | CAGR / max. Drawdown | Risiko-Ertrag-Verhältnis; für Timing am wichtigsten |
| **Ulcer-Index / Martin** | Tiefe und Dauer aller Verluste / Ertrag je Ulcer | robuster als nur der tiefste Verlust |
| **Sharpe / Sortino** | Überrendite über Kasse-Zins je Schwankung / je Abwärtsschwankung | Standarddefinitionen mit risikolosem Zins |
| **Up-/Down-Capture** | Anteil an den Gewinn-/Verlustmonaten von Buy & Hold | ideal: Up hoch, Down niedrig |
| **Beta, Alpha, Tracking Error, Information Ratio** | Bezug zu Buy & Hold | Marktabhängigkeit und Mehrwert |
| **VaR / CVaR 95 %** | schlechte bzw. sehr schlechte Tage | Tail-Risiko |
| **Rollierende Renditen** | Ergebnis je Einstiegszeitpunkt über 1/3/5 Jahre | wie oft vorn, wie oft Verlust |
| **Zeit im Risiko, Wechsel/Jahr, Fehlsignale** | Handelsverhalten | Kosten- und Steuertreiber |
| **Kosten & Steuer** | Gebühren, gezahlte und latente Steuer | bei häufigem Umschichten der Renditefresser |

**Der wichtigste Punkt:** Eine Timing-Strategie gewinnt selten an Rendite – sie
gewinnt an **Drawdown** und **Glättung**. Der ehrliche Vergleich ist „Rendite **pro**
Verlust“ (Calmar, Martin) plus die Frage, ob man den Drawdown ausgehalten hätte.

### Beispiele (Daten bis 11/2026, Standardeinstellungen)

| Strategie | Zeitraum | CAGR | Buy & Hold | Max. DD | DD Buy & Hold | Calmar | B&H |
|---|---|---|---|---|---|---|---|
| RSL-Ampel, S&P 500 (Kursindex) | 1970–2026 | 6,81 % | 8,20 % | −34,9 % | −56,1 % | 0,20 | 0,15 |
| RSL-Ampel, S&P 500 Total Return | 1988–2026 | 9,62 % | 10,77 % | −17,7 % | −53,7 % | 0,54 | 0,20 |
| RSL-Ampel, DAX | 1988–2026 | 7,73 % | 7,83 % | −28,7 % | −70,0 % | 0,27 | 0,11 |
| RSL-Ampel, MSCI World (IWDA) | 2009–2026 | 9,08 % | 11,50 % | −24,0 % | −31,4 % | 0,38 | 0,37 |
| 200-Tage-SMA, Bitcoin (BTC-EUR) | 2015–2026 | 52,68 % | 63,23 % | −68,3 % | −82,7 % | 0,77 | 0,76 |

Lesart: Über lange Zeiträume kostet der Filter **Rendite**, halbiert aber den
**maximalen Verlust**. Im bulligen MSCI-World-Zeitraum ab 2009 verliert er klar.
Bei Bitcoin senkt der 200-Tage-Filter den Drawdown deutlich, kostet aber Rendite –
auch weil kurzfristige Gewinne steuerpflichtig sind, Buy & Hold nach einem Jahr nicht.
Weitere Zahlen und die Lesart: Anleitung, Kapitel 15.4.

---

## 5. Daten

### Yahoo Finance (automatisch, ohne Key)

| Instrument | Symbol |
|---|---|
| iShares Core MSCI World (EUR, Amsterdam / Xetra) | `IWDA.AS` / `EUNL.DE` |
| Vanguard FTSE All-World (Acc) | `VWCE.DE` |
| iShares Core DAX | `EXS1.DE` |
| iShares Core S&P 500 (EUR) | `SXR8.DE` |
| Xetra-Gold | `4GLD.DE` |
| DAX / MDAX / S&P 500 / Nasdaq 100 | `^GDAXI` / `^MDAXI` / `^GSPC` / `^NDX` |
| S&P 500 mit Dividenden (Total Return, ab 1988) | `^SP500TR` |
| Bitcoin / Ethereum (EUR) | `BTC-EUR` / `ETH-EUR` |
| Bitcoin-ETC/-ETN (Xetra) / US-Bitcoin-ETF | `BTCE.DE`, `VBTC.DE` / `IBIT` |

Die Suche im Tool (Lupe) findet Symbole und Namen; der Reiter „Daten“ bietet eine
Schnellwahl. Geladene Reihen liegen in `data/cache/` und werden automatisch
aktualisiert, wenn sie älter als ein paar Tage sind. **Adjustierte Kurse**
(Dividenden, Splits) werden verwendet; Kursindizes wie `^GSPC` enthalten dennoch
keine Dividenden – das Tool weist darauf hin.

### Datenqualität

Jede Reihe wird auf Sprünge (Datenfehler, Währungs-/Splitwechsel), Ausreißer, Lücken
und eingefrorene Kurse geprüft; echte Crashs in unruhigen Phasen werden davon
unterschieden. Beispiel: `SXR8.DE` hat am 01.11.2010 einen Datenfehler von −24,6 %.

### CSV-Import

Funktioniert mit Exporten von **ariva.de, onvista, investing.com, Yahoo** und
eigenen Dateien. Erkannt werden Trennzeichen (`;`, `,`, Tab), Datumsformate
(`31.12.2015`, `2015-12-31`, `31/12/2015`, `12/31/2015` – Tag oder Monat zuerst wird
**je Datei** bestimmt), Excel-Serials, Dezimalkomma und Tausenderpunkte sowie die
richtige Kursspalte. Vorspann-Zeilen stören nicht.

### Warum keine direkte Browser-Abfrage?

Yahoo sendet keine CORS-Header, ein Browser dürfte die Daten nicht direkt laden.
Deshalb läuft der Download **im lokalen Python-Prozess** – das ist auch der Grund für
die Architektur „Python + Browser-Oberfläche“.

---

## 6. Konfiguration

| Feld | Bedeutung | Standard |
|---|---|---|
| Risiko-Anlage | ETF/Aktie/Index/Krypto, der bei „grün“ gehalten wird | `IWDA.AS` |
| Signalgeber | Reihe für das Signal | `^GSPC` |
| Defensiv-Anlage | Kasse (verzinst) oder defensiver ETF | Kasse |
| Signaltyp | RSL-Ampel, SMA-Trend, Golden Cross, Momentum | RSL-Ampel |
| Startkapital | | 10.000 |
| Ordergebühr / Gebühr % | fix bzw. prozentual je Order | 5,90 € / 0 % (Bitcoin-Vorlagen: 0 € / 0,25 %) |
| Slippage | Auf-/Abschlag je Ausführung | 5 bp (Bitcoin: 15 bp) |
| Kasse-Zins | Verzinsung der Kasse, auch risikoloser Zins für Sharpe | 2 % p. a. |
| Ausführung | nächster Schlusskurs (realistisch) / gleicher Schlusskurs (wie Original) | nächster Schlusskurs |
| Steuer-Bewertung | anteilig (wie Original) / nur bei Verkauf / keine | anteilig |
| Steuerregel | automatisch / Aktienfonds (30 % TF) / Mischfonds (15 %) / ohne TF / §23 | automatisch |
| Abgeltungsteuer | inkl. Soli | 26,375 % |
| Sparerpauschbetrag | je Jahr | 1.000 € |
| Pers. Steuersatz / Freigrenze (§23) | für Bitcoin & Gold-ETCs innerhalb eines Jahres | 35 % / 1.000 € |
| Zeitraum | Start/Ende, leer = alles ab gültigem Signal | – |

Details zu allen Steuerregeln: Anleitung, Kapitel 8.

---

## 7. Kommandozeile (ohne Browser)

```bash
python ETF_Tool.py --selftest
python ETF_Tool.py --download IWDA.AS ^GDAXI EXS1.DE BTC-EUR
python ETF_Tool.py --quality SXR8.DE
python ETF_Tool.py --backtest '{"risk":"IWDA.AS","signal":"^GSPC","signal_type":"ampel"}'
python ETF_Tool.py --port 9000 --no-browser
```

---

## 8. Dateien

| Datei | Zweck |
|---|---|
| `ETF_Tool.py` | das komplette Werkzeug (Server, Engine, Oberfläche) – **die einzige Datei, die Sie brauchen** |
| `Start_ETF_Tool.bat` | Start per Doppelklick unter Windows |
| `README_ETF_Tool.md` | dieser Projektüberblick |
| `Anleitung.md` (= `README.md`) | ausführliches Benutzerhandbuch |
| `Analyse_ETF_Simulation_Original.md` | Reverse-Engineering der Original-Arbeitsmappe |
| `data/cache/*.json`, `data/index.json` | geladene Kursreihen und ihr Verzeichnis |
| `tool_src/engine.py`, `data.py`, `server.py`, `ui/` | Quellteile |
| `build_tool.py` | baut `ETF_Tool.py` aus den Quellteilen (prüft dabei auf Namenskonflikte) |
| `tool_src/validate_engine.py`, `api_test.py`, `make_numbers.py` | Validierung, End-to-End-Test, Doku-Zahlen |

Die Oberfläche ist in `ETF_Tool.py` eingebettet (zlib+Base64). Wer etwas ändern
will: `tool_src/` bearbeiten und `python build_tool.py` ausführen.

---

## 9. Grenzen und Ehrlichkeit

* **Keine Anlageberatung.** Historische Simulationen sind keine Prognose.
* **Survivorship-/Auswahl-Bias:** Wer heute einen ETF oder Bitcoin auswählt, weiß,
  dass er überlebt hat. Die getestete Rendite ist dadurch eher zu hoch.
* **Yahoo-Daten** sind gut, aber nicht fehlerfrei – die Qualitätsprüfung findet die
  typischen Fehler, ersetzt aber keinen Blick auf den Kursverlauf.
* **Steuer** ist vereinfacht: keine Vorabpauschale, kein getrennter Aktien-Verlusttopf,
  Kirchensteuer nur über den Steuersatz; die automatische Steuerregel ist eine Näherung.
* **Kasse** mit konstantem Zins, **keine Währungsumrechnung** (Ergebnisse in der Währung
  der Risiko-Anlage), kein Intraday, keine Limit-Orders.
* **Parameter-Suche** neigt zur Überanpassung. Nur das Out-of-Sample-Ergebnis des
  Walk-Forward-Tests ist eine ehrliche Schätzung.
* Der **Original-Nachbau** ist eine Annäherung: Hebel und Short werden synthetisch aus
  dem DAX mit Finanzierung und Kosten gebildet (Abweichung zum echten LevDAX 2010–2026:
  0,9 Prozentpunkte p. a.). Er erreicht 35,4 % statt 47,5 % p. a. – die Originalzahlen
  beruhen auf idealisierten Reihen ohne Finanzierungskosten.

---

## 10. Fehlersuche

| Problem | Ursache / Lösung |
|---|---|
| „Keine Daten“ beim Start | Reiter „Daten“ → Symbole laden (Internet nötig) oder CSV importieren |
| Download schlägt fehl | Symbol prüfen (Yahoo-Suche), Firewall/Proxy, später erneut versuchen |
| Browser öffnet nicht | URL aus der Konsole manuell aufrufen |
| Port belegt | `--port 9000` verwenden (freier Port wird sonst automatisch gesucht) |
| Start später als eingestellt | Signal-Vorlauf: der Test beginnt erst mit gültigem Signal |
| Gelbe Hinweisbox | Datenfehler im Zeitraum – Zeitraum danach wählen |

---

## 11. Verifikation

* Signal-Logik gegen die Arbeitsmappe: **14.453 Tage, 0 Abweichungen**
* Kontoführung (Wert vor/nach Steuern): **598 Zeilen, 0 Abweichungen**
* Synthetischer 2x-DAX gegen echten LevDAX (DBPE.DE): 11,9 % vs. 12,8 % p. a.
* **27 Regressionstests** der Rechenregeln (`python ETF_Tool.py --selftest`), u. a.
  Buy & Hold exakt, Gebühren exakt, Steuer 263,75 € auf 1.000 € Gewinn (wie Original),
  Teilfreistellung, Pauschbetrag, Verlusttopf, §23, Strategie = Buy & Hold bei „immer
  investiert“ in allen Steuermodi, Jahres-/Monatskette = Gesamtrendite, Kassezins
  2,00 % bei Börsen- und Krypto-Kalender, Sharpe/Sortino gegen Handrechnung
* End-to-End-Test der Programmschnittstelle (`tool_src/api_test.py`)

Version 1.1 behebt mehrere Rechenfehler der Version 1.0 (u. a. Benchmark im Steuermodus
„nur bei Verkauf“, Finanzierung synthetischer Short-Produkte, unvollständiges
Walk-Forward-Raster, Sharpe/Sortino, CSV-Datumsformat, 252-Tage-Annahme bei Krypto).
Vollständige Liste mit Auswirkungen: Anleitung, Kapitel 15.2.
