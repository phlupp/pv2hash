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

### controller_events

Echter Event-Log für angewendete Reglerentscheidungen. Enthält unter anderem Zeitpunkt, Miner, altes/neues Profil, Reason-Code, textuelle Flags, relevante Netz-/Batteriewerte und `decision_context_json`. Die Data-Logger-Zeitstrahlansicht und die Chart-Marker verwenden diese Tabelle direkt.

### history_events

Vorbereitet für spätere allgemeine Ereignisse wie Source-Ausfall oder Portal-Synchronisierung. Controller-Profilwechsel werden nicht mehr hier vorbereitet, sondern in `controller_events` gespeichert.

## Retention

PV2Hash löscht regelmäßig Samples, die älter als die konfigurierte Aufbewahrungszeit sind. Die Aufbewahrung ist auf maximal 30 Tage begrenzt.

## Portal-Vorbereitung

Der Data Logger nutzt den zentralen Runtime-Snapshot. Dadurch entstehen dieselben stabilen Datenstrukturen, die später auch für `pv2hash.net` verwendet werden können.

## Chart-Oberfläche

Die Data-Logger-Seite nutzt die lokal mitgelieferte Chart.js-Datei aus `pv2hash/static/vendor/chartjs/` und funktioniert ohne CDN oder Internetzugriff.

Die Zeitreihen werden über diesen Endpunkt geladen:

```text
GET /api/datalogger/series?range=1h|3h|6h|12h|24h|7d&max_points=720
GET /api/datalogger/series?range=1h&end=2026-05-10T12:00:00Z&max_points=720
```

Der Endpunkt liest aus `history.sqlite` und reduziert größere Zeiträume serverseitig auf eine begrenzte Punktzahl. Dadurch bleiben 24h- und 7d-Ansichten auch bei 10-Sekunden-Sampling browserfreundlich.

Die erste Chart-Ausbaustufe zeigt:

- **Energiefluss:** Netzanschluss, Minerleistung, Batterie-Ladeleistung und Batterie-Entladeleistung
- **Batterie:** SOC sowie Lade-/Entladeleistung; Ladeleistung wird positiv und Entladeleistung negativ dargestellt, die Watt-Achse wird symmetrisch um 0 skaliert
- **Mining:** Gesamthashrate und Minerleistung

Profilwechsel-Marker und der Zeitstrahl `Reglerentscheidungen` werden ausschließlich aus echten `controller_events` mit `event_type=applied` gelesen. Es gibt keinen errechneten Fallback über `history_miner_samples`; fehlende Marker zeigen damit bewusst an, dass kein Controller-Event gespeichert wurde. Die Timeline-Punkte sind klick- und touchfähig; die Detailkarte unter dem Zeitstrahl zeigt Zeitpunkt, Miner, Profilwechsel, Grund, Netz-/Batteriewerte und Flags.

Die Data-Logger-Seite lädt standardmäßig den Live-Bereich `1h` und aktualisiert die Charts bei sichtbarem Browser-Tab automatisch alle 30 Sekunden. Über die Zeitraum-Auswahl stehen zusätzlich `3h`, `6h`, `12h`, `24h` und `7d` zur Verfügung. Die Buttons `← Zurück` und `Weiter →` verschieben das aktuell gewählte Zeitfenster jeweils um die gewählte Auflösung. In dieser Historienansicht läuft kein Auto-Refresh. Mit `Live` springt die Ansicht wieder auf das aktuelle Zeitfenster der gewählten Auflösung und aktiviert den Auto-Refresh.

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

Der Gerätefilter steht oben auf der Data-Logger-Seite vor dem Regler-Zeitstrahl, ist kompakt und standardmäßig eingeklappt. Die Kopfzeile zeigt nur die aktuelle Auswahl, z. B. `Alle Miner`, einen einzelnen Minernamen oder `2 Miner`. Die vollständige Checkbox-Auswahl lässt sich über `Auswahl anzeigen` aufklappen.

Für Miner-Samples werden ab Schema-Version 2 zusätzlich einheitliche Temperaturfelder gespeichert:

- `temp_c`: repräsentative Miner-Temperatur
- `temp_asic_min_c`: niedrigste bekannte ASIC-/Board-Temperatur
- `temp_asic_max_c`: höchste bekannte ASIC-/Board-Temperatur

Bestehende `history.sqlite`-Datenbanken werden beim Start automatisch erweitert. Die Migration ergänzt fehlende Spalten per `ALTER TABLE`; ältere Samples behalten für diese Felder `NULL`.

Die Chart.js-Instanzen werden beim Auto-Refresh nicht mehr neu erzeugt, sondern per `chart.update("none")` aktualisiert. Dadurch bleibt das erste Einblenden der Charts erhalten, während zyklische Aktualisierungen ohne erneutes Fading erfolgen.
