# ETF-Timing-Studio

Ein eigenständiges Analyse-Werkzeug für **Trendfolge-Strategien auf Aktien und
ETFs** – Nachbau und Erweiterung der Arbeitsmappe `ETF_Simulation.xlsm`.

Läuft auf jedem Rechner mit **Python 3.8+**, ohne Installation von Paketen,
ohne Excel, ohne API-Key. Die Oberfläche öffnet sich im Browser, gerechnet wird
lokal auf dem eigenen Rechner.

```
python ETF_Tool.py
```

Windows: `Start_ETF_Tool.bat` doppelklicken.

> **Ausführliche Bedienungsanleitung:** [Anleitung.md](Anleitung.md) – Schritt für
> Schritt durch Oberfläche, Reiter, Parameter, Datenverwaltung und Fehlerbehebung.

---

## 1. Was das Tool macht

Es beantwortet die Frage, für die die Original-Arbeitsmappe gebaut wurde:

> „Wann sollte ich in einen ETF investiert sein – und wann lieber nicht?“

Dazu wird eine **Signalregel** auf einen Index (z. B. S&P 500) gerechnet, die
daraus **Risiko an / Risiko aus** ableitet, und diese Regel über Jahrzehnte
historischer Kurse **simuliert**: mit Ordergebühren, Slippage, Abgeltungssteuer,
Kassenverzinsung und einem ehrlichen Vergleich gegen Buy & Hold.

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
„rot“ ein **Short-DAX-ETF** gehalten – Renditen um 47 % p. a. bei Drawdowns um
47 %. Diese Produkte sind heute **geschlossen**. Das Tool bildet sie für den
Vergleich **synthetisch** nach (Preset „Original-Nachbau“) und stellt sonst
**real handelbare ETFs** in den Mittelpunkt.

---

## 2. Schnellstart

1. **Starten** – `python ETF_Tool.py` (oder `Start_ETF_Tool.bat`). Der Browser
   öffnet `http://127.0.0.1:8765/`.
2. **Daten laden** – Reiter „Daten“: Symbole eingeben (`IWDA.AS, ^GSPC,
   EXS1.DE`) → „Laden“. Oder CSV importieren. Beim ersten Start kann auch
   direkt „Standard-Daten laden“ geklickt werden.
3. **Strategie wählen** – links eine Vorlage auswählen, z. B.
   „RSL-Ampel · MSCI World (Kasse)“.
4. **Analysieren** – „Analyse starten“.
5. **Ansehen** – Reiter „Übersicht“, „Signale“, „Jahre & Monate“, „Trades“.
6. **Belastbarkeit prüfen** – „Parameter-Suche“, „Walk-Forward“,
   „Monte-Carlo“, „Kosten-Sensitivität“.

---

## 3. Die Reiter

| Reiter | Inhalt |
|---|---|
| **Übersicht** | Kennzahlenkarten, Vermögensverlauf (linear/log) gegen Buy & Hold, Drawdown-Verlauf, größte Verlustphasen, Kennzahlentabelle |
| **Signale** | Signalgeber mit gleitendem Durchschnitt und grün/rot hinterlegten Phasen, RSL-Verlauf mit Ober-/Untergrenze, aktueller Signalzustand |
| **Jahre & Monate** | Jahresrenditen als Balken (Strategie vs. Buy & Hold), Monatsrenditen-Heatmap, Jahresübersicht |
| **Trades** | alle Umschichtungen mit Kurs, Stück, Gebühr, Steuer, realisiertem Gewinn; Gewinnquote, Ø Haltedauer |
| **Vergleich** | dieselbe Strategie auf mehreren ETFs, sortiert nach Rendite, mit Balkendiagramm |
| **Parameter** | automatische Suche über RSL-Tage × Schwellen × Wechseltage, Heatmap der Calmar-Ratio, Rangliste, „übernehmen“ per Klick |
| **Robustheit** | Walk-Forward (rollierende Optimierung, echte Out-of-Sample-Kurve), Monte-Carlo-Projektion, Kosten-Sensitivität |
| **Daten** | Datenbestand verwalten, Yahoo-Download, CSV-Import |

---

## 4. Kennzahlen und wie man sie liest

| Kennzahl | Bedeutung | Worauf achten |
|---|---|---|
| **CAGR p. a.** | durchschnittliche Jahresrendite (geometrisch) | mit Buy & Hold vergleichen, nicht isoliert |
| **Max. Drawdown** | größter Verlust vom Hoch zum Tief | *die* Kennzahl für „hält man das durch?“ |
| **Calmar** | CAGR / max. Drawdown | Risiko-Ertrag-Verhältnis; > 0,5 ist gut |
| **Sharpe** | Rendite je Schwankungsrisiko | bei Trendfolge meist niedriger als Buy & Hold |
| **Volatilität** | annualisierte Tagesschwankung | Timing senkt sie meist deutlich |
| **Zeit im Markt** | Anteil der Tage mit Risikoanlage | niedrig = viel Kasse, viel Steuer- und Kostenträgheit |
| **Kosten & Steuer** | Gebühren + anteilige Steuer | bei häufigem Umschichten der Renditefresser |
| **Längste Unterwasserphase** | Tage unter dem alten Hoch | psychologisch entscheidend |

