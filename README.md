# PV2Hash

PV2Hash ist ein lokales Steuerungs- und Monitoring-System zur Nutzung von Überschussleistung am Netzanschlusspunkt für Bitcoin-Mining mit ASIC-Minern.

Ziel ist es, verfügbare Leistung am Netzanschlusspunkt automatisch in Miner-Leistung umzusetzen – lokal, leichtgewichtig und ohne Cloud-Abhängigkeit.

PV2Hash läuft als Webanwendung auf Linux-Systemen wie Debian und ist modular aufgebaut, damit verschiedene Energiequellen, Regelstrategien und Miner-Treiber unterstützt werden können.

---

## Status

PV2Hash hat den Stand einer **ersten voll funktionsfähigen Version** erreicht.

Der aktuelle veröffentlichte Stand ist über die **GitHub Releases** und **Tags** ersichtlich.

Bereits umgesetzt sind unter anderem:

- modulare Grundarchitektur mit `sources`, `controller`, `miners`, `models`, `templates`
- persistente JSON-Konfiguration mit Runtime-Reload
- funktionierender Simulator für Quelle und Miner
- SMA Energy Meter Anbindung über das SMA Meter Protocol
- echte Verteil- und Schrittlogik für mehrere Miner
- native Braiins gRPC-Anbindung für einen echten Miner
- Weboberfläche für Dashboard, Quellen, Miner, Einstellungen und System
- Logging mit Datei-Log, Ringbuffer und Web-Konsole
- Release-Builds und GitHub-Releases als Grundlage für Installer und Self-Update

---

## Projektziel

PV2Hash steuert ASIC-Miner anhand der aktuell am Netzanschlusspunkt verfügbaren Leistung.

Die Regelung orientiert sich dabei **nicht** an einer theoretischen PV-Leistung oder einem abgeleiteten Hausverbrauch, sondern am real gemessenen Verhalten am Netzanschlusspunkt:

- **negative Netzleistung** = Einspeisung
- **positive Netzleistung** = Netzbezug

Dadurch kann PV2Hash unabhängig von der konkreten Erzeugerstruktur arbeiten. Für die Regelung ist zunächst nur der Netzanschlusspunkt relevant. Später ist zusätzlich eine Batterie-Integration vorgesehen.

---

## Architektur

PV2Hash ist modular aufgebaut:

- `pv2hash/sources/` → Energiequellen
- `pv2hash/miners/` → Miner-Treiber
- `pv2hash/controller/` → Regel- und Verteillogik
- `pv2hash/models/` → gemeinsame Datenmodelle
- `pv2hash/templates/` → HTML-Templates
- `pv2hash/static/` → statische Assets
- `pv2hash/logging_ext/` → Logging-Basis
- `pv2hash/config/` → Konfiguration
- `pv2hash/runtime.py` / `pv2hash/services.py` → Laufzeitstatus und Reload
- `scripts/` → Hilfsskripte für Tests, Build und Installation

---

## Unterstützte Quellen

### `simulator`

Reproduzierbarer linearer Netzanschlusspunkt-Simulator mit Miner-Feedback.

### `sma_meter_protocol`

SMA Energy Meter / Home Manager Anbindung per Multicast über das SMA Meter Protocol (`0x6069`).

Merkmale:

- Multicast-Empfang über `239.12.255.254:9522`
- Auswahl einer lokalen Interface-IP
- optionaler Geräte-IP-Filter
- korrekte Auswertung der Netzleistung am Netzanschlusspunkt
- Vorzeichenlogik:
  - **negativ** = Einspeisung
  - **positiv** = Netzbezug

---

## Unterstützte Miner-Treiber

### `simulator`

Simulierter Miner für Entwicklung, Tests und Regelungsvalidierung.

### `braiins`

Native Braiins OS+ Anbindung per **Python gRPC**.

Aktuell umgesetzt:

#### Read

- `GetApiVersion`
- `Login`
- `GetConstraints`
- `GetMinerDetails`
- `GetMinerStatus` (erste Stream-Nachricht)
- `GetMinerStats`
- `GetErrors`
- `GetTunerState`

#### Write

- `PauseMining`
- `ResumeMining`
- `Start`
- `SetPowerTarget`

#### Semantik

- `profile == "off"` → `PauseMining`
- `profile power_w <= 0` → `PauseMining`
- `profile power_w > 0` → bei Bedarf `ResumeMining` / `Start`, danach `SetPowerTarget`

Wichtige Besonderheit:

Ein pausierter Miner bleibt im PV2Hash-Sinn weiter **regelbar**, damit er sauber wieder von `floor` auf höhere Profile angehoben werden kann.

---

## Regelung

PV2Hash verwendet derzeit eine **coarse-Regelung** mit klaren Leistungsstufen.

### Profile

