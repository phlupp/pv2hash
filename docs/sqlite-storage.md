# Lokaler SQLite-Status und 365-Tage-Prognosearchiv

**Gültigkeit:** Neue Entwicklung zu PR #43 auf `testvm01`; alle produktiven
Hashpis bleiben bis zu einem freigegebenen Release unverändert.

## Aufbewahrung und automatische Wartung

- Alle erfolgreichen PV-Prognosen werden weiterhin append-only nach Ausgabezeit
  in `forecast_revisions` und `forecast_revision_points` archiviert.
- **365 Tage Aufbewahrung** nach `issued_at` (Ausgabezeit der Prognose); der
  Grenzwert selbst wird nicht gelöscht (`issued_at < now-365d`).
- Bei Programmstart und danach **stündlich** sucht der Hintergrundprozess nach
  alten Revisionen. Er entfernt je **maximal 12 Revisionen pro kleiner
  Transaktion** die Kindpunkte und den Revisionskopf; alte Revisionen lassen
  sich nach einer Unterbrechung ohne doppelten Effekt weiterbereinigen.
- **Nicht** gelöscht werden die bisherige Live-/Historien-Tageskurve
  (`forecast_points`), `forecast_quality_samples`, DataLogger-Rohdaten,
  Miner-/Controllerereignisse oder das Exporter-Checkpoint.
- Datenbankdatei schrumpft nach SQL-DELETE meist **nicht**. Freie SQLite-Seiten
  werden für neue Prognoserevisionen wiederverwendet. Kein automatisches
  `VACUUM` oder zeitkritischer zusätzlicher Controllerzugriff.
- Qualitätsauswertungen bleiben bis auf Weiteres dauerhaft, weil ihr
  Umfang klein ist (höchstens rund 23 MiB/Jahr pro Instanz für vier Horizonte).
- Bei Fehlern der Wartungsaufgabe wird geloggt und beim nächsten Durchlauf
  erneut versucht; der normale Minercontroller ist davon unabhängig.
- Für einen längeren Produktionsbetrieb bei umfangreichen Anlagen sollten
  Backupgröße, SD-Kartenreserven und Wartungsdauer mitgemessen werden.

## SQLite-Status auf der Systemseite

Ein eigener unabhängiger Datensammler liest **ohne SQL-Schreibzugriff** auf die
überwachten Datenbanken deren Metadaten und Dateigrößen:

| Datenbank | Dateipfad | Gegenstand |
| --- | --- | --- |
| PV-Prognose | `data/forecast.sqlite` | Vorhersagen, Revisionen, Qualität |
| Data Logger | `data/history.sqlite` | Lokale Mess-/Minerhistorie |
| Exporter | `data/exporters.sqlite` | Export-Checkpoints |

Für jede Datei werden die sichtbare Dateigröße, zusätzliche `-wal`-Größe,
`PRAGMA page_count`, `PRAGMA page_size` und `PRAGMA freelist_count`
ausgelesen. Bei der Prognosedatenbank werden zusätzlich die Anzahl der
archivierten Prognoserevisionen und die Anzahl der Qualitätsvergleiche
angezeigt. Leere/freie Seiten sind wiederverwendbar, aber nicht sofort
freier Dateisystemplatz. Fehlende/temporär nicht lesbare Dateien werden
sichtbar gekennzeichnet. Es werden keine Secrets, Token oder Datenbankinhalte
angezeigt. Zugriffe auf die Datenbanken erfolgen read-only.

- Alle **60 Minuten** (und beim Programmstart) schreibt der Hintergrundprozess
  höchstens einen Messwert pro Stunde/DB in eine **separate kleine Datenbank**:
  `data/sqlite_status.sqlite` (`storage_hourly_status`). Die Statusabfrage
  ist selbst read-only; sie löst keine Wartung und keine DB-Schreibzugriffe aus.
- **90 Tage** rollierende Größenmessungen, auf der Systemseite als
  **30-Tage-Größenverlauf** mit täglichem letztem Wert je Datenbank.
- API: `GET /api/system/sqlite-status?days=30`, zulässig 1–90 Tage.
- Stündliche Erfassung ohne Hintergrundverbindung zu zentraler InfluxDB.
- Die separate SQLite-Statusdatei selbst wird absichtlich nicht in den
  drei überwachten Dateien mitgezählt (sonst rekursive Größenentwicklung).