**Der wichtigste Punkt:** Eine Timing-Strategie gewinnt selten an Rendite – sie
gewinnt an **Drawdown** und **Glättung**. Wer nur auf CAGR schaut, wird fast
immer Buy & Hold besser finden. Der ehrliche Vergleich ist
„Rendite **pro** Verlust“ (Calmar, Sharpe) plus die Frage, ob man den
Drawdown psychologisch ausgehalten hätte.

### Beispiele aus diesem Tool (Stand der Daten: siehe Reiter „Daten“)

| Strategie | Zeitraum | CAGR | Buy & Hold | Max. DD | DD Buy & Hold |
|---|---|---|---|---|---|
| RSL-Ampel, S&P 500 | 1970–2026 | 5,69 % | 7,47 % | −32,9 % | −55,6 % |
| RSL-Ampel, DAX | 1988–2026 | 7,19 % | 7,91 % | −27,5 % | −69,6 % |
| RSL-Ampel, DAX-ETF (EXS1) | 2008–2026 | 5,91 % | 4,62 % | −32,1 % | −54,3 % |
| RSL-Ampel, MSCI World (IWDA) | 2009–2026 | 7,86 % | 10,92 % | −22,1 % | −30,6 % |

Lesart: Über sehr lange Zeiträume kostet der Filter **Rendite**, halbiert aber
den **maximalen Verlust**. Im bulligen MSCI-World-Zeitraum ab 2009 verliert er
klar – dort gab es kaum Bärenmärkte, die er hätte vermeiden können. Genau das
ist die ehrliche Antwort, die die Original-Arbeitsmappe schuldig bleibt.

---

## 5. Daten

### Yahoo Finance (automatisch, ohne Key)

Symbole wie bei Yahoo eingeben:

| Instrument | Symbol |
|---|---|
| iShares Core MSCI World (EUR, Amsterdam) | `IWDA.AS` |
| iShares Core MSCI World (Xetra) | `EUNL.DE` |
| Vanguard FTSE All-World (Acc) | `VWCE.DE` |
| iShares Core DAX | `EXS1.DE` |
| iShares Core S&P 500 (EUR) | `SXR8.DE` |
| Xetra-Gold | `4GLD.DE` |
| DAX / MDAX / S&P 500 / Nasdaq 100 | `^GDAXI` / `^MDAXI` / `^GSPC` / `^NDX` |
| EURO STOXX 50 / Dow Jones | `^STOXX50E` / `^DJI` |

Die Suche im Tool (Lupe) findet Symbole und Namen. Geladene Reihen liegen in
`data/cache/` und werden automatisch aktualisiert, wenn sie älter als ein paar
Tage sind. **Adjustierte Kurse** (Dividenden, Splits) werden verwendet.

### CSV-Import

Funktioniert mit Exporten von **ariva.de, onvista, investing.com, Yahoo** und
eigenen Dateien. Erkannt werden automatisch:

* Trennzeichen `;`, `,` oder Tab
* Datumsformate `31.12.2015`, `2015-12-31`, `31/12/2015`, Excel-Serials
* Dezimalkomma und Tausenderpunkte
* die richtige Kursspalte (bevorzugt „Schluss“, „Close“, „Adj Close“, „Kurs“)

Vorspann-Zeilen (Fondsinfo, WKN etc.) stören nicht.

### Warum keine direkte Browser-Abfrage?

Yahoo und Stooq senden keine CORS-Header, ein Browser dürfte die Daten nicht
direkt laden. Deshalb läuft der Download **im lokalen Python-Prozess** – das ist
auch der Grund für die Architektur „Python + Browser-Oberfläche“.

---

## 6. Konfiguration

| Feld | Bedeutung | Standard |
|---|---|---|
| Risiko-Anlage | ETF/Aktie/Index, der bei „grün“ gehalten wird | `IWDA.AS` |
| Signalgeber | Index für die Ampel | `^GSPC` |
| Defensiv-Anlage | Kasse (verzinst) oder defensiver ETF | Kasse |
| Signaltyp | RSL-Ampel, SMA-Trend, Golden Cross, Momentum | RSL-Ampel |
| Startkapital | | 10.000 € |
| Ordergebühr | fix je Order | 5,90 € |
| Gebühr % | prozentual je Order | 0 % |
| Slippage | Aufschlag/Abschlag je Ausführung | 5 bp |
| Steuersatz | Abgeltungssteuer inkl. Soli | 26,375 % |
| Kasse-Zins | Verzinsung der Kasse | 2 % p. a. |
| Ausführung | nächster Schlusskurs (realistisch) / gleicher Schlusskurs (wie Original) | nächster Schlusskurs |
| Steuer | anteilig (wie Original) / nur bei Verkauf / keine | anteilig |
| Zeitraum | Start/Ende, leer = alles | – |

