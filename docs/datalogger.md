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
