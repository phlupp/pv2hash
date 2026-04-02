# PV2Hash

PV2Hash ist ein lokales Steuerungs- und Monitoring-System für PV-Überschussnutzung mit ASIC-Minern.

Ziel ist es, überschüssige Energie aus einer PV-Anlage automatisiert in Bitcoin-Mining umzuwandeln. PV2Hash läuft leichtgewichtig auf Debian oder Raspberry Pi, bietet eine Weboberfläche zur Konfiguration und ist modular für verschiedene Datenquellen und Miner-Treiber aufgebaut.

## Status

Aktueller Stand: `0.1.0+build.2`

PV2Hash befindet sich in aktiver Entwicklung. Der aktuelle Stand ist ein funktionierender technischer MVP mit echter SMA-Meter-Anbindung, Weboberfläche, Logging-Grundlage und Multi-Miner-Struktur.

---

## Hauptziele des Projekts

- lokale Nutzung von PV-Überschuss für Mining
- keine Cloud-Abhängigkeit
- modulare Unterstützung für verschiedene Energiequellen
- modulare Unterstützung für verschiedene Miner / Firmwares
- Weboberfläche für Betrieb und Konfiguration
- Logging, Diagnose und spätere Self-Update-Fähigkeit
- später grobe und feine Regelstrategie

---

## Bereits umgesetzt

### Weboberfläche
- dunkles, modernes Dashboard
- Seiten für Dashboard, Quellen, Miner, Einstellungen und System
- Live-Anzeige der wichtigsten Betriebsdaten
- Formularseiten zur Konfiguration

### Konfiguration
- persistente Konfiguration über JSON-Datei
- Runtime-Reload nach Änderungen
- mehrere Miner in der Konfigurationsstruktur
- auswählbare Verteilstrategie (`equal`, `cascade`)
- vorbereitete Policy-Modi (`coarse`, später `fine`)

### Datenquelle / SMA
- funktionierender `sma_meter_protocol` Source-Adapter
- Multicast-Empfang über `239.12.255.254:9522`
- Auswahl einer lokalen Interface-IP
- optionaler Geräte-IP-Filter
- korrekte Auswertung des SMA Meter Protocol (`0x6069`)
- korrekte Vorzeichenlogik:
  - negativ = Einspeisung
  - positiv = Netzbezug

### Zustandsbewertung der Quelle
- `live`
- `stale`
- `offline`
- konfigurierbare Schwellwerte:
  - Packet Timeout
  - Stale nach
  - Offline nach

### Miner-Struktur
- Multi-Miner-Grundlage
- Simulator-Miner
- erster Braiins-Treiber als Basis
- Prioritäten und Verteilmodi vorbereitet

### Logging
- Python-Logging als zentrale Basis
- Datei-Logging unter `data/logs/pv2hash.log`
- Log-Rotation
- Ringbuffer für Web-Konsole
- Live-Konsole unter `/system`
- Download des aktuellen Logs
- farbliche Level-Darstellung in der Web-Konsole

---

## Geplante nächste Schritte

### Regelung
- Verhalten bei `stale`
- Verhalten bei `offline`
- Strategien bei fehlenden Messwerten:
  - sofort aus
  - letzten Wert halten
  - letzten Wert für definierte Zeit halten
  - später Fallback-Profil

### Miner-Steuerung
- echte Profilumschaltung für Braiins OS
- Verbindungstest / Statusabfrage pro Miner
- bessere Kaskadierung anhand echter Leistungsbudgets

### Weitere Quellen
- Modbus TCP als weitere Hauptquelle
- spätere Batterie-Integration:
  - Ladeleistung
  - Entladeleistung
  - SOC

### Debug / Diagnose
- pro Source-Adapter optionales Debug-Logging
- später gezielter Debug-Modus pro Komponente
- ggf. separate Diagnoseansichten

### System / Release
- Versionserkennung im UI
- GitHub-basierte Versionsprüfung
- Self-Update-Funktion
- Installer-Skript
- systemd-Service

---

## Architektur

PV2Hash ist modular aufgebaut:

- `sources/` → Energiequellen
- `miners/` → Miner-Treiber
- `controller/` → Regel- und Verteillogik
- `templates/` / `static/` → Weboberfläche
- `logging_ext/` → Logging-Basis
- `config/` → Konfiguration
- `runtime.py` / `services.py` → Laufzeitstatus und Neuladen

---

## Aktuell unterstützte Quellen

- `simulator`
- `sma_meter_protocol`

## Aktuell unterstützte Miner-Treiber

- `simulator`
- `braiins` (Grundgerüst / Basis)

---

## Starten (Entwicklung)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn pv2hash.app:app --host 0.0.0.0 --port 8000 --no-access-log
```

---

## Hinweise

PV2Hash ist aktuell ein Entwicklungsprojekt. Die Regelung echter Miner sollte erst aktiviert werden, wenn die Messdaten und Sicherheitslogik sauber validiert sind.