**Steuermodi**

* *anteilig* – täglicher Netto-Liquidationswert: Gebühr und fiktive Steuer auf
  den unrealisierten Gewinn werden abgezogen. Das ist das Modell der
  Original-Spalte I und die konservative Sicht.
* *nur bei Verkauf* – Steuer erst beim tatsächlichen Verkauf (echte
  Cashflow-Sicht).
* *keine* – für Vorsteuer-Vergleiche.

---

## 7. Kommandozeile (ohne Browser)

```bash
python ETF_Tool.py --selftest
python ETF_Tool.py --download IWDA.AS ^GDAXI EXS1.DE
python ETF_Tool.py --backtest '{"risk":"IWDA.AS","signal":"^GSPC","signal_type":"ampel"}'
python ETF_Tool.py --port 9000 --no-browser
```

---

## 8. Dateien

| Datei | Zweck |
|---|---|
| `ETF_Tool.py` | das komplette Werkzeug (Server, Engine, Oberfläche) – **die einzige Datei, die Sie brauchen** |
| `Start_ETF_Tool.bat` | Start per Doppelklick unter Windows |
| `README_ETF_Tool.md` | Projektüberblick, Architektur, Verifikation, Grenzen |
| `Anleitung.md` | ausführliches Benutzerhandbuch: Bedienung, alle Reiter, Parameter, Datenpflege, Fehlerbehebung |
| `Analyse_ETF_Simulation_Original.md` | Reverse-Engineering der Original-Arbeitsmappe |
| `data/cache/*.json` | heruntergeladene Kursreihen (wird automatisch angelegt) |
| `data/index.json` | Verzeichnis der geladenen Instrumente |
| `tool_src/`, `build_tool.py` | Quellteile und Build-Skript (nur für Entwicklung) |

Die Oberfläche ist in `ETF_Tool.py` eingebettet (zlib+Base64). Wer sie ändern
will: `tool_src/ui/` bearbeiten und `python build_tool.py` ausführen.

---

## 9. Grenzen und Ehrlichkeit

* **Keine Anlageberatung.** Historische Simulationen sind keine Prognose.
* **Survivorship-/Auswahl-Bias:** Wer heute einen ETF auswählt, weiß, dass er
  überlebt hat. Die getestete Rendite ist dadurch eher zu hoch.
* **Yahoo-Daten** sind gut, aber nicht fehlerfrei (Split-/Dividendenkorrekturen,
  Lücken, gelegentlich falsche Schlusskurse). Für ernsthafte Arbeit:
  CSV-Import aus einer geprüften Quelle.
* **Steuer** ist stark vereinfacht (kein Verlusttopf über Jahre, keine
  Teilfreistellung, keine Sparerpauschbeträge, keine Kirchensteuer).
* **Kein Intraday, keine Limit-Orders, keine Liquiditätsprüfung.** Gehandelt
  wird zum Schlusskurs mit Aufschlag.
* **Parameter-Suche** neigt zur Überanpassung. Deshalb gibt es den
  Walk-Forward-Test: nur dessen Out-of-Sample-Ergebnis ist eine ehrliche
  Schätzung.
* Der **Original-Nachbau** ist eine Annäherung: Hebel und Short werden
  synthetisch aus dem DAX mit Finanzierungskosten gebildet, die Monatsrotation
  folgt der Variante D der Arbeitsmappe. Deshalb weicht das Endkapital vom
  Original ab (31,6 % statt 47,5 % p. a.) – die Originalzahlen beruhen auf
  idealisierten ETF-Reihen ohne Hebelkosten.

---

## 10. Fehlersuche

| Problem | Ursache / Lösung |
|---|---|
| „Keine Daten“ beim Start | Reiter „Daten“ → Symbole laden (Internet nötig) oder CSV importieren |
| Download schlägt fehl | Symbol prüfen (Yahoo-Suche), Firewall/Proxy, später erneut versuchen |
| Browser öffnet nicht | URL aus der Konsole manuell aufrufen |
| Port belegt | `--port 9000` verwenden (freier Port wird sonst automatisch gesucht) |
| „Zu wenige Kurstage“ | Zeitraum größer wählen oder Instrument mit längerer Historie |
| Diagramme leer | Fenster breiter ziehen (Neuzeichnen bei Größenänderung) |

---

## 11. Verifikation

Die Engine wurde gegen die Original-Arbeitsmappe geprüft (siehe
`Analyse_ETF_Simulation_Original.md`, Abschnitt 5):

* Signal-Logik: **14.453 Tage, 0 Abweichungen**
* Kontoführung (Wert vor/nach Steuern): **598 Zeilen, 0 Abweichungen**
* Invarianten: Buy & Hold ohne Kosten exakt, Gebühren exakt, Steuer exakt,
  Jahresrenditen verkettet = Gesamtrendite

`python ETF_Tool.py --selftest` führt einen schnellen Funktionstest aus.
