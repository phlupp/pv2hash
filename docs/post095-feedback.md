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
