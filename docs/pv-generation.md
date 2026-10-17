# PV-Erzeugung: Mehrere Wechselrichter und atomare Abfragerunden

Stand: Entwicklungsbranch `feat/post095-gui-exporter-identity`, **nach dem
Brückenrelease v0.9.5**; nicht freigegeben für Produktivgeräte.

## Ziel und Datentrennung

PV2Hash bildet eine **PV-Erzeugung gesamt** aus beliebig vielen
aktivierten PV-Geräten (bis maximal 64). Jedes Gerät besitzt
`id`, `uuid`, `name`, `driver`, `enabled`, `settings`.
Die gemeinsame Geräteverwaltung besitzt `pv.enabled` und
`pv.poll_interval_ms`.

Heute ist `pv_modbus` der einzige unterstützte Treiber; weitere
Treiber werden über die zentrale `build_pv_device()`-Factory ergänzt.
Das bestehende Modbus-Schema pro Gerät wird vollständig wiederverwendet:
Host/IP, Port, Unit-ID, Anfrage-Timeout, Registerart, eine 0-basierte
Registeradresse, Datentyp, Byte-Reihenfolge und Faktor. Auch bei
32-Bit-Werten wird nur eine Register-Startadresse konfiguriert.

**Alle PV-Geräte sind reine Messgeräte.** Der Controller erhält weder
Einzelmessungen noch die Summe als Regeleingang.

## Atomare Messrunde

`PvGenerationGroup` startet pro Runde alle aktiven Geräteabfragen
gleichzeitig. Jedes Gerät besitzt einen eigenen Request-Timeout.
Das Ergebnis der gesamten Runde wird unabhängig von der Fertigstellung
einzelner Abfragen **erst nach Abschluss aller Reads/Timeouts** durch
einen einzigen Snapshot-Tausch veröffentlicht:

- Während der Runde bleiben die vorherige Gesamtleistung,
  Gesamtqualität, Rundennummer und sämtliche Gerätestatus unverändert.
- Anschließend wird ein vollständiger `PvRound` publiziert:
  `number`, `updated_at`, `power_w`, `quality`, `live_count`,
  `enabled_count`, `devices[]`.
- **LIVE**: alle aktivierten Geräte melden gültige Werte;
  ihre Summe gilt als vollständige PV-Erzeugung.
- **DEGRADED**: mindestens ein Gerät LIVE, mindestens ein anderes
  ausgefallen/abgelaufen. Die Summe der gültigen Werte wird mit
  sichtbarer Kennzeichnung als **Teilsumme** publiziert.
- **OFFLINE**: kein aktives Gerät liefert gültige aktuelle Werte.
  Gesamtleistung ist `null`, nicht 0.
- **Deaktivierte Geräte** zählen weder für die Summe noch für den
  Gesamtstatus; ohne aktivierte Gruppe/Teilnehmer verschwindet die
  PV-Kachel aus dem Dashboard.
- **0 W ist gültig**, wenn es als LIVE-Messung geliefert wird.
- Spät eintreffende Antworten einer zeitlich abgelaufenen Runde
  können keinen alten Wert nachträglich einfügen.
- Die Abfragerunden sind pro Instanz serialisiert. Bei Runtime-Reload
  wird der alte Sammler geschlossen; begonnene Abfragen dürfen
  danach keine neuen Daten mehr veröffentlichen.

Das Dashboard, `/api/sources/status`, `/api/dashboard/status`
und der Runtime-Snapshot der Aufzeichnung lesen ausschließlich den
letzten abgeschlossenen Aggregat-Snapshot. Es gibt keine
zwischengeschaltete Addition in der GUI.

## Anzeige und Einrichtung

Unter **Messungen**:

1. Die Karte **PV-Erzeugung gesamt** zeigt Summe, Qualität und
   Anzahl aktiver/LIVE-Geräte sowie das gemeinsame Abfrageintervall.
2. **Wechselrichter hinzufügen** erzeugt ein ungespeichertes,
   zunächst deaktiviertes Gerät. Die neue Karte erhält die
   eindeutige ID; Aktivierung und Verbindungsdetails liegen in
   dessen Treiberformular.
3. Jede Gerätekarte erlaubt Name, Aktivieren/Deaktivieren,
   Treiberwahl (aktuell Modbus TCP) sowie dessen volle Einstellungen.
   **Gerät entfernen** ist zunächst ebenfalls eine Vorschau.
4. **Alle Änderungen speichern** übernimmt die gesamte Konfiguration.
   **Verwerfen** stellt den gespeicherten Stand wieder her.

Die tatsächlichen Einzelwerte/-qualitäten sind nur Live-Diagnosedaten
auf der Messungen-Seite; für den DataLogger und Exporter werden
keine zusätzlichen Gerätezeitreihen angelegt.

## Interner DataLogger und InfluxDB

Der bestehende `history_samples`-Datensatz (Schema v6) bleibt
unverändert strukturiert:

| Feld | Bedeutung |
|---|---|
| `pv_power_w` | **eine** Gesamtleistung in Watt oder Teilsumme |
| `pv_quality` | `live`, `degraded`, `offline`, `stale`, `disabled` |

Der InfluxDB-Exporter schreibt im vorhandenen Measurement
`pv2hash_system` genau diese beiden Felder. Bei OFFLINE fehlt
`pv_power_w` (NULL in SQLite); eine Teilsumme wird mit
`pv_quality="degraded"` übertragen. Für künftige Prognose-Analysen
lassen sich unvollständige Zeitbereiche dadurch sicher ausschließen.

## Migration und Rollback

Eine bestehende 0.9.5-Konfiguration mit
`pv.type="pv_modbus"`, `pv.settings` und `pv.enabled` erzeugt
idempotent **genau ein** Gruppenmitglied. Host, Register,
Faktor, Unit-ID, Anfrage-Timeout und der bisherige UUID werden
beibehalten. Solange die neue Gerätegruppe nicht bearbeitet und
gespeichert wird, bleiben `pv.type="pv_modbus"` und die alten
Einstellungen zur vereinfachten Rückkehr nach 0.9.5 erhalten.
Sobald mehrere Geräte konfiguriert sind, ist diese Rückwärtsfähigkeit
naturgemäß nicht mehr gegeben.

Große Änderungen an der Produktionskonfiguration unterbleiben
bis zur regulären Freigabe und dem Update über GitHub.

## Abnahme

- Zwei gleichzeitige Geräte: kein Zwischenwert und kein
  Qualitätssprung während einer laufenden Runde.
- Timeouts pro Gerät: LIVE / DEGRADED / OFFLINE, danach atomare
  Veröffentlichung.
- Veraltete Threads können den vorangegangenen bzw. nächsten
  Snapshot niemals überschreiben.
- Runtime-Reload während einer Runde verwirft alte Ergebnisse.
- NULL vs. echte 0 W, deaktivierte Geräte, additive Migration,
  idempotente Konfiguration, eindeutige IDs.
- `testvm01`: Bestehender echter Einzel-WR wurde ohne
  Parameterverlust als erstes Gruppenmitglied übernommen.
  GUI-Vorschau Hinzufügen / Einstellungen / Entfernen / Verwerfen
  getestet und nicht persistent gespeichert.
