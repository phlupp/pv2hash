# Post-0.9.5 Feedback: Versionsanzeige, PV-Kachel, Exporter

Entwicklungsstand für ein kommendes **Paket-Release mit ID `rYYYY.MM.DD.N`**.
Kein Bestandteil des bereits veröffentlichten Brückenreleases `v0.9.5`.
Der Rollout aller drei Hashpis muss vor der Umstellung auf Paket-IDs
abgeschlossen sein. Kein direktes Produktivupdate für diese Änderungen.

## Neue GUI (Produktivmodus)

- Das Wort „Vorschau“ verschwindet aus der unteren Menüleiste.
  Der überholte Seitentitel „Neue Oberfläche (Entwicklung)“ wird
  ebenfalls zu „PV2Hash“ vereinfacht.
- Die GUI-Version bleibt unten im Menü und auf der Systemseite.
  Das redundante Versions-Badge rechts oben im Dashboard entfällt.
- Die PV-Kachel zeigt **„Aktuelle PV-Leistung“**, ohne technische
  Protokollangabe im Untertext.
- Der Messqualitäts-Tooltip nutzt den echten Treibernamen, z. B.
  `Quelle: Modbus TCP PV-Erzeugung. Messstatus: live`, statt des
  frei vergebenen Quellenamens. Dazu ergänzt
  `/api/dashboard/status.cards.pv.driver_label` das bestehende
  Antwortschema; `source` bleibt aus Kompatibilitätsgründen erhalten.
- Die bestehende Status- und Ausblendelogik bei deaktivierter
  PV-Messung bleibt unverändert.

## Einheitliche Release-Identität für InfluxDB

Quelle ist ausschließlich `pv2hash/build_info.json` aus dem tatsächlich
laufenden Installationspaket. Die System-API und der InfluxDB-Exporter
lesen beide `read_build_identity()`. Eine alte `install.env` oder
historische Samples können diese Werte nicht übersteuern.

Das bestehende Measurement `pv2hash_system` erhält String-Felder:

| Feld | Paket-Release-Beispiel | Bedeutung |
| --- | --- | --- |
| `pv2hash_version` | `r2026.10.18.1` | Paket-Release-ID (Bestandsfeld) |
| `pv2hash_core_version` | `0.9.5` | eigenständige Core-SemVer |
| `pv2hash_gui_version` | `2.0.1` | eigenständige GUI-SemVer |

**Keine neuen Tags**, keine Änderung der Series-Identität oder
Datentypen, kein Versionsfeld in jedem Miner-Measurement.
Der bestehende `pv2hash_version`-String kann nach Migration
nicht mehr sicher als SemVer geparst werden. Grafana- und
API-Konsumenten, die numerisch vergleichen oder formatieren,
müssen vor produktivem Release überprüft werden.
Für historische Zeitbereiche bleiben ältere Werte unverändert;
Dashboards müssen gegebenenfalls beide Darstellungen vertragen.

Ein Source-/Legacy-Checkout ohne Build-Metadaten sendet weiterhin
seine vorhandene Core-Version in `pv2hash_version`,
zusätzlich `pv2hash_core_version`, aber **keine erfundene**
Release-ID oder GUI-Version. Nach Paketinstallation werden alle drei
Werte vollständig aus den eingebetteten Metadaten exportiert.

## Qualitätssicherung

- Backend-Regressionsprüfung für eingebettete Paketidentitäten,
  Legacy-Fallback, einheitliche API-Antwort und alle drei
  InfluxDB-String-Felder.
- Existierende Exporter-/Mining-/Update-Tests unverändert inhaltlich
  erhalten, nur die Versionsprüfungen entsprechend angepasst.
- React-TypeScript-Check, Frontend-Tests und Vite-Build.
- Tests und Entwicklung auf `testvm01`, ohne Schreibzugriffe auf die
  produktiven Hashpis oder InfluxDB.

Abnahmestand testvm01: 88 Python-Unittests, 27 Frontend-Tests,
Vite-Build, LIVE-Mouse-over „Quelle: Modbus TCP PV-Erzeugung“,
320-px-Darstellung ohne horizontalen Überlauf und ohne Browser-Konsolenfehler.

## Nachtrag: Mobile Updates, kompakte Versionsgruppe und PV-Historie

- Auf Mobilgeräten ist bei einem verfügbaren Update ein dezenter orange
  pulsierender Punkt direkt rechts oben am Menü-Burger angeheftet.
  Derselbe Updatezustand wird vom zentralen Version-Status ermittelt,
  es wird kein zweiter API-Request oder Poller eingeführt. Bei deaktiviertem
  Updatehinweis bleibt der Burger unverändert. Der ARIA-Name informiert
  Screenreader über ein verfügbares Update.
- Der Desktop-/aufgeklappte Menü-Fuß enthält unter **Versionen** in
  einer Zeile GUI/Core, den nur bei Bedarf klickbaren Updatehinweis zur
  Systemseite und darunter den Verbindungsstatus. Die ausführlichen
  Paketdetails bleiben auf der Systemseite. Eingeklappte Menüs behalten
  einen anklickbaren, kompakten Updatepunkt.
- Intern: `history_samples` erhält die Spalten `pv_power_w REAL` und
  `pv_quality TEXT`, additives Schema **v6**. Bestehende Messwerte
  bleiben bestehen und enthalten für diese beiden Spalten `NULL`.
- `_build_runtime_snapshot_payload()` erhält den Zustand direkt vom
  unabhängigen PV-Modbus-Adapter. Gleichzeitig wird die bisher undefinierte
  Referenz `pv_debug` in dieser Routine korrigiert. Ein echtes
  `LIVE 0 W`-Sample bleibt **0**. Offline/disabled/stale werden ohne
  Zahlenwert gespeichert, mit eigenem Qualitätstext. LIVE-Daten sind
  nur bis maximal drei Pollingintervalle (mindestens 15 s) gültig.
- Die lokale Serie `/api/datalogger/series` beinhaltet
  `pv_power_w` und `pv_quality`, sowohl bei rohen Werten als auch
  bei aggregierten/verdichteten Zeitreihen. Die bestehende Energiefluss-
  Grafik zeigt bei vorhandenen Daten die PV-Erzeugung als optionale
  separate Linie in Watt (keine erfundenen Werte).
- Die bestehenden `ExportSource.samples()` liefert die neuen
  SQLite-Spalten ohne zweiten Transport. Der InfluxDB2-Exporter schreibt
  `pv_power_w` als numerisches Feld und `pv_quality` als ausdrücklich
  erlaubtes Textfeld in **`pv2hash_system`**. Alle bisherigen Felder,
  Measurements und Tags bleiben erhalten; kein Einfluss auf Mining-
  Regelung, Controller oder Retention.
- Qualitätsprüfung: Vollständige Kette vom tatsächlichen PV-Adapter
  über Runtime-Snapshot, lokaler SQLite-Migration und ExportSource bis
  zum InfluxDB-Line-Protocol (auch 0 W, OFFLINE, STALE, Schema-Upgrade).
  Der Release-Identitäts-Exporter aus dem ersten PR bleibt erhalten.

Browsersmoke: 390 px, Burger-Badge an der rechten oberen Ecke erkennbar,
Screenreader-Navigationstext und keine horizontalen Überläufe.