Es gibt intern diese Profilsemantik:

- `off`
- `floor`
- `eco`
- `mid`
- `high`

Bedeutung:

- `off` = bewusste Pause
- `floor` = niedrigstes normales Regelprofil
- `floor = 0 W` bedeutet Pause
- `floor > 0 W` bedeutet minimale Dauerleistung

### Verteilstrategien

#### `equal`

Alle aktiven Miner werden gemeinsam stufenweise geregelt.

#### `cascade`

Miner werden nach Priorität von unten nach oben durchgeschaltet.

---

## Verhalten bei Quellverlust

Das Source-Loss-Handling liegt im Controller.

Aktuell unterstützt:

- `off_all`
- `force_profile`
- `hold_current`

Zusätzlich gilt:

- `hold_seconds = 0` bedeutet unbegrenztes Halten bis die Quelle zurückkommt
- `simulated` wird wie `live` behandelt

---

## Weboberfläche

Die Weboberfläche enthält aktuell:

- Dashboard
- Quellen
- Miner
- Einstellungen
- System

Bereits umgesetzt:

- überarbeitetes Dashboard
- überarbeitete Minerseite
- Runtime-Daten, Constraints und Status auf der Minerseite
- Formularseiten für Konfiguration
- Live-Status und Diagnose

---

## Logging

PV2Hash besitzt eine zentrale Logging-Basis.

Aktuell vorhanden:

- Datei-Logging unter `data/logs/pv2hash.log`
- Log-Rotation
- Ringbuffer für Web-Konsole
- Live-Webkonsole unter `/system`
- Download des aktuellen Logs
- farbliche Log-Level-Darstellung

---

## API

### `GET /api/status`

Liefert den aktuellen Laufzeitstatus als sauber JSON-kompatible API-Antwort.

---

## Installation

PV2Hash wird über **GitHub Releases** verteilt.

Ein Release enthält mindestens:

- `pv2hash-<version>.tar.gz`
- `manifest.json`
- `SHA256SUMS`

### Neueste Release installieren

```bash
curl -fsSL https://raw.githubusercontent.com/phlupp/pv2hash/main/scripts/install_release.sh | sudo bash
```

### Bestimmte Version installieren

```bash
curl -fsSL https://raw.githubusercontent.com/phlupp/pv2hash/main/scripts/install_release.sh | sudo env TAG=<tag> bash
```

Beispiel:

```bash
curl -fsSL https://raw.githubusercontent.com/phlupp/pv2hash/main/scripts/install_release.sh | sudo env TAG=v0.2.2-build.4 bash
```

Der Installer soll:

- die gewünschte Release von GitHub laden
- Checksummen prüfen
- nach `/opt/pv2hash/releases/<version>/` entpacken
- einen `current`-Symlink setzen
- ein Python-`venv` anlegen
- Abhängigkeiten installieren
- einen `systemd`-Dienst einrichten und starten

---

## Entwicklung starten

### Lokaler Entwicklungsstart

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn pv2hash.app:app --host 0.0.0.0 --port 8000 --no-access-log
```

Danach ist die Oberfläche standardmäßig unter Port `8000` erreichbar.

---

## Release bauen

Lokales Release-Asset bauen:

```bash
chmod +x scripts/build_release.sh
bash scripts/build_release.sh
```

Erzeugt unter `dist/<version-slug>/` unter anderem:

- `pv2hash-<version-slug>.tar.gz`
- `manifest.json`
- `SHA256SUMS`

---

## Konfigurationsprinzip

PV2Hash verwendet eine persistente JSON-Konfiguration.

Merkmale:

- Konfiguration bleibt lokal
- Änderungen können zur Laufzeit übernommen werden
- mehrere Miner in einer gemeinsamen Struktur
- Prioritäten und Verteilstrategien sind konfigurierbar

---

## Geplante nächste Schritte

### Kurzfristig

- Braiins-Write-Logik weiter härten
- Status- und Fehlerbehandlung pro Miner verfeinern
- UI- und Runtime-Anzeige weiter verbessern
- Release-Installer finalisieren
- Update-Prüfung gegen GitHub Releases
- Self-Update-Funktion

### Mittelfristig

- weitere Miner-Treiber
- Controller-Feinschliff
- zusätzliche Datenquellen
- Batterie-Integration
- gezieltere Diagnose- und Debug-Ansichten

---

## Hinweise

PV2Hash ist weiterhin ein Entwicklungsprojekt.

Trotz funktionierender echter Miner-Anbindung sollte der produktive Einsatz immer mit Bedacht erfolgen. Messwerte, Profile, Quellverlust-Logik und Sicherheitsverhalten sollten vor einem unbeaufsichtigten Betrieb validiert werden.

---

## Repository

GitHub:

`https://github.com/phlupp/pv2hash`
