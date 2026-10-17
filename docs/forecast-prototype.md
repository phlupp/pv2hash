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

### Diagrammeinheit

Die tatsächlichen PV-Messungen und die Gesamtprognose liegen in API,
Modellrechnung und SQLite weiterhin in **W** vor. Nur das
Tagesdiagramm rechnet beide Kurven in **kW** um (`W / 1000`).
Y-Achse (`kW`), Achsenwerte und Tooltip nutzen durchgängig kW,
auch unterhalb von 1 kW (z. B. `0,25 kW`). Das Diagramm skaliert
weiterhin automatisch auf die vorhandene Leistung; nur die
**Einheit** ist festgelegt.

### Automatische Anzeigeaktualisierung und Navigation

Die React-Prognoseseite lädt den angezeigten heutigen oder morgigen Tag
alle **30 Sekunden** neu, analog zum Live-DataLogger, ohne Seiten-Reload.
Bei historischen Tagen sind keine regelmäßigen Abrufe nötig, da die
Vorhersage für vergangene Zeitpunkte unveränderlich ist. Der
Einstellungs-/Aktualisierungsstatus wird unabhängig davon alle 60 s
nachgeladen. Bei erneutem Aktivieren des Browserfensters greift
zusätzlich das Standard-Refetch-on-Focus von React Query.

Neben „Vor“ führt „Heute“ zur Live-Tagesansicht zurück und wechselt
zur API-Abfrage *ohne festes Datum*. So wird um Mitternacht beim nächsten
Refresh automatisch der neue aktuelle Standort-Kalendertag angezeigt.
Auch wenn „Vor“ oder „Zurück“ bei heute landet, wird wieder die
Live-Abfrage benutzt. Die Navigation erlaubt maximal morgen.
Die regelmäßigen Anzeigeabrufe sind **keine** neuen Wetterabfragen:
Diese laufen weiterhin im konfigurierten Prognoseintervall serverseitig.

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

### Einrichtungs- und Fehlerzustand (Preview-Ergänzung)

Solange nicht **jeder konfigurierte Wechselrichter** mindestens eine
aktive Generatorfläche besitzt, zeigt `/ui-next/prognose` eine
Einrichtungsaufforderung mit den fehlenden Geräten und direktem Öffnen
der Generator-Einstellungen. Die Forecast-Aktivierung wird bei
unvollständiger Abdeckung auch serverseitig abgelehnt (keine
irreführende Teilprognose als Gesamtanlage).
Es werden keine kWp-/Ausrichtungs-/Neigungswerte geraten und weder der
Provider noch die Mining-Regelung automatisch aktiviert. Der sichtbare
Status unterscheidet aktiv/inaktiv und zeigt nach dem ersten Abruf den
letzten erfolgreichen Zeitpunkt an.

Ein fehlerhafter manueller oder automatischer Prognose-Abruf hält den
Fehlertext und Zeitstempel in der separaten `forecast_meta`-Tabelle fest.
`GET /api/forecast/settings.history` liefert `last_failure` und
`last_error` bis zur nächsten erfolgreich gespeicherten Prognose.
Der Anbieteradapter maskiert Schlüssel und sensible Requests bereits
vor diesem Schritt. Fehler erzeugen keine Prognosepunkte und verändern
keine historische Kurve. Die Anzeige verschwindet erst nach Erfolg.

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
