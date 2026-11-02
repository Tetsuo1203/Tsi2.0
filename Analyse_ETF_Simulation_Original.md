# Analyse: ETF_Simulation.xlsm

Reverse-Engineering der Original-Arbeitsmappe – Arbeitsblätter, Formeln, Makros,
nachgewiesene Logik und Schwachstellen. Grundlage für den Nachbau in
`ETF_Tool.py` (ETF-Timing-Studio).

---

## 1. Paketübersicht

| Element | Inhalt |
|---|---|
| Datei | `ETF_Simulation.xlsm` (3,3 MB, 14 Blätter, VBA-Projekt) |
| Arbeitsmappe | Codename „DieseArbeitsmappe“, gespeichert unter `D:\unverschluesselt\ETF\` |
| Definierte Namen | `_IDVTrackerID` = 171005, `_IDVTrackerVersion` = 1, sowie `Börsentag`, `Makro1`, `Skalierung` (alle `#N/A`) |
| Externe Verknüpfung | `[1]Kurse` – eingelesene Kursdatei (Spalte A = Datum, Datumswerte als Excel-Serials) |
| VBA | `xl/vbaProject.bin`, 4 Module (`Modul1`–`Modul4`) + 15 Blattmodule |
| Steuerelemente | 4 Schaltflächen auf „Regiezentrum“: `Indizes`, `SuP`, `Berechnungen`, `ETF_Import` |

### Blätter

| Blatt | Zeilen × Spalten | Rolle |
|---|---|---|
| `Regiezentrum` | 12 × 8 | Steuerzentrale: Datenquellen, Pfade, CSVs, Hyperlinks zu ariva.de, Laufzeiten der Makros, Schaltflächen |
| `Parameter` | 42 × 4 | Varianten-Steuerung: Monatsrotation, Signalparameter, Kosten, Steuer, Kapital, Ergebniskennzahlen |
| `Berechnungsblatt` | 4.061 × 10 | Backtest-Engine: Datum, Ampelindex, Ampel, ISIN, Kurs, Stück, Anschaffungskosten, Wert vor/nach Steuern, Drawdown |
| `Jahresergebnisse` | 19 × 4 | Jahresschlussstände mit absoluter und prozentualer Veränderung |
| `DAX_Monatsveränderung` | 61 × 13 | Monatsrenditen des DAX als Matrix (Jahre × Monate) |
| `Strategie_Monatsveränderung` | 60 × 13 | Monatsrenditen der Strategie, gleiche Matrix |
| `db_x-trackers` | 14.130 × 5 | Kursreihen der vier DB-X-trackers-ETFs (Datum + 4 ISIN-Spalten) |
| `DE0008469008` | 14.133 × 4 | DAX (Datum, Kurs, RSL, Ampel) |
| `DE0008467416` | 5.026 × 10 | MDAX (Datum, Kurs, RSL, Ampel) |
| `DE0008469016` | 4.202 × 10 | HDAX (Datum, Kurs, RSL, Ampel) |
| `EU0009658145` | 7.479 × 4 | EURO STOXX 50 |
| `US2605661048` | 14.599 × 4 | Dow Jones Industrial Average |
| `US78378X1072` | 14.602 × 4 | S&P 500 |
| `Konstanten` | 12 × 5 | ISIN-Verzeichnis, Beginn- und mögliche Berechnungszeiträume |

---

## 2. Die Strategie in einem Satz

Ein **Trendfolge-Timing-Modell**: Der Abstand des Signal-Index zu seinem
130-Tage-Durchschnitt („RSL“) schaltet eine **Ampel** mit Hysterese auf grün/rot.
Bei grün wird ein **gehebeltes Long-Produkt** (LevDAX, Faktor 2) gehalten, bei rot
ein **Short-Produkt** (ShortDAX −1 oder ShortDAX x2, Faktor −2) – gesteuert über
eine **monatliche Zuordnungstabelle**. Das erklärt Renditen von ~47 % p. a. – und
Drawdowns von ~47 %.

### Signalformel (Blatt `US78378X1072`, Spalte C und D)