- Statusanzeigen werden unabhängig vom bestehenden Systemmodell einmal pro
  Minute aktualisiert und laufen nicht im Controller-Takt.

## Zentraler InfluxDB-Export (opt-in)

Der bestehende InfluxDB-2.x-Exporter wird um den **optional zu aktivierenden**
Stream `forecast_quality` erweitert; vorhandene Installationen übertragen
**unverändert weiter die bisherigen Streams**. Im Exporter-Editor gibt es
unter „Erweitert“ den Schalter **„Prognosequalität zentral exportieren“**;
Standard **aus**. Das Aktivieren einer geänderten Exporter-Konfiguration
unterliegt wie bisher den gesicherten Verbindungstests.

- Messreihe: `pv2hash_forecast_quality`
- Niedrig-kardinale Tags: `instance_id`, `instance_name`, `horizon` (`15m`,
  `30m`, `60m`, `180m`). Weder `issued_at` noch eindeutige Revisionen sind
  Tags.
- Zeitstempel: der tatsächliche Auswertungszeitpunkt `valid_at` in UTC;
  Fields: `forecast_w`, `actual_w`, `error_w`, `abs_error_w`,
  `forecast_issued_at` (Textfeld).
- Quelle sind bereits **validierte und dauerhaft gespeicherte** lokale
  Qualitätsvergleiche. Pro Eintrag wird ein monotones `quality_id` generiert;
  dieser Cursor garantiert, dass auch **nachträglich erstellte** Auswertungen
  für ältere `valid_at` nicht übergangen werden.
- Der bestehende pro-Exporter-Checkpoint wird erst nach erfolgreichem HTTP-204
  fortgeschrieben. Bei Fehlern gilt der bekannte Backoff und nach Neustart
  können die Datensätze erneut übertragen werden; derselbe Influx-Timestamp
  und dieselben Tags machen diesen Wiederholungsfall idempotent.
- **Kein Export vollständiger Prognoserevisionen**, kein neuer Datenbankdienst
  auf dem Hashpi und keine automatischen Veränderungen der zentralen InfluxDB,
  solange die Option deaktiviert bleibt.

Vor Aktivierung im Produktivbetrieb empfiehlt sich ein Test mit einem
separaten InfluxDB-Testbucket und gültigem Token. Die bestehenden Messreihen
und Datenbank-Pfade bleiben kompatibel.


## Optionale Shadow-Prognose (Entwicklung auf testvm01, 20.10.2026)

Der neue, in `forecast-shadow-learning.md` beschriebene Modellbereich
besteht aus vier zusätzlichen Archiv-/Projektions- und Qualitätstabellen
plus Modell-Snapshots. Alle liegen in derselben `forecast.sqlite`; sie
werden nicht in die Minerdatenbank oder nach InfluxDB repliziert.
Shadow-Revisionspunkte besitzen dieselbe **365-Tage**-Aufbewahrungsgrenze
wie Basisrevisionen. Die kleine Projektions-/Tageshistorie und permanente
Qualitätsstichproben bleiben für historische Analyse bestehen. Größe und
WAL sind in der bestehenden Dateigrößenansicht sichtbar; individuelle
Shadow-Tabellenzähler sind eine mögliche spätere UI-Erweiterung.

### Export-Cursor für Shadow-Qualitätsproben

Die Shadow-Qualität hat eine stabile numerische
\`quality_id INTEGER PRIMARY KEY AUTOINCREMENT\`. Die bestehende
Shadow-Prototyp-Tabelle ohne \`quality_id\` wird transaktional
unter Erhalt vorhandener Proben migriert. Spät bewertete
Messungen werden mit einem monotonic Cursor exportiert;
dieser liegt wie alle Export-Zustände in \`exporters.sqlite\`.
Die Systemseiten-Übersicht bleibt unverändert; die
\`forecast.sqlite\`-Dateigröße enthält bereits sämtliche
Shadow-Tabellen. Mehrjahres-Qualitätsdaten und von ihnen
referenzierte Modell-Snapshots bleiben lokal erhalten.
