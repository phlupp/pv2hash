# PV-Prognose – Architektur und Entwicklung

**Stand 2026-10-19:** Die PV-Prognose ist produktiver Bestandteil
von PV2Hash; zuletzt wurde `r2026.10.19.1` mit wählbarer 15-/60-Minuten-
Auflösung veröffentlicht. Ursprung der Architektur ist der Prototyp
`feat/forecast-foundation`.

**Weiterführende Konzepte:** [Batterie-Ladeziel und Energieplaner im Shadow Mode](energy-planner.md),
[Prognoserevisionen und Qualitätsauswertung](forecast-quality.md) sowie
[lernende PV-Prognose im Shadow Mode mit lila Kurve](forecast-shadow-learning.md).
Die Prognosequalität wird ausschließlich informativ ausgewertet; der
Batterie-/Mining-Planer ist noch nicht implementiert und greift nicht
in die bestehende Minerregelung ein.

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
  `unixtime`, `timezone=auto`; drei Tage, wahlweise 15 oder 60 Minuten.
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

## Teststatus des ursprünglichen Prototyps (historisch)

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

## Tagesenergie in der Prognoseansicht

Über dem Tagesdiagramm werden für den gewählten Standort-Kalendertag
**Erwartete Tagesenergie** und **(bisher) erzeugte PV-Energie** in kWh
angezeigt. Beide Werte stammen direkt aus demselben Leseaufruf
`GET /api/forecast/day` wie die Zeitreihen. Die Buttons
**Zurück / Heute / Vor** wechseln die beiden Energiewerte gemeinsam
mit den Kurven; keine zusätzlichen Wetteranbieter-Abfragen nötig.

Die Berechnung integriert zeitlich aufeinanderfolgende Leistungswerte
nach der Trapezregel in kWh (W · h / 1000). Nur nachgewiesene gültige
Zeitintervalle zählen:

- **Prognose:** Aus den gespeicherten stündlichen Gesamtprognosen,
  maximal 90 Minuten Abstand pro Intervall. Für die letzte Stunde
  kann der Prognosepunkt **genau am folgenden Tagesbeginn** genutzt
  werden; dieser Punkt erscheint nicht im Diagramm des ausgewählten
  Tages. Fehlende Prognosepunkte führen zu einer gekennzeichneten
  **Teilprognose**, nicht zu extrapolierten Stunden.
- **Ist-Erzeugung:** Aus den tatsächlichen Rohmessungen des lokalen
  10-Sekunden-DataLoggers, ausschließlich bei `pv_quality="live"`.
  Maximal 35 Sekunden Abstand werden integriert. Ein Offline-/DEGRADED-
  Abschnitt, eine Datenlücke oder ein erkennbarer SMA-NaN-Fehler
  **unterbricht** die Berechnung. Eine gültige `0 W` ist dagegen
  echte Nullerzeugung. Geringfügig negative gültige Leistung wird
  nur für die energetische Bilanz auf 0 begrenzt; die Kurve bleibt
  unverändert.
- **Fehlende Daten:** Ohne ein einziges vollständig belegtes Intervall
  liefert die API `kwh: null` und die GUI zeigt `—`, nicht 0 kWh.
  Bei unvollständigen Tagen erscheinen Hinweis und Datenabdeckung.
  Eine vollständige Tagesenergie darf nur bei hinreichend vollständiger
  Zeitabdeckung behauptet werden.
- **Zeitzonen:** Integration erfolgt über UTC-Zeitstempel innerhalb
  der bestehenden standortbezogenen Tagesgrenzen; Sommer-/Winterzeit
  mit 23 bzw. 25 Stunden ist berücksichtigt.

Zusatzobjekt der Tages-API:

```json
"energy": {
  "forecast": {"kwh": 18.4, "coverage_hours": 24, "day_hours": 24, "complete": true},
  "actual": {"kwh": 11.7, "coverage_hours": 13.5, "day_hours": 24, "complete": false}
}
```

*Die Zahlen im Beispiel sind illustrativ, keine echten Messwerte.*
Der Endpoint ist weiterhin rein lesend, legt keine neuen Datenreihen
an und greift nicht in die Minerregelung ein. Für die Prognose werden
historische Zeitpunkte nicht nachträglich überschrieben; die bereits
bekannten Altwerte mit unplausibler negativer PV-Erzeugung sind auch
von der Ist-Energieberechnung ausgeschlossen. Es werden keine
bereinigten Ersatzmessungen in SQLite oder InfluxDB geschrieben.