```
C (RSL)   = B / AVERAGE(B[r-129 : r])
D (Ampel) = WENN( ODER( C <= Untergrenze(0,92);
                        UND( C < 1; ZÄHLENWENN(C[r-18:r]; "<1") = 19 ) ); "rot";
             WENN( ODER( C >= Obergrenze(1,05);
                        UND( C >= 1; ZÄHLENWENN(C[r-15:r]; ">=1") = 16 ) ); "grün";
                   D[r-1] ) )
```

Also:
* **sofort rot**, wenn der RSL unter 0,92 fällt,
* **sofort grün**, wenn er über 1,05 steigt,
* sonst **Hysterese**: 19 Tage unter 1 → rot, 16 Tage über 1 → grün, dazwischen
  bleibt der Zustand stehen.

Diese Logik wurde 1:1 nachgebaut und gegen die **im Original gespeicherten
Ampelwerte auf 14.453 Handelstagen** geprüft: **0 Abweichungen**.

### Kontoführung (Blatt `Berechnungsblatt`)

| Spalte | Formel (R1C1, gekürzt) | Bedeutung |
|---|---|---|
| E | `=INDEX('db_x-trackers'!B:E; MATCH($A; …; 1); MATCH($D; …; 0))` | Kurs des gewählten ETFs (bei „Kasse“ = 1) |
| F | `=(Startkapital − Gebühr) / E` bzw. `=(Wert nach Steuern − Gebühr) / E` | Stückzahl beim Umschichten |
| G | `=Parameter!R33C` (Start) bzw. `=Wert nach Steuern` | Anschaffungskosten |
| H | `=ROUND(E*F; 2)` | Wert vor Steuern |
| I | `=ROUND((H − Gebühr) + (G − H + Gebühr) * Steuersatz; 2)` | **Wert nach Steuern und Provision** (fiktive Liquidation) |
| J | `=1 − I / MAX(I$2:I)` | größter Rückschlag (Drawdown) |

Spalte I ist damit ein **Netto-Liquidationswert**: Ordergebühr abgezogen,
Abgeltungssteuer (26,375 %) auf den noch unrealisierten Gewinn anteilig
zurückgestellt. Genau dieses Modell verwendet der Nachbau (Modus „anteilig“).

Die Nachrechnung der Spalten H und I aus den Originaldaten ergab auf
**598 geprüften Zeilen 0 Abweichungen** (Toleranz 1 Cent).

### Parameter (Blatt `Parameter`, Spalten C und D = zwei Varianten)

| Zeile | Bedeutung | Wert (Variante C/D) |
|---|---|---|
| 1–24 | Monatsrotation: je Monat eine Zeile „grün“ und eine „rot“ mit ISIN | z. B. Jan grün = LevDAX, Jan rot = ShortDAX x2 |
| 25 | Ampelindex | `US78378X1072` (S&P 500) |
| 26 | RSL-Tage | 130 |
| 27 / 28 | Obergrenze / Untergrenze | 1,05 / 0,92 |
| 29 / 30 | Wechseltage grün / rot | 16 / 19 |
| 31 | Abgeltungssteuersatz | `=0,25*1,055*1` → 26,375 % |
| 32 | Transaktionskosten je Order | 5,90 € |
| 33 | Startkapital | 10.000 € |
| 34 / 35 | frühestens / gewählt | 01.01.2000 |
| 36–40 | Ergebnis: CAGR, größter Rückschlag (mit von/bis), Endkapital | z. B. C: 47,46 % p. a., 4.961.839 € |
| 41 / 42 | spätestens / gewählt | 23.12.2015 |

**Varianten-Konzept:** Jede Spalte ab C ist ein kompletter Parametersatz. Das
Makro `Berechnungen` schreibt die Formeln des `Berechnungsblatts` für jede
Variante neu, liest CAGR/Drawdown/Endkapital zurück und legt sie in den Zeilen
36–40 der jeweiligen Spalte ab. Die im Original gespeicherten Formeln verweisen
auf `Parameter!$D$…`, d. h. die zwischengespeicherte Rechnung ist die
**Variante D**.

