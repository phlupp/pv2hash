# Prognosequalität und Vorbereitung des Energieplaners (Phase A/B)

**Status:** Entwicklung auf `testvm01`, bislang keine Produktivregelung und
kein automatischer Batterie-/Miner-Eingriff. Fachvorgaben zum Ladeziel
100 % SOC bis Sonnenuntergang minus zwei Stunden sind im gemergten Fachkonzept
[`docs/energy-planner.md`](energy-planner.md) dokumentiert.

Das getrennte Konzept für eine **lernende, ausschließlich parallel berechnete
PV-Shadow-Prognose** (Konfigurationsdiagnose, saisonale Verschattung,
dritte Diagrammkurve und fairer Qualitätsvergleich) steht in
[forecast-shadow-learning.md](forecast-shadow-learning.md).

## A – Unveränderliche Prognose-Revisionen

- `data/forecast.sqlite` bleibt der Prognose-Datenspeicher. Die bestehende
  `forecast_points`-Tabelle versorgt weiterhin die Live-GUI und wird nur für
  **zukünftige** Zeitpunkte aktualisiert. Historische Bestandsdaten werden
  **nicht rückwirkend rekonstruiert**.
- Zusätzlich werden bei **jedem erfolgreichen Prognoseabruf** alle
  tatsächlich neu gespeicherten zukünftigen Punkte in
  `forecast_revisions` (Abruf, Zeitpunkt, Anbieter, Zeitzone, Auflösung)
  und `forecast_revision_points` (UTC-Zielzeit, Wechselrichter/gesamt,
  Leistung in W) innerhalb **derselben SQLite-Transaktion** append-only
  festgehalten. Ein fehlgeschlagener Abruf erzeugt keine Revision.
- Dadurch bleibt nachvollziehbar, welche Leistung *zu welchem Zeitpunkt*
  vorhergesagt wurde, selbst wenn die zukünftige Anzeige-Prognose später
  überarbeitet wird. Auch beim Wechsel 15 → 60 Minuten werden Archivwerte
  nicht gelöscht. Für bereits abgelaufene Prognosen aus Versionen vor dieser
  Implementierung können frühere Revisionen **nicht** nachgebildet werden.
- Archivvolumen wächst mit Prognose-Intervall, Anzahl der Wechselrichter und
  dem Abrufhorizont (aktuell drei Tage). **365 Tage lokale Aufbewahrung**
  für vollständige Prognoserevisionen werden automatisch eingehalten,
  Qualitätswerte bleiben dauerhaft. Die Bereinigung erfolgt in kleinen
  Transaktionen ohne automatisches `VACUUM`.
  [SQLite-Status, Aufbewahrung und optionaler Influx-Export](sqlite-storage.md).

## Einheitliche UTC-Abrufzeitpunkte (für vergleichbare Vorlaufqualität)

Der automatische Abruf ist unabhängig vom Neustartzeitpunkt einer Instanz
**fest an das UTC-Viertelstundenraster gebunden**. Bei einem konfigurierten
15-Minuten-Intervall lauten die nominalen Zeiten `xx:10`, `xx:25`,
`xx:40` und `xx:55` **UTC**, also jeweils etwa **fünf Minuten vor**
dem nächsten Viertelstunden-Stichtag. Bei 30 Minuten erfolgen Abrufe
nominal `xx:10` und `xx:40`; andere Intervalle verwenden dieselbe
globale 15-Minuten-Rasterbasis. Werte, die kein Vielfaches von 15 Minuten
sind, werden für die *effektive Taktung* auf das nächste Vielfache
aufgerundet, damit nicht häufiger als konfiguriert abgerufen wird.

- Ein Hintergrundabruf startet nur im **vierminütigen Fenster** ab der
  geplanten UTC-Slotzeit. Wird ein Slot verpasst, wartet das System auf
  den nächsten; es behauptet nie eine rückdatierte Ausgabezeit.
- Die letzte erfolgreiche Revision ist bereits persistent gespeichert und
  unterdrückt einen erneuten Abruf im gleichen Slot auch nach Neustart.
  Bei Fehlern sind Wiederholungen innerhalb desselben Slots möglich.
