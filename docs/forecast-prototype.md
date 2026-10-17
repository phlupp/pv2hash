# PV-Prognose – isolierter Architekturprototyp

**Status:** Experiment / separater Draft-Branch `feat/forecast-foundation`.
Basiert auf dem post-0.9.5 Draft-PR #28, gehört **nicht automatisch**
zum unmittelbar nächsten PV2Hash-Release. Auf produktiven Hashpis
nicht aktiviert und nicht installiert.

## Fachliches Modell

- **Prognoseseite:** vollständiger Standort-Kalendertag von 00:00 bis
  24:00 lokaler Zeit. Navigation beliebig zurück, höchstens einen
  Kalendertag vorwärts (morgen). Der aktuelle Tag enthält eine
  kontinuierlich wachsende gelbe Ist-Kurve (nur gemessene
  `pv_quality=live`-Werte), darüber eine graue gestrichelte
  Prognosekurve.
- **Zeitmodelle:** UTC-Zeitstempel in SQLite, tägliche Grenzen
  anhand der Standort-IANA-Zeitzone. Damit funktionieren auch
  23-/25-Stunden-Tage bei Sommer-/Winterzeitwechsel.
- **Unveränderliche Vergangenheit:** Für jede Prognoseaktualisierung
  wird die Erzeugung für einzelne WR und insgesamt nur für
  `valid_at > Zeitpunkt der Aktualisierung` ersetzt/ergänzt.
  **Vergangene oder genau aktuelle Prognosepunkte bleiben unverändert,
  auch bei neuen Anbieterantworten und nach einem Tageswechsel.**
  Es gibt keine nachträgliche Rekonstruktion nicht gespeicherter
  Prognosen durch Wetter-Reanalyse. Wenn zum Zeitpunkt der ersten
  Aktivierung frühere Stunden noch nicht protokolliert wurden,
  fehlen diese Prognosepunkte – die UI erfindet sie nicht.
- **Trennung:** `pv2hash/forecast` enthält eigenständige
  Anbieter-Adapter, Modellrechnung, SQLite-Store und
  Aktualisierungslogik. Der bestehende PV-DataLogger bleibt
  unverändert. Mining-Regler/Optimierer lesen keine Prognosen.

## Wechselrichter und Generatorflächen

Anlagendaten liegen innerhalb der bestehenden PV-Geräte:
`pv.devices[].generators[]`:

| Name | Bedeutung |
| --- | --- |
| `id`, `name`, `enabled` | stabile Generatorflächen-Identität / Aktivierung |
| `kwp` | installierte Generatorleistung in kWp |
| `tilt` | Modulneigung in Grad (0–90) |
| `azimuth` | Ausrichtung, 0 Süd, -90 Ost, +90 West |
| `loss_percent` | erster pauschaler Verlustfaktor |

Pro Wechselrichter können mehrere unabhängige Ausrichtungen erfasst
werden (z. B. Ost/West an verschiedenen MPPTs). Die Prognose bleibt
auch dann aktiv, wenn die aktuelle Modbus-Messung eines WR offline ist.
Die Messgeräte-ID verbindet Komponenten, Prognose und spätere Diagnose.

Das erste vereinfachte physikalische Modell:

`P[W] = min(kWp * 1000, max(0, GTI_inst[W/m²]) / 1000 * kWp * 1000 * (1 - loss_percent/100))`

Das ist **keine validierte Ertragsprognose**: Temperatur, Verschattung,
Wechselrichter-Clipping und jahreszeitliche Effekte bleiben zunächst
unberücksichtigt. Keine automatische Kalibrierung im Prototyp.

## Prognosetreiber

Aktueller Treiber: `open_meteo`. Die API-Metadaten
(`config_fields()`) definieren die GUI-Felder, einschließlich
Bedingung für API-Key. Die React-GUI rendert diese Felder generisch.

- Frei: `https://api.open-meteo.com/v1/forecast`,
  kein API-Schlüssel; beachten: freie Nutzung ist nichtkommerziell.