---

## 3. Die Makros

### Was technisch ermittelbar war – und was nicht

* Der Zugriff auf das VBA-Projektobjektmodell (`.VBProject`) ist auf diesem
  System gesperrt (`AccessVBOM`), eine Registrierungskorrektur war nicht
  zulässig.
* `xl/vbaProject.bin` wurde daher direkt geparst (eigener MS-CFB- und
  MS-OVBA-Dekoder). Die Datei ist **nicht** von Excel, sondern von einem
  Fremdwerkzeug geschrieben: der `dir`-Stream ist komprimiert und verwendet
  ANSI- statt UTF-16-Strings und eine abweichende Datensatzstruktur.
* Der **Quelltext** ließ sich deshalb nicht vollständig rekonstruieren, wohl
  aber die **P-Code-Caches** der Module: deren Stringtabellen enthalten alle
  Prozedurnamen, Variablennamen, Zellbezüge, Formelbausteine und Meldungen.

Die folgenden Aussagen stützen sich auf drei voneinander unabhängige Quellen:
P-Code-Strings, die zwischengespeicherten Formeln der Blätter und die
Schaltflächen-Zuordnungen im Zeichnungsobjekt.

### Schaltflächen → Makros

`xl/drawings/drawing1.xml`, Blatt `Regiezentrum`:

| Schaltfläche | Makro |
|---|---|
| „Indizes aktualisieren“ | `[0]!Indizes` → `Modul2.Indizes` |
| „SuP“ (S&P 500) | `[0]!SuP` → `Modul3.SuP` |
| „Berechnungen“ / „Varianten berechnen“ | `[0]!Berechnungen` → `Modul1.Berechnungen` |
| „ETF_Import“ | `[0]!ETF_Import` → `Modul4.ETF_Import` |

### Modul1 – `Berechnungen` (Varianten rechnen)

Belegte Bestandteile aus dem P-Code:

* `Option Explicit`, Variablen u. a. `bytIndex As Byte`, `bytTätigkeitszeile`,
  `datBeginn`, `datMonatsende`, `intAnzahl_Berechnungsvarianten`,
  `lonLetzteBerechnungszeile`, `strAmpel`, `bytBerechnungsdurchgang`.
* `Application.ScreenUpdating = False`, `Application.Calculation = xlCalculationManual`
  → am Ende wieder `True`/`xlCalculationAutomatic`.
* `Sheets("Regiezentrum")`, `Sheets("Parameter")`, `Sheets("Berechnungsblatt")`,
  `Sheets("Jahresergebnisse")`, `Konstanten`.
* Baut die Formeln des Berechnungsblatts **als Text** und schreibt sie per
  `Cells(2, 6).FormulaR1C1 = …`, `Cells(39, intBerechnungsdurchgang + 2).FormulaR1C1 = …`.
* Formelbausteine (Auszug, wörtlich aus dem P-Code):
  `=RC2/AVERAGE(R[-130]C2:RC2)`,
  `=IF(OR(RC3<=Parameter!R28C,…),"rot",…)`,
  `=(Parameter!R33C-IF(RC4="Kasse",0,Parameter!R32C))/RC5`,
  `=ROUND(SUM(RC8-IF(RC4="Kasse",0,Parameter!R32C),(RC7-RC8+IF(RC4="Kasse",0,Parameter!R32C))*Parameter!R31C),2)`,
  `=1-RC9/MAX(R2C9:RC9)`,
  `=(R40C/R33C)^(365/(IF(R42C="",R41C,R42C)-IF(R35C="",R34C,R35C)))-1`.
* Kopiert Jahresergebnisse (`Range("A3:D…").Copy`, `PasteSpecial xlValues`) und
  schreibt Laufzeit/Zeitstempel ins Regiezentrum (`Now()`).

**Funktion:** Schleife über die Varianten-Spalten – Formeln setzen, berechnen
lassen, Kennzahlen (CAGR, größter Rückschlag mit Von/Bis, Endkapital)
zurücklesen, Jahresergebnisse fortschreiben.

### Modul2 – `Indizes` (Indexdaten importieren)