- UTC vermeidet doppelte beziehungsweise übersprungene Slots bei
  Sommer-/Winterzeitumstellungen. Der tatsächlich gespeicherte
  `issued_at`-Zeitstempel bleibt maßgeblich für jede Qualitätsbewertung.
- **Vollständige 15-/30-Minuten-Vorlaufabdeckung:** Dazu ist ein
  **Abrufintervall von 15 Minuten** erforderlich, zusätzlich zur
  15-Minuten-**Prognoseauflösung**. Mit 30 oder mehr Minuten bleiben
  einige Viertelstunden-Ziele zwangsläufig ohne geeignete Prognoserevision;
  diese Lücken werden **nicht künstlich aufgefüllt**.
- Bestehende Instanz-Einstellungen, gespeicherte Revisionen, Export-Cursor
  und Vergleiche bleiben unverändert. Für gleichwertige Daten die
  Abrufintervalle aller zu vergleichenden Hashpis über die vorhandene
  Einstellung auf 15 Minuten setzen (separate freizugebende Konfiguration).

Die Qualitätsbewertung behält ausdrücklich ihre bisherigen engen
Toleranzen und verwendet nur **vorab archivierte Prognosen** und gültige
LIVE-Istwerte. Die feste Taktung beeinflusst weder Mining-/Batterie-
Regelung noch Shadow-Lernalgorithmen.

## Prognosefehler nach Vorlaufzeit

Eine unabhängige Hintergrundaufgabe wertet alle fünf Minuten vergangene,
jetzt messbare Prognosepunkte der letzten 48 Stunden aus. Die Aufgabe läuft
in einem **Thread**, getrennt von der Minerregelung; bei einem Neustart kann
sie bis zu 48 Stunden nachholen, sofern Rohmessungen noch existieren.

- Vorlaufhorizonte **15, 30, 60 und 180 Minuten**.
- Für jede Zielzeit wird ausschließlich die vorher **tatsächlich ausgegebene**
  Revision benutzt, deren Ausgabezeit auf dem/knapp vor dem gewünschten
  Vorlauf liegt (je Horizon maximal 8/8/10/20 zusätzliche Minuten Alter).
  Ein stundenalter Forecast zählt **nicht** als 15-Minuten-Prognose.
- Ist-Wert: der nächste reale `history_samples`-Eintrag im Abstand von
  höchstens ±60 Sekunden zur UTC-Zielzeit. Es zählen ausschließlich
  Messungen mit `pv_quality=live`, endlichem und gültigem Leistungswert.
  Ein näherer ungültiger/degradierter Messwert darf nicht durch einen weiter
  entfernten LIVE-Wert umgangen werden. Fehlende Daten werden nicht ersetzt.
- Jede auswertbare Kombination `(valid_at, horizon_minutes)` wird in
  `forecast_quality_samples` **einmalig und dauerhaft** abgelegt
  (Vorhersage, Ist, Fehler, ursprüngliche Ausgabezeit). Wiederholte
  Aufgabenläufe legen keine Duplikate an und verändern keine Bewertungen.
- Die dauerhaften Qualitätswerte überleben die 7-/30-Tage-Rohdatenspeicherung
  des DataLoggers. Für noch nicht beobachtete Daten gibt die API **null**
  statt künstlicher 0-W-Genauigkeit aus.
- API `GET /api/forecast/quality?days=7` (1–90 Tage) liefert Anzahl
  Prognoseabrufe, Stichprobenumfang, **MAE**, **Bias** (Prognose minus Ist)
  und **RMSE** in Watt pro Vorlaufzeit. Die GUI zeigt MAE und Anzahl
  gültiger Vergleiche für die letzten sieben Tage.
- Anfangs ist die Stichprobe naturgemäß klein. Die Zahl der verfügbaren
  Vergleiche hängt von API-Abrufintervall und Messqualität ab. Besonders
  für 15-Minuten-Horizonte kann ein 60-Minuten-Abrufintervall Lücken
  erzeugen: Genauigkeit wird **nicht** aus beliebig alten Abrufen geschätzt.

