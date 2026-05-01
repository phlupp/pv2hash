# PV2Hash Data Logger

Der Data Logger zeichnet lokale Laufzeitdaten der PV2Hash-Instanz auf. Er ist die Grundlage für lokale Charts, Regleranalyse und eine spätere Portal-Synchronisierung.

## Standardwerte

- Aktiviert: ja
- Intervall: 10 Sekunden
- Aufbewahrung: 7 Tage
- Maximale Aufbewahrung: 30 Tage

Die Einstellungen sind unter **Einstellungen → Data Logger** änderbar.

## Speicherort

```text
data/history.sqlite
```

Die Datenbank wird automatisch erstellt. PV2Hash nutzt SQLite mit WAL-Journal, damit die Aufzeichnung leichtgewichtig bleibt.

## Tabellen

### history_samples

Ein Hauptsample pro Aufzeichnungsintervall. Enthält unter anderem:

- Netzleistung und Source-Quality
- Batterie-SOC, Lade-/Entladeleistung und Battery-Quality
- Gesamtleistung und Gesamthashrate der Miner
- Controller-Zusammenfassung und letzte Entscheidung
- Host-Werte wie CPU, RAM, Disk und Uptime

### history_miner_samples

Ein Gerätesample pro Miner und Aufzeichnungsintervall. Enthält unter anderem:

- stabile Miner-UUID
- Config-Key
- Profil
- Leistung
- Hashrate
- Erreichbarkeit
- Runtime-State

### history_events

Vorbereitet für spätere Ereignisse wie Profilwechsel, Source-Ausfall oder Portal-Synchronisierung. In Phase 1 wird die Tabelle angelegt, aber noch nicht aktiv befüllt.

## Retention

PV2Hash löscht regelmäßig Samples, die älter als die konfigurierte Aufbewahrungszeit sind. Die Aufbewahrung ist auf maximal 30 Tage begrenzt.

## Portal-Vorbereitung

Der Data Logger nutzt den zentralen Runtime-Snapshot. Dadurch entstehen dieselben stabilen Datenstrukturen, die später auch für `pv2hash.net` verwendet werden können.

## Chart-Oberfläche

Die Data-Logger-Seite nutzt die lokal mitgelieferte Chart.js-Datei aus `pv2hash/static/vendor/chartjs/` und funktioniert ohne CDN oder Internetzugriff.

Die Zeitreihen werden über diesen Endpunkt geladen:

```text
GET /api/datalogger/series?range=1h|6h|12h|24h|7d&max_points=720
```

Der Endpunkt liest aus `history.sqlite` und reduziert größere Zeiträume serverseitig auf eine begrenzte Punktzahl. Dadurch bleiben 24h- und 7d-Ansichten auch bei 10-Sekunden-Sampling browserfreundlich.

Die erste Chart-Ausbaustufe zeigt:

- **Energiefluss:** Netzanschluss, Minerleistung, Batterie-Ladeleistung und Batterie-Entladeleistung
- **Batterie:** SOC sowie Lade-/Entladeleistung; Ladeleistung wird positiv und Entladeleistung negativ dargestellt, die Watt-Achse wird symmetrisch um 0 skaliert
- **Mining:** Gesamthashrate und Minerleistung

Profilwechsel-Marker werden aus `history_miner_samples` abgeleitet. Wenn sich das Profil eines Miners im ausgewählten Zeitraum ändert, liefert die Series-API einen Marker mit Zeitpunkt, Minername sowie altem und neuem Profil. Die Chart-Oberfläche zeichnet diese Marker als vertikale Linien in die Charts ein; im Tooltip am nächstgelegenen Datenpunkt wird der Profilwechsel angezeigt.

Die Data-Logger-Seite lädt standardmäßig den Bereich `12h` und aktualisiert die Charts bei sichtbarem Browser-Tab automatisch alle 30 Sekunden. Beim Wechsel zurück in einen sichtbaren Tab wird sofort neu geladen.

## Platzierung der Statusinformationen

Die Data-Logger-Seite bleibt bewusst chart-fokussiert. Dort werden nur die wichtigsten Betriebsparameter als Badges angezeigt:

- Aktiv/Inaktiv
- Intervall
- Aufbewahrung

Die technischen Details zum lokalen Logger werden auf der Systemseite in einer eigenen Karte angezeigt:

- Aktiv
- Intervall
- Aufbewahrung
- Samples
- DB Size
- Letztes Sample als relative Zeit mit Sekunden

## Miner-Auswahl und Temperaturen

Die Data-Logger-Seite kann die Mining-Charts nach Minern filtern. Standardmäßig werden alle im gewählten Zeitraum verfügbaren Miner berücksichtigt. Alternativ können ein oder mehrere Miner ausgewählt werden; Leistung, Hashrate, Profilwechsel-Marker und Temperaturwerte werden dann nur für diese Auswahl aggregiert.

Für Miner-Samples werden ab Schema-Version 2 zusätzlich einheitliche Temperaturfelder gespeichert:

- `temp_c`: repräsentative Miner-Temperatur
- `temp_asic_min_c`: niedrigste bekannte ASIC-/Board-Temperatur
- `temp_asic_max_c`: höchste bekannte ASIC-/Board-Temperatur

Bestehende `history.sqlite`-Datenbanken werden beim Start automatisch erweitert. Die Migration ergänzt fehlende Spalten per `ALTER TABLE`; ältere Samples behalten für diese Felder `NULL`.

Die Chart.js-Instanzen werden beim Auto-Refresh nicht mehr neu erzeugt, sondern per `chart.update("none")` aktualisiert. Dadurch bleibt das erste Einblenden der Charts erhalten, während zyklische Aktualisierungen ohne erneutes Fading erfolgen.