* Liest die Kursdatei `[1]Kurse` (Spalte A Datum) und schreibt die Indexreihen
  in die Blätter `DE0008469008`, `DE0008467416`, `DE0008469016`,
  `EU0009658145`, `US2605661048`, `US78378X1072`.
* Setzt `Konstanten!D/E` („möglicher Berechnungsbeginn/-ende“) über
  `=INDEX(<Blatt>!C3,…)` bzw. `=ROUND(INDEX(…!C5,…),2)`.
* **Baut die ETF-Reihen synthetisch**: für `db_x-trackers` je Spalte
  `=ROUND((1+(DAX_t/DAX_{t-1}−1)*Faktor)*Vortag;4)` mit Faktor 1 (DAX-ETF),
  2 (LevDAX), −1 (ShortDAX), −2 (ShortDAX x2). Die echten ariva-Kurse dienen nur
  als Startwert/Fallback.
* Setzt die RSL-/Ampelformeln der Indexblätter und verlängert sie bis zum
  letzten vorhandenen Datum.

### Modul3 – `SuP` (S&P 500 / Dow ergänzen)

* Sucht auf `Regiezentrum` den Eintrag „Standard & Poor’s 500 manuell eintragen“,
  ermittelt `MAX(US2605661048!A:A)` und `MAX(US78378X1072!A:A)` und kopiert die
  fehlenden Dow-Zeilen in das S&P-Blatt (`MATCH(…)+1`), danach
  `PasteSpecial xlValues`.
* Zweck: Der S&P 500 als Ampelindex muss tagesaktuell sein, auch wenn die
  Datenquelle für den S&P selbst unvollständig ist.

### Modul4 – `ETF_Import` (ariva-CSV einlesen)

* Pfad und Dateinamen stehen auf `Regiezentrum` (Spalte B/C):
  `D:\unverschluesselt\ETF\Aktualisierungen\DBX\Ariva_dbxtrackers_DAX.csv`
  usw., inklusive Hyperlink auf die ariva-Historienseite.
* Öffnet die CSV (`Workbooks.Open … ReadOnly:=True, Local:=True`), liest
  `Kurse!B:D`, übernimmt nur **positive** Kurse
  (`=IF(ISNA(MATCH(…)),RC,IF(INDEX(…)>0,ROUND(INDEX(…),4),RC))`)
  und schreibt sie in die passende ISIN-Spalte von `db_x-trackers`
  (`=MATCH(INDEX(Konstanten!C1,MATCH(RC1,Konstanten!C3,0)),'db_x-trackers'!R1,0)`).
* Schließt die Datei mit `Close SaveChanges:=False`.

### Blattmodule

Alle 15 Blattmodule enthalten nur `Option Explicit` – keine Ereignisprozeduren
(kein `Worksheet_Change`, kein `Workbook_Open`). Die Datei arbeitet also rein
auf Zuruf über die vier Schaltflächen.

---

## 4. Nebenbefunde und Schwachstellen

1. **Gehebelte/Short-ETFs sind geschlossen.** LevDAX (LU0411075376) und
   ShortDAX/ShortDAX x2 (LU0292106241, LU0411075020) sind nicht mehr
   handelbar; die Strategie ist so nicht mehr investierbar.
2. **Datenreihen sind teilweise synthetisch.** `db_x-trackers` enthält ab einem
   Stichtag berechnete Reihen (DAX-Tagesrendite × Faktor), keine echten
   ETF-Kurse – inklusive fehlender Kosten, Tracking-Differenz und
   Finanzierungsaufwand des Hebels. Das überschätzt Renditen systematisch.
3. **Monatsrotation ist fehlerhaft verdrahtet.** Die Formel
   `MATCH(DATE(…;"01."&TEXT(A;"MM.")&JAHR(HEUTE()));Parameter!A1:A24;0)` sucht
   den **Monatsersten** in einer Liste von **Monatsletzten** und trifft deshalb
   nie exakt; das `+WENN(Vortag="rot";1;0)` verschiebt die Zeile zusätzlich.
   Die tatsächlich verwendete ISIN ist damit nicht die beabsichtigte.