## Prognoseauflösung und Abrufintervall getrennt (Folgeentwicklung)

Die Open-Meteo-Prognose unterscheidet zwischen **Auflösung** und
**Abrufhäufigkeit**:

- **Prognoseauflösung:** wahlweise 15 oder 60 Minuten, standardmäßig 15.
  Im Viertelstundenmodus wird
  `minutely_15=global_tilted_irradiance_instant` vom Anbieter abgerufen;
  im Stundenmodus weiterhin `hourly`. Echte UTC-Zeitpunkte und
  generatorbezogene Ausrichtung werden beibehalten. Fehlende Punkte
  interpoliert PV2Hash nicht.
- **Automatischer Abruf:** unveränderlich alle 15 Minuten auf
  gemeinsamen UTC-Slots. Das ehemals einstellbare Intervall
  (15 bis 360 Minuten, historischer Standard 30) ist entfallen.
  Die GUI zeigt den festen Takt als Information.

**Bestandsinstallationen:** Bereits gespeicherte Abrufintervalle
bleiben historisch in der Konfiguration vorhanden, werden aber beim
Abruf ignoriert. Beim nächsten Speichern der Prognoseeinstellungen
wird der Legacy-Wert auf 15 normalisiert. Keine Änderung historischer
Prognose- oder Qualitätswerte. Siehe
[Prognosequalität und UTC-Takt](forecast-quality.md).

**Speichervertrag:** Das bisherige `forecast_points`-Schema und die
Werte in W bleiben unverändert. Ein vollständiger Anbieterabruf wird
weiterhin atomar pro Generatorfläche, Wechselrichter und Gesamtergebnis
abgelegt. Ausschließlich **zukünftige** Zeitpunkte werden aktualisiert;
ältere Werte bleiben zur Qualitätsevaluierung unverändert. Bei einem
Wechsel von 15 auf 60 Minuten werden nach einem *erfolgreichen*
Anbieterabruf nur die bereits gespeicherten **zukünftigen**
Viertelstundenpunkte außerhalb des neuen Stundenrasters entfernt.
Vergangene 15-Minuten-Werte bleiben erhalten. Fehlende Werte werden
nicht interpoliert. Die kWh-Integration erkennt die Auflösung der
historisch gespeicherten Punkte, und für den aktuellen/zukünftigen
Tag beachtet sie die gewählte Auflösung, damit Datenlücken nicht
als Vollständigkeit gelten. Die standortbezogene Tageszählung
berücksichtigt 23-/25-Stunden-Tage bei der Zeitumstellung.

**Tests auf `testvm01`:** Simulierte HTTP-Antworten für beide API-Felder,
Konfigurationsspeicherung/-validierung und Rückwärtskompatibilität;
atomare Speicherung, Zukunftsbereinigung ohne Vergangenheitsänderung,
Energieberechnung mit Lücken und Sommer-/Winterzeit. Ein echter,
lesender Open-Meteo-Test mit neutralen Koordinaten in Deutschland lieferte
288 Punkte in 15-Minuten- und 72 Punkte in 60-Minuten-Auflösung für
je drei Tage. Es wurden dabei keine persönlichen Standortdaten oder
API-Schlüssel verwendet. Die Minerregelung wird nicht verändert.

### Aktueller Teststatus – 15-Minuten-Erweiterung (2026-10-19)

- Vollständige Python-Regressionssuite: **149 Tests bestanden**.
- Frontend: **55 Tests bestanden**, TypeScript/Vite-Build erfolgreich.
- Echte Open-Meteo-Leseabfragen mit neutralen Koordinaten: **288 ×
  15-Minuten-Werte bzw. 72 × Stundenwerte in drei Tagen**.
- Ende-zu-Ende-Test mit zwei synthetischen Generatorflächen an zwei
  Wechselrichtern, echter API-Antwort und **temporärer SQLite-Datei**:
  für morgen 96 Gesamtprognosepunkte und vollständige kWh-Integration.
  Temporäre Dateien werden wieder entfernt; weder aktive Testinstanz-
  Daten noch produktive Hashpi-Konfiguration wurden verändert.
- Umschalten 15 ↔ 60, Datenlücken, UTC-Zeitstempel, 23-/25-Stunden-
  Tage, historische Unveränderlichkeit und Validierungsfehler getestet.
