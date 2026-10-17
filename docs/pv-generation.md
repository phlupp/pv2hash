# PV-Erzeugung – Modbus TCP (Core 0.9.5)

Das PV-Erzeugungsmodul ist eine **optionale, rein lesende Energiequelle** mit
eigener Rolle `pv`. Es ist **nicht Teil der Regelung**: Mining-Profile,
Batterie- und Netzbezug bleiben unverändert. Eine nicht erreichbare PV-Quelle
darf daher keine Schaltentscheidung verzögern oder verändern.

## Bedienung

Unter **Messungen → PV-Erzeugung**:

1. Messprofil **Modbus TCP PV-Erzeugung** wählen.
2. Verbindungsdaten eingeben: Host/IP, Port (Vorgabe 502),
   Unit-ID (Vorgabe 1), Intervall (1000–60000 ms, Vorgabe 5000),
   Timeout (0,2–5 s, Vorgabe 1).
3. **Genau einen Messwert** `PV-Leistung` konfigurieren:
   0-basierte Register-Startadresse (0–65535), Holding (FC03)
   oder Input (FC04), Datentyp `uint16`, `int16`, `uint32`,
   `int32` oder `float32`, Byte-Reihenfolge Big/Little Endian,
   Umrechnungsfaktor auf Watt.
4. **PV-Messung aktiviert** einschalten und speichern.
5. Auf dem Dashboard im Energiebereich erscheint die PV-Erzeugung
   in Watt mit einer eigenen Messqualität (LIVE, OFFLINE, KEINE DATEN).
   Bei nicht konfigurierter oder deaktivierter Quelle wird die Kachel
   vollständig ausgeblendet; eine konfigurierte Offline-Quelle bleibt
   mit entsprechendem Status sichtbar.

Bei 32-Bit-Datentypen werden vom Gerät automatisch **zwei
aufeinanderfolgende Register** gelesen, obwohl nur **eine Startadresse**
konfiguriert wird. Es gibt weder mehrere PV-Messwerte noch
gerätespezifische Profile. Für Geräte mit Leistung in kW etwa Faktor
`1000` konfigurieren; mit negativem Vorzeichen ggf. Faktor `-1`.

## Architektur

- `pv2hash/sources/modbus_register.py` kapselt den vorhandenen
  lesenden Modbus/TCP-Registerdecoder als gemeinsame Grundlage für
  Batterie und PV (ausschließlich Modbus FC01/02/03/04,
  PV-Treiber nutzt ausschließlich FC03/04).
- `pv2hash/sources/pv_modbus.py` enthält den eigenständigen
  PV-Treiber samt Schema, Validierung und Qualitätszustand.
- `pv2hash/factory.py` baut den Treiber nur bei aktivierter PV-Messung.
- `pv2hash/services.py` verwaltet ihn getrennt von Netz- und
  Batteriequellen und liefert sein Formular über die bestehende
  **generische** `/api/sources/gui`-Schnittstelle.
- `pv2hash/app.py` fragt ihn in einer **eigenen asynchronen
  Messschleife** ab (blockierende Socketzugriffe in einem
  Thread). Die Regler-Schleife empfängt **keinen** PV-Wert.
- `/api/dashboard/status.cards.pv` enthält `power_w`,
  `quality`, `enabled`, `source`, `updated_at`;
  die vorhandene React-Dashboard-Kachel verwendet ausschließlich
  live markierte PV-Werte.
- Kein Modbus-Schreibvorgang. Bei Abfragefehler wird der zuletzt
  gemessene Zahlenwert verworfen und `OFFLINE` ausgegeben.
  Andere Messquellen bleiben funktionsfähig.
- Alte Konfigurationen erhalten durch Merge einen standardmäßig
  deaktivierten PV-Block. Bestehende Legacy-Formulare ohne
  `pv_type` verändern eine konfigurierte PV-Messquelle nicht.

## Tests vor Release

- Automatisierte Tests: reale lokale Modbus/TCP-Emulation für
  FC03/FC04, 16/32-Bit und Skalierung; ungültige Verbindung,
  Registerbereich, Faktor und Offline-Fall;
  Batterie-Modbus-Regressionsprüfung.
- Entwicklungs-Core und React-Vorschau auf `testvm01`.
  Auf dem produktiven Hashpi wurde nichts verändert.
- Vor produktivem Anschluss benötigen wir ausschließlich die
  Modbus-Registerangaben des Wechselrichters. Die Einstellungen
  sind bewusst herstellerunabhängig.

### Durchgeführte Integrationsprüfung auf testvm01

- Fake-Modbus-Server auf localhost, FC04, Registeradresse 470,
  `uint16` mit **3427 W**. Vollständig getrennte Test-Instanz:
  Live-Polling, Dashboard-`cards.pv.power_w=3427`, PV-Quellenkarte
  und Status `LIVE` bestätigt.
- Parallel `/api/status.snapshot.pv_power_w == null` geprüft:
  die PV-Messung gelangt **nicht** in die Reglerberechnung.
- Live-Geräte während der Entwicklung nicht abgefragt;
  produktive Konfiguration nicht verändert.
- GUI in Vite: dynamische Feldschema-Vorschau nach Auswahl
  von `pv_modbus`, ohne Speichern; anschließend verworfen.
  Dashboard- und Messungen-Seite bei 320 px ohne horizontalen Überlauf,
  keine JavaScript-Konsolenfehler.
- 9 gezielte PV-Treiber-/API-Kompatibilitätstests, insgesamt
  **76 Python-Tests** und **25 Frontend-Tests** bestanden.