- Kunden-API: `https://customer-api.open-meteo.com/v1/forecast`
  mit `apikey` in der Anfrage. Ein gespeicherter Schlüssel wird
  im Settings-GET niemals ausgegeben.
- Pro Generatorfläche wird `global_tilted_irradiance_instant`
  mit eigener Neigung/Ausrichtung abgerufen. Zeitformat
  `unixtime`, `timezone=auto`; zunächst 3 Tage, stündlich.
- Alle aktiven Generatorflächen müssen dieselben UTC-Zeitpunkte
  liefern, bevor ein Gesamtwert berechnet wird. Je Zeitpunkt
  werden erst Generatoren zu WR, dann WR zu Gesamt prognostiziert.
  Fehlt eine Generatorfläche, wird kein Teil-Gesamtwert publiziert.
- Anbieter-Updates standardmäßig **deaktiviert**, erst nach
  Konfiguration aktivierbar; Intervall 15–360 Minuten und
  manuelles Aktualisieren. Kein automatischer Provider-Zugriff
  allein durch Öffnen der Seite.

## Speicherung und API

Separate Datei `data/forecast.sqlite`, Tabelle `forecast_points`.
Primärschlüssel `(valid_at, device_id)`. Zusätzlich
`issued_at`, `provider`, `power_w`.
`device_id='__total__'` ist das einzige Chart-Ergebnis;
andere Zeilen halten die Prognose **pro Wechselrichter**
für spätere Auswertungen fest (keine Grafikkurve je Gerät).

Alle Gesamtergebnisse einer Aktualisierung werden in **einer
Transaktion** persistiert. Vor dem Speichern wird die Zeitgrenze
erneut geprüft. Keine Änderung bestehender historischer Prognosen.

API-Endpunkte:
- `GET /api/forecast/settings`: Treiberfeldschema,
  aktuelle nichtgeheime Einstellungen, WR/Generatorflächen
  und Aktivierungsstatus
- `POST /api/forecast/settings`: validierte Einstellung
  einschließlich Generatorflächen, **kein Runtime-Reload**
  der Mining-/Quellen-/Controller-Dienste
- `POST /api/forecast/refresh`: vollständiger Abruf
  und **nur zukünftige** Zeitpunkte
- `GET /api/forecast/day?date=YYYY-MM-DD`: Tagesgrenzen,
  gespeicherte Gesamtprognose und tatsächliche
  5-Minuten-Mittelwerte der `live`-PV-Historie.

## Teststatus

- Acht dedizierte Prognosetests für unveränderliche Vergangenheit,
  aktualisierbare Zukunft, pro-WR-Aggregation, mehrere Generatorflächen,
  separate Ist-Messung, bezahlten/kostenlosen API-Treiber,
  Credential-Masking und Sommer-/Winterzeit.
- Gesamte Backend-Test-Suite: 109 bestanden.
- Frontend: 27 Tests, TypeScript und Vite-Build erfolgreich.
- Echter Open-Meteo-GET mit **neutralen öffentlichen Testkoordinaten**
  (nicht Nutzerstandort): 72 Prognosepunkte und korrekte Zeitzone.
- `testvm01`: Preview auf Port 5175; existierender Entwicklungs-Core
  mit Backup der zwei geänderten Module und der Konfiguration
  getestet, 19 echte Ist-Messpunkte vom aktuellen Tag.
  **Keine Generatorflächen oder API-Zugangsdaten ohne Nutzerwahl
  aktiviert.**
- Browser: Heute/Morgen-Navigation, eingeklappte Einstellungen,
  Generatorfläche hinzufügen/verwerfen, 390px ohne horizontalen
  Überlauf und ohne JS-Konsolenfehler.

## Abgrenzung / nächste Diskussion

Zuerst Werte für die echten PV-Generatorflächen erfassen und wenige
Tage Prognosen mit Ist-Erzeugung vergleichen. Anschließend prüfen,
ob das vereinfachte Modell ausreichend plausible Kurven liefert.
Erst danach über weitergehende Kalibrierung, genauere
PV-Modelle, optionale Forecast-Revisionen oder eine
Regelungsnutzung entscheiden. Der Produktivrollout wartet ausdrücklich.