**Keine automatische Modellkalibrierung und keine Regelung auf Basis dieser
Kennzahlen.** Die Qualitätswerte dienen erst einmal dem Vergleich.

## B – Technische Vorbereitung des Batterieplaners

`GET /api/energy-planner/inputs` ist ein **nur lesendes** Dateninventar;
kein Vorschlag für einen Betriebszustand oder Startkommando. Der Endpunkt
liefert explizit `planner_enabled=false`, `ready_for_control=false`,
fehlende Pflichtinformationen, Live-Messqualität und die fachlichen
Standardwerte `soc_pct=100`, `time_mode=sunset`, `offset_minutes=-120`.
Diese Vorgaben sind derzeit **keine gespeicherten produktiven Einstellungen**.

Vorhanden: aktuelle Netz-, PV-, Batterie-SOC-, Lade-/Entlade- und Minerwerte.
Die Nennkapazität kann bei unterstützten Batterien optional aus den
Modbus-Diagnosedaten gelesen werden; sie wird **nicht** mit der tatsächlich
nutzbaren Kapazität verwechselt. Aus validen Momentanwerten ist die
Hauslast **ohne Miner** rechnerisch bestimmbar:

`Hauslast = PV-Erzeugung + Netzbezug + Batterieentladung − Batterieladung − Minerleistung`

Vorzeichenkonvention: Netz positiv = Import, negativ = Export. Degradierte,
fehlende, unmögliche oder nicht beobachtete Werte führen zu `null` und
expliziten `missing`-Einträgen; keine automatische Interpretation als
Nullverbrauch oder verfügbare Mining-Energie.

**Für die spätere zeitliche Simulation noch offen:** qualitätsgesicherte
nutzbare Kapazität, maximale Ladeleistung (W), Ladewirkungsgrad,
prognostizierter Hausgrundlastverlauf und verlässliche Standort-/Sonnen-
untergangsberechnung. Keine Minerregelung oder Batteriekonfiguration
wird durch diese Vorbereitungen verändert. Shadow Mode folgt erst in
Phase B nach zusätzlicher Freigabe.

## Testumfang

Regressionstests prüfen Atomarität, unveränderliche ältere Vorhersagen,
fehlende/ungültige LIVE-Messungen, zeitlich richtige Vorlaufhorizonte,
Neustart/mehrfachen Evaluationslauf, dauerhafte Kennzahlen und den
read-only Datenvertrag. Die Testdatenbanken liegen in temporären
Verzeichnissen auf `testvm01`; keine Änderung produktiver DBs.


## Lernende PV-Shadow-Prognose (Phase 1 – testvm01)

Der ergänzende Stand und Datenvertrag ist in
[`forecast-shadow-learning.md`](forecast-shadow-learning.md#9-entwicklungsstand-stufe-1--testvm01-feature-branch-20102026)
dokumentiert. Neue Basisrevisionen können atomar eine separat versionierte
Shadow-Revision erhalten. **Diese Änderung schreibt weder alte Basisrevisionen
noch alte Qualitätsproben um.** Der zusätzliche Endpoint
`GET /api/forecast/quality/shadow` vergleicht nur tatsächlich im Voraus
ausgegebene und über dieselbe gültige Ist-Messung paarbare Revisionen.
Ohne solche Paare gibt es **keinen** behaupteten Shadow-MAE.

### Optionaler Shadow-Influx-Export

Der neue, standardmäßig deaktivierte
\`forecast_shadow_quality\`-Stream exportiert ausschließlich dauerhaft
gespeicherte, as-issued gepaarte Basis-/Shadow-/LIVE-Vergleiche nach
\`pv2hash_forecast_shadow_quality\`. Unabhängiger Cursor je Ziel,
Ack/Retry und Zeitstempel der **Prognose-Zielzeit** sind in
[\`forecast-shadow-learning.md\`](forecast-shadow-learning.md)
dokumentiert. Der bisherige \`forecast_quality\`-Stream bleibt identisch.
Rohrevisionsarchive werden nicht automatisch nach InfluxDB übertragen.