4. **Ergebniswerte sind inkonsistent.** `Jahresergebnisse` liefert ab 2006 nur
   Nullen, `Parameter!C36/C40` (47,46 % / 4,96 Mio. €) passen nicht zu den
   zwischengespeicherten Formeln der Variante D.
5. **Bewertung mit fiktiver Steuer, aber ohne Verlustverrechnung.** Spalte I
   versteuert jeden positiven Unterschiedsbetrag, Verluste werden nicht
   vorgetragen.
6. **Ausführung zum gleichen Schlusskurs.** Das Signal wird aus dem Schlusskurs
   berechnet und am selben Schlusskurs gehandelt – ein nicht einhaltbarer
   Vorteil (Look-ahead).
7. **Kein Benchmark-Vergleich, keine Risikokennzahlen** außer Drawdown; Sharpe,
   Volatilität, Sortino fehlen.
8. **Varianten-Suche ist Handarbeit**: eine Spalte je Variante, kein
   automatischer Parametersweep, kein Out-of-Sample-Test.

---

## 5. Was der Nachbau daraus macht

| Original | ETF-Timing-Studio |
|---|---|
| Ampel mit 130 Tagen, 1,05/0,92, 19/16 | identisch, alle Parameter frei einstellbar (validiert: 0 Abweichungen auf 14.453 Tagen) |
| Monatsrotation grün/rot über ISIN-Liste | optional, 12 × 2 Matrix; Standard = ein Risiko-ETF + Kasse |
| Gehebelte ETFs (geschlossen) | synthetische Hebelreihen `^GDAXI@2` usw. für den Original-Vergleich; Standard sind real handelbare ETFs |
| Spalte I = Netto-Liquidationswert | gleiches Steuermodell („anteilig“), zusätzlich „nur bei Verkauf“ und „keine Steuer“ |
| 5,90 € Gebühr, 26,375 % Steuer | frei einstellbar, zusätzlich Prozentgebühr, Slippage, Kasse-Zins |
| Ausführung zum selben Schlusskurs | Standard: nächster Schlusskurs (realistisch), umschaltbar |
| Eine Spalte je Variante | automatische Parameter-Suche (bis 144 Kombinationen) mit Calmar-Ranking |
| – | Walk-Forward-Test, Monte-Carlo-Projektion, Kosten-Sensitivität, ETF-Vergleich |
| – | Jahres-/Monatsmatrizen, Drawdown-Phasen, Trade-Liste, CSV-Export |
| Daten aus ariva-CSV per Makro | Yahoo-Finance-Download (ohne API-Key) plus CSV-Import (ariva, onvista, investing.com, Yahoo) |

### Validierung

| Prüfung | Ergebnis |
|---|---|
| Ampel-Logik gegen Originalwerte (S&P 500, 1958–2015) | 14.453 Tage, **0 Abweichungen** |
| Spalten H/I gegen Original (598 Zeilen) | **0 Abweichungen** (≤ 1 Cent) |
| Buy & Hold ohne Kosten | Endwert exakt = Kursverhältnis |
| Kauf/Verkauf mit Gebühr | genau 2 × 5 € Belastung |
| Steuer „anteilig“ | 1.000 € Gewinn → 263,75 € Steuer (exakt) |
| Jahresrenditen vs. Gesamtrendite | verkettet identisch |

---

## 6. Quellenlage

* `xl/workbook.xml`, `xl/worksheets/*.xml`, `xl/drawings/drawing1.xml`,
  `xl/externalLinks/externalLink1.xml` – direkt aus dem Paket gelesen.
* Zwischengespeicherte Werte und Formeln über `openpyxl`
  (`data_only=True`/`False`).
* `vbaProject.bin` – eigener MS-CFB-/MS-OVBA-Parser, P-Code-Stringtabellen der
  Module `Modul1`–`Modul4` und der Blattmodule.
* Makros wurden **nicht ausgeführt**; die Arbeitsmappe wurde nur mit
  deaktivierten Makros geöffnet (bzw. gar nicht, siehe oben).
