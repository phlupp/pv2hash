# PV2Hash GUI 2.0 — technische Basis (Entwicklungsstand)

## Ziel
Langfristige React/TypeScript-Komponentenarchitektur mit Mantine, ECharts und TanStack Query.
Das bestehende Python-FastAPI-Backend sowie Controller, Datenmodelle und Treiber bleiben erhalten.

## Stand der Phase 0
- Separates Frontend in `frontend/` (keine bestehende GUI überschrieben).
- Responsives Mantine AppShell mit einklappbarer Desktop-Sidebar und mobilem Burger-Menü.
- Navigation nach Übersicht, Energie, Mining und System gruppiert.
- PV2Hash-Logo und dunkles Farbschema übernommen.
- React Router mit eigener URL-Basis `/ui-next/`.
- TanStack Query für wiederverwendbare API-Abfragen.
- Dashboard liest `GET /api/status` und `GET /api/ui/versionstatus` aus dem bestehenden Backend.
- Eine erste ECharts-Zeitreihen-Komponente prototypisch angebunden.
- Dashboard, DataLogger und Messungen laufen als React-Seiten; die übrigen noch nicht migrierten Seiten sind ehrlich als Platzhalter markiert.
- Dashboard und DataLogger sind lesend; Messungen stellt die vorhandenen schreibenden Source-Endpunkte mit Validierung und Rückmeldungen bereit. Noch keine tatsächlichen Konfigurationen per neuer GUI gespeichert. Keine Änderung am Regler. Kein Produktionsdeployment.

## Development auf testvm01
Isolierter Repository-Klon: `/home/chatgpt/pv2hash-ui-next`, Branch `feat/react-mantine-shell`.
Node.js 24 wird für den Build nur unter dem Benutzer `chatgpt` bereitgestellt.
Die produktiven Nodes benötigen **keinen** laufenden Node-Server.

```bash
cd /home/chatgpt/pv2hash-ui-next/frontend
export PATH="/home/chatgpt/.local/opt/node-pv2hash/bin:$PATH"
npm ci
npm run build
npm run dev
```

Vite bedient die React-Oberfläche im Testnetz auf Port 5173, unter
`http://10.20.0.51:5173/ui-next/`. `/api` und `/static` werden im Development
an die unveränderte FastAPI-Testinstanz auf `127.0.0.1:8000` weitergeleitet.

## Geplante Migration
1. Gemeinsame Layout- und API-Schicht fertigstellen, mobile/Desktop-Abnahme.
2. Dashboard und DataLogger inklusive vollständiger Zeitreihen und Events migrieren.
3. Quellen/Messungen, Miner, Sockets und dynamische Formulare übernehmen.
4. System, Updates, Status/Offline-Overlay und Bedienlogik vollständig übernehmen.
5. Vor Umschaltung vollständige funktionale Regressionstests durchführen.
6. Bestehende UI erst entfernen, wenn alle Funktionen gleichwertig verfügbar sind.
7. Erst danach eine neue Seite Prognose entwickeln.

## Technische Regeln
- Komponenten und Datentypen wiederverwenden; keine Seiten-spezifischen API-Fetch-Hacks.
- Keine CDNs, externe Laufzeit-Schriften oder sonstige Internetabhängigkeit der GUI.
- React-Build in Releaseprozess integrieren, nach Tests als statische Dateien ausliefern.
- Alle Funktionsaufrufe nur einmal auslösen; Mutationen ausdrücklich bestätigen und sichtbar rückmelden.
- Keine direkten Zugriffe des Frontends auf Miner, Datenbank oder Controller.
- Keine Veränderung der bestehenden Controller-Logik im Zuge der GUI-Migration.
- Bestehende Konfiguration, Adapterschemata, API-Antworten und Updatepfade absichern.
- Prognosen, Messwerte und simulierte Demo-Daten klar auseinanderhalten.


## Designregel: Informationsdichte und Mantine-Standards (Phase 0.1)

- Keine selbst gestalteten Kachel-Hintergründe, Farbverläufe oder großen Radien.
- Cards verwenden Mantines globale Komponenten-Defaults (Border, sm-Padding, sm-Radius, kein Schatten).
- Gemeinsame Theme-Tokens in `frontend/src/theme.ts`; nur Navigation und wenige Layoutregeln in `frontend/src/styles.css`.
- Kennzahlen platzsparend: vier Spalten am Desktop und zwei auf kleinen Smartphones; kompakter Text und geringe Kartenhöhe.
- Statusmeldungen als schmale Zeilen statt vollflächiger Statuskarten, wenn kein größerer Inhalt notwendig ist.
- Diagramme verwenden dieselben Mantine-Cards und eine wiederverwendbare ECharts-Komponente.
- Mehr Informationsdichte bedeutet nicht kleinere Bedienelemente: Mobile Navigation und Touch-Ziele bleiben gut erreichbar.
- Vor der späteren produktiven Freigabe die Komponentenvisuell in beiden Viewports abnehmen.

## Verbindlicher GUI-2.0-Designstandard

Freigegeben am 2026-10-16 als Basis für die weitere GUI-Migration:

- **GUI 2.0** bezeichnet die neue Oberflächengeneration; das ist keine eigene Backend- oder Release-Versionsnummer.
- Die seitliche Navigation bleibt auf Desktop-Geräten erhalten, auf Mobilgeräten als ausklappbares Menü. Navigationseinträge werden in Übersicht, Energie, Mining und System gruppiert.
- **Kompakte Informationsdichte** statt großer dekorativer Flächen: native Mantine-Komponenten, kleine Radien, dezente Rahmen, wenige Schatten, lesbare Typografie und nutzbarer Platz für Messdaten.
- Künftige Seiten (insbesondere DataLogger, Prognose, Optimierung) folgen den zentralen Mantine-Komponenten und dem gemeinsamen Layout; keine individuell nachgebauten Kachel-Designs.
- **Farben sind keine feste Produktentscheidung:** das aktuelle dunkle Schema mit violettem Akzent bleibt zunächst erhalten; spätere Farben/Dark-Light-Varianten können über zentrale Theme-Tokens umgestellt werden.
- Eine Farbumstellung soll **nicht** manuell in einzelnen Seiten stattfinden. Feste Farbwerte in CSS, ECharts, Icons und Statusdarstellungen müssen dafür sukzessive in zentrale semantische Tokens überführt werden. Statusfarben behalten unabhängig vom gewählten Akzent eine eindeutige Bedeutung.
- Keine Änderungen an der produktiven Steuerung oder dem API-Verhalten durch diese Designfestlegung.


## Dashboard: funktionsbezogene Module (Phase 0.2)

Die GUI 2.0 hat **kein festes Dashboard-Template** mit immer denselben Bereichen.
Das Dashboard setzt registrierte Module in definierter Reihenfolge zusammen.

- Jede Funktion bringt bei Bedarf eine eigene Datei `frontend/src/dashboard/sections/<Feature>.tsx` mit, die `dashboardSection` mit `id`, `order`, `Component` und optional `enabled(context)` exportiert.
- `frontend/src/dashboard/registry.ts` entdeckt solche Dateien beim Vite-Build automatisch über `import.meta.glob`. Die Hauptseite `pages/Dashboard.tsx` muss für einen weiteren Dashboard-Bereich nicht geändert werden.
- `enabled(context)` entscheidet anhand **realer** API-Konfiguration und Verfügbarkeit, ob das Modul angezeigt wird. Für die zukünftige Prognose wird erst mit einem echten aktivierten Forecast-Backend ein Vorschau-Modul ausgeliefert. Keine Mock-Prognosen im Live-Dashboard.
- Die Module erhalten nur einen gemeinsamen lesenden Kontext (`/api/dashboard/status` und `/api/status`) und bauen ihre Darstellung aus Mantine-Standardkomponenten auf.
- Aktuelle Module: Energie, Regelung, Mining. Energie zeigt Netz und PV-Leistung sowie Batterie nur bei aktivierter/verfügbarer Batterie.
- PV-Erzeugung ohne entsprechenden PV-Wert wird ausdrücklich als **nicht verfügbar** gekennzeichnet, nicht aus der Netzeinspeisung geschätzt.
- Die zentrale Hauptseite zeigt **keine** technischen Steuerungs-Modi wie Coarse/Equal/Cascade. Regelparameter verbleiben in den Einstellungen, ohne die Controller-Logik zu verändern.
- Die Miner-Tabelle zeigt nur Status, Name, Profil, Leistung und Hashrate. Aktionen, Prioritäten und IP-Adressen gehören auf die Minerseite.
- CPU/RAM gehören auf die Systemseite. Der frühere spontane Live-Trend wird nicht als Pflichtteil des Dashboards übernommen; die Zeitreihenanalyse gehört zum DataLogger.
- Die Ringdarstellung wurde durch eine kompakte Fortschrittsleiste mit Wartezeit und letzter Regleraktion ersetzt.
- Die neue Dashboard-Seite ist bis zur Migration der Bedienseiten ausschließlich **read-only**; es werden keine bestehenden Backend-Aktionen neu eingebaut.

## Unabhängige GUI-Versionierung

PV2Hash GUI ist ab der neuen GUI-Generation eigenständig nach SemVer versioniert;
erste Entwicklungskennung ist **`2.0.0-dev.1`**.
Maßgebliche Versionsquelle ist `frontend/package.json` (mit Lockfile);
die Versionskennung wird beim Build in die Frontend-Anzeige übernommen.

Die **GUI-Version** und die **Backend-/PV2Hash-Core-Version** bleiben getrennte
Angaben, da Änderungen der Oberfläche nicht immer ein Core-Release benötigen.
Zukünftige Release-Builds müssen festhalten, welche API-Versionen sie unterstützen;
SemVer allein garantiert nicht die Kompatibilität zum Backend.


## Messqualität direkt an Energiekacheln (Phase 0.3)

- **Quellen- und Qualitätsstatus gehören zur jeweiligen Messung**, nicht in die Energie-Bereichsüberschrift.
- Jeder Energie-Kachelkopf darf einen kleinen Status-Badge erhalten. Quellname und technischer Rohstatus sind nur über den Tooltip abrufbar; die Kachel selbst zeigt den Messwert und seine Datenqualität.
- Die wiederverwendbare Komponente `frontend/src/dashboard/components/QualityBadge.tsx` übersetzt z. B. `live` → LIVE, `simulated` → SIMULIERT, `stale` → VERALTET, `offline` → OFFLINE.
- Netzleistung: `snapshot.quality` (Quelle aus dem bestehenden Dashboard-Endpunkt).
- Batterie: **eigenständig** `snapshot.battery_quality`; die Batterie übernimmt nie den Netzstatus.
- PV-Leistung: `snapshot.pv_power_w` kann bereits dargestellt werden, aber das aktuelle Modell liefert **noch keine eigene PV-Qualität oder PV-Quellenrolle**. Deshalb steht ohne PV-Wert KEIN WERT, mit Wert aber ohne Qualitätsinfo OHNE STATUS. Keinesfalls wird `snapshot.quality` als angebliche PV-Qualität wiederverwendet.
- Der Badge kennzeichnet die Qualität/Verfügbarkeit des gemeldeten **Messwerts**, nicht automatisch eine gemessene Netzwerkverbindungsqualität.
- Mobile Darstellung: Bei nur zwei Kacheln nebeneinander darf das vollständige Badge auf eine zweite Zeile umbrechen. Informationsgehalt geht vor unverständlicher Abkürzung; horizontales Scrollen ist nicht erlaubt.
- Mit späterer separater PV-Source: zuerst Datenmodell/API um `pv_quality` und echte Quellenidentität ergänzen, dann den Badge anbinden. Keine Scheinwerte.


### Regelungs-Countdown (GUI 2.0.0-dev.3)
- Die Restzeit erscheint **nur im neutralen grauen Badge**, nicht doppelt in einer Zusatzzeile.
- Der Fortschrittsbalken erhält Mantine-`sm` statt `xs` (etwas höher), in ruhigem Akzent-Violett.
- Warten auf das Mindest-Schaltintervall ist ein normaler Zustand, **keine Warnung**. Orange/Rot bleibt echten Warnungen und Fehlern vorbehalten.
- Verfügbare Controller-API-Daten bleiben unangetastet. Die letzte Regleraktion und Ereignismetadaten bleiben sichtbar.


### Countdown und Miner-Tabelle (GUI 2.0.0-dev.4)

- Der neutrale Countdown-Badge trennt die Restzeit-Ziffern (Tabellenziffern, größer) vom kleineren, kleingeschriebenen Einheitssymbol `s`; er bleibt ohne Alarmfarbe. Freigabe-/Bereit-Zustände werden als gewöhnlicher Badge-Text angezeigt.
- Der Minername wird nur für echte Miner mit einer von der Backend-API gelieferten, sicheren HTTP(S)-WebGUI-URL zum externen Link (`target=_blank`, `rel=noopener noreferrer`); kein IP-Text als zusätzliche Spalte. Auch Simulatoren erhalten ihren konfigurierten URL-Link, selbst wenn diese Adresse ins Leere zeigt oder `127.0.0.1` lautet.
- Die WebGUI-Link-Prüfung ist in `frontend/src/dashboard/minerLink.ts` gekapselt und durch `npm test` abgesichert (echte Geräte, Simulator/Loopback und ungültige URL).
- Tabellen-Spalte Profil auf etwa 50–54 px begrenzt und Mindest-Tabellenbreite auf 420 px gesenkt, ohne die Statusanzeige umzubauen. Bei sehr schmalen Displays bleibt die Tabelle intern horizontal scrollbar.
- Anpassungen sind reine GUI-Änderungen. Bestehende API, Regler und Produktionsinstallation bleiben unverändert.


### Dashboard-Tabellen: scrollbarfreier Lesebereich (GUI 2.0.0-dev.5)

- Auf schmalen iPhone-Displays bleibt die Miner-Tabelle horizontal scrollbar; der Scrollbalken soll die letzte Datenzeile nicht überlagern.
- Dafür die native Mantine-`ScrollArea` mit `scrollbars="x"` und `offsetScrollbars="x"` verwenden. Der horizontale Scrollbalken bekommt einen konstanten Platz unter der Tabelle, auch wenn er nur beim Wischen kurz sichtbar ist. Das verhindert Überlagerungen und Layoutsprünge ohne künstliche, semantisch leere `Table.Tfoot`-Zeile.
- Der Minername öffnet die im Backend vorhandene WebGUI-URL unabhängig vom Treibertyp in einem neuen Tab (auch Simulator). Nur gültige HTTP(S)-URLs ohne eingebettete Zugangsdaten werden als Links angezeigt.


## Core-Abgleich auf testvm01 (2026-10-16)

Die GUI 2.0 bleibt unabhängig vom PV2Hash-Core versioniert. Für die
Dashboard-Abnahme wurde der laufende Entwicklungs-Core von 0.8.2 auf das
**offizielle Release 0.9.4** aktualisiert.

- Dienst: `pv2hash-dev.service`, Port 8000; die GUI-Vite-Vorschau bleibt
  unabhängig auf Port 5173 und verwendet den Backend-Proxy auf Port 8000.
- Bisheriger Entwicklungsstand: `/srv/pv2hash-pools-stage`, Version 0.8.2.
  Dieses Staging-Verzeichnis wurde bewusst **nicht überschrieben**.
- Neuer Core: `/srv/pv2hash-gui2-core`, geklont aus Git-Tag `v0.9.4`,
  Commit `4918a57cc7e01949ca7963253f789d57db00f9a7`.
- Die neue Core-Kopie verwendet dieselbe bestehende Python-Virtualenv über
  `.venv -> /srv/pv2hash/.venv` und dieselben Daten über
  `data -> /srv/pv2hash/data`.
- Der bestehende systemd-Drop-in `90-exporter-metrics.conf` bleibt erhalten.
  Der neu angelegte `95-gui2-core.conf` setzt ausschließlich
  `WorkingDirectory=/srv/pv2hash-gui2-core`.
- Vorherige Sicherung: `/srv/pv2hash-update-backups/gui2-core-before-v0.9.4-20261016T235427/`.
  Enthält Konfiguration, Instanzkennung, Dienst-/Drop-in-Dateien sowie
  konsistente SQLite-Sicherungen von `history.sqlite` und `exporters.sqlite`.
  Das Sicherungsverzeichnis ist nur für den Administrator zugänglich;
  Inhalte und Zugangsdaten dürfen nicht in GitHub eingecheckt werden.
- Validiert: Git-Tag-Commit, Python-Kompilierung, pip-Abhängigkeiten,
  Konfigurationsmigration an isolierter Kopie, aktiver Dienst, API-Version
  0.9.4, DataLogger-Historie/Integrität, aktive Batterie-Quelle inklusive
  LIVE-Qualität, unveränderte Batterie-/Netz-/Miner-/Exporter-Einstellungen,
  GUI-2.0-Anbindung und Browserkonsole.

### Rückfallweg (nur falls erforderlich)

Zuerst prüfen, ob das ältere Backend das inzwischen möglicherweise
fortgeschriebene Datenbankschema noch lesen kann. Den Codepfad kann man durch
Entfernen des neuen systemd-Drop-ins wieder auf den vorherigen Stage-Stand setzen:

```bash
sudo rm /etc/systemd/system/pv2hash-dev.service.d/95-gui2-core.conf
sudo systemctl daemon-reload
sudo systemctl restart pv2hash-dev.service
```

**Wichtig:** Der Code-Rollback ersetzt keine Datenbankmigration rückwärts.
Die vor dem Wechsel angelegte Sicherung ist separat aufzubewahren und darf
nicht unbedacht über inzwischen gewachsene Live-Daten zurückgespielt werden.
Der produktive Hashpi wurde durch diese Arbeiten nicht verändert.


## DataLogger in GUI 2.0 (Phase 0.4)

Stand: GUI `2.0.0-dev.6` auf testvm01, Core `0.9.4`.
Die alte DataLogger-Seite bleibt bis zur endgültigen GUI-Migration unverändert.

### Komponenten und Architektur

- `frontend/src/pages/DataLogger.tsx`: unabhängige React-Seite, UI-Zustand
  für Zeitraum, Live/Historie, Minerfilter, Reglerereignis und Debug-Ausklappen.
- `frontend/src/datalogger/api.ts`: **lesende** API-Abfragen für Status,
  Serien und optionale Debug-Ereignisse über bestehende FastAPI-Endpunkte.
- `frontend/src/datalogger/time.ts`: zentrale Zeitfenster-Navigation
  (`1h`, `3h`, `6h`, `12h`, `24h`, `7d`), Formatierungsfunktionen.
- `frontend/src/datalogger/charts/DataLoggerChart.tsx`: wiederverwendbare
  ECharts-Komponente mit Zeitachse, Zoom, interaktiver Legende und
  tatsächlichen Profilwechsel-Markern; ausgeliefert nur mit der
  DataLogger-Route (kein ECharts-Ladezwang auf dem Dashboard).
- `frontend/src/datalogger/charts/palette.ts`: zentrale semantische Farben
  für Netz, Miner, Batterie, SOC, Hashrate, Temperatur und Regler-Events.
- `frontend/src/datalogger/components/ControllerTimeline.tsx`: echte
  angewendete Regler-Events oberhalb der Charts mit auswählbarer
  Detaildarstellung. Ereignisse werden über dieselbe ID in Charts markiert.
- `frontend/src/datalogger/cluster.ts`: rein visuelle Gruppierung dichter
  Events in Zeit-Bins. Im Kontext einer Gruppe können die einzelnen echten
  Events ausgewählt werden; API-Rohdaten werden dadurch nicht verändert.

### Darstellung und Verhalten

- Zeitfenster als Mantine SegmentedControl; Zurück/Weiter verschieben das
  Intervall, Live springt zur Gegenwart und aktiviert den 30-Sekunden-Refresh.
  Historische Fenster werden nicht zyklisch aktualisiert.
- Mehrfachfilter für Miner über Mantine MultiSelect; bei leerer Auswahl
  werden alle Miner berücksichtigt. Das Backend filtert Leistung,
  Hashrate, Temperaturen sowie Regler-Events.
- Drei ECharts-Karten: Energiefluss (Netz/Miner/Batterie), Batterie (W
  und SOC auf getrennten Achsen) und Mining (W, GH/s, Temperatur).
  Die Batterie-Karte erscheint dynamisch bei konfigurierter Batterie
  oder vorhandenen Batterie-Samples im Zeitfenster.
- Debug-Karte standardmäßig geschlossen, API-Abfrage nur bei geöffnetem
  Bereich. Darin Anzeige der kurzlebigen Controller-Debug-Ereignisse.
- Samplezahl, Zeitintervall und Aufbewahrung als kompakte Informationen;
  technische SQLite-Details gehören weiterhin auf die Systemseite.
- Ausschließlich tatsächlich gespeicherte `controller_events` mit
  `event_type=applied` werden in Zeitleiste und Diagrammen dargestellt.
- Alle Ressourcen lokal gebündelt, keine CDN-Abhängigkeit.
  Bestehende Legacy-GUI und PV2Hash-Regelung bleiben unverändert.

### Offene technische Grenze in Core 0.9.4

Die bestehende Core-API `/api/datalogger/series` holt für ein Zeitfenster
**maximal die 500 ältesten angewendeten Reglerereignisse**
(`ORDER BY ts ASC LIMIT 500`). Für lange Zeitfenster können deshalb
neuere Reglerentscheidungen fehlen; `marker_count` ist dann nur die
Anzahl der gelieferten Events, nicht die tatsächliche Gesamtzahl.
GUI 2.0 kennzeichnet diese Begrenzung bei 500 Markern sichtbar.
Diese Core-API sollte vor der endgültigen Migration auf vollständige,
effiziente und zeitlich korrekt auswählbare Event-Abfragen erweitert werden.
Für die erste Vorschau wurde der freigegebene Core 0.9.4 **nicht gepatcht**.

### Teststand

- `npm run build` erfolgreich (TypeScript/Vite).
- `npm test`: sechs Tests erfolgreich (Zeitfenster, Event-Gruppierung,
  fehlerhafte Zeitstempel sowie bestehende Miner-WebGUI-Links).
- Browser-Vorschau Desktop/Mobil: keine JavaScript-Fehler, keine horizontale
  Seitenüberbreite, Live/Zurück/7d, Event-Gruppen und aufgeklappter Debug
  mit echten Core-0.9.4-Daten geprüft.
- Offene visuelle Abnahme: Mobile Safari und die Detailgestaltung der
  Chartachsen auf dem produktiven Vergleichsgerät. Bei Bedarf Feinschliff.

## Messungen als treibergenerierte GUI (Phase 0.5)

Stand: GUI **2.0.0-dev.7** auf testvm01, Core **0.9.4**.

### Verbindliches Layout

- Jede Messquelle erscheint als eigene **Karte über die gesamte Inhaltsbreite**,
  untereinander statt in zwei Desktop-Spalten, auch auf Mobilgeräten.
- Eine Karte zeigt Quelle/Treiber, einen Messqualitäts-Badge sowie kompakte
  Statuswerte. Die Konfiguration wird auf Wunsch ausgeklappt.
- Treiber dürfen beliebig viele Statusfelder, Detailgruppen, Felder,
  Fieldsets und Aktionen über das vorhandene gemeinsame GUI-Modell liefern.
  Der React-Renderer in `frontend/src/sources/SourceCard.tsx` verarbeitet
  das Schema generisch – **keine React-Sonderkomponenten für SMA, Simulator
  oder Modbus**. Alle Eingabefelder und Abstände sind Mantine-Komponenten.
- Für zukünftige Quellenrollen wie PV muss das Backend erst ein korrektes
  eigenes Quellenmodell inklusive Rolle/Qualitätsstatus liefern. Sobald
  ein Modell unter `gui_models` auftaucht, wird es in der React-Seite
  automatisch als weiterer Messbereich angezeigt. Keine vorgetäuschte
  PV-Messung bis dahin.

### Integration und Verhaltensregeln

- `GET /api/sources/status`: Live-Anzeige aller vom Core gelieferten
  `gui_models` alle 5 Sekunden, ohne offene Formulare neu aufzubauen.
- `POST /api/sources/gui/preview`: Wechsel des Treiberprofils;
  GUI-Felder werden aus dem aktualisierten Modell neu aufgebaut.
- `POST /api/sources/action`: treibereigene Aktionen
  (`action_on_change` und Aktionsbuttons, z. B. SMA-Gerätesuche,
  Batterie-Modbus-Profilauswahl). Die API liefert aktualisierte GUI-Modelle.
- `POST /api/sources/config`: bewusste Speicherung der in der
  gesamten Messungen-Seite bearbeiteten Felder. Vorher erfolgt
  schema-basierte Pflichtfeld-/Zahlenvalidierung.
- Backend-Formularnamen bleiben unverändert; boolesche Checkboxwerte
  werden wie im alten HTML-Formular über `on` abgebildet.
- Der Entwurf und die Live-Anzeige sind getrennte Zustände.
  Live-Polling überschreibt **niemals** gerade bearbeitete Felder.
  Eine ungespeicherte Treiberwechsel-Vorschau erhält den neutralen
  Status-Badge **VORSCHAU**, nicht den echten Messqualitätsstatus.
- Ungespeicherte Änderungen werden angezeigt und können verworfen
  werden. Aktionen sind gegen Doppel-Ausführung abgesichert;
  Bestätigungen/Fehlermeldungen erfolgen zentral über Notifications.
- Das geteilte QualityBadge kennt unter anderem LIVE, SIMULIERT,
  DEAKTIVIERT und OFFLINE. Quellname bleibt als Untertitel bzw. Tooltip
  erreichbar.

### Teststand und Einschränkungen

- `npm run build` sowie `npm test` erfolgreich.
- Prüfung auf 320, 390 und 1280 px: Karten untereinander,
  ohne horizontale Seitenüberbreite. Netzwert und Batteriestatus LIVE.
- Die Entwicklungsvorschau wechselte simulator → SMA und zeigte
  alle SMA-Konfigurationsfelder und die Geräte-Suche via
  `/api/sources/gui/preview`. Der unveränderte laufende Simulator
  wurde im Core und in der Konfigurationsdatei verifiziert.
- Fehlende SMA-Seriennummer blockiert einen Speicherversuch **vor** dem
  API-Aufruf, anschließend mit Verwerfen wieder auf Simulator gesetzt.
- Treiberaktionen wurden aus Sicherheitsgründen nicht gegen die
  echte Batterie oder Geräte ausgeführt; der tatsächliche Speicherweg
  und Batterie-Modbus-Profilwechsel müssen vor Produktionsfreigabe
  noch mit geeigneten Testdaten vollständig Ende-zu-Ende geprüft werden.
- Die Legacy-GUI und sämtliche Core-/Treiberdateien bleiben unverändert.


### Messungen: Layout-Feinschliff (GUI 2.0.0-dev.8)

- **Breitenlogik:** Der Source-Treiber liefert Feldtyp, Feldgruppe und
  `layout.width` (z. B. `quarter`); der gemeinsame Mantine-Renderer setzt
  diese Hinweise responsiv um. Gruppen aus **exakt fünf gleichwertigen
  Viertelbreiten-Feldern** werden als gemeinsame Gruppe auf breiten
  Bildschirmen in **fünf Spalten** angeordnet. Gemischte Breiten
  behalten ihre vom Treiber vorgegebene Anordnung.
  Das gilt auch für künftige Treiber, nicht nur für Modbus.
- 1280/1600 px: fünf Registerparameter pro Zeile; Tablet 800 px: 2/2/1;
  iPhone 320/390 px: ein Eingabefeld pro Zeile, ohne Seitenüberbreite.
- **Messdetails:** Die Anzahl nebeneinanderliegender Messwerte passt sich
  an die gelieferten Detailfelder an; bis zu sechs auf breiten Screens.
- **Kontrast:** Inputs und Selects erhalten über das zentrale Mantine-Theme
  einen dunkleren Hintergrund (`dark.8`) und besser sichtbare Randfarbe
  (`dark.5`). Keine seitenabhängigen Farb-Hacks.
- **Leeres Select:** Wenn der Treiber keine Optionen liefert, erscheint
  dessen `help`-Hinweis direkt als Platzhalter im Auswahlfeld.
  Beispielsweise `Bitte Geräte-Suche starten.` in der leeren
  SMA-Seriennummernauswahl; keine doppelte Hilfetext-Zeile.
- **Seitenkopf:** Badge `API LIVE` benennt die Erreichbarkeit der
  Messungen-API (nicht den Status jeder Quelle), während jede Quelle
  ihren eigenen QualityBadge behält. `Aktualisieren` ruft den Status
  sofort erneut ab; die 5-Sekunden-Automatik bleibt und es findet
  weder Speicherung noch Treiber-Neustart statt.
- Vorschau-SMA wurde nur **nicht persistent** genutzt und anschließend
  verworfen; Konfiguration und Batterie-Verbindung blieben unangetastet.


### Messungen: dreispaltige Standardgruppen (GUI 2.0.0-dev.9)

- Auch **drei gleichartige, nicht explizit mit Breiten versehene** Treiber-Eingabefelder
  erhalten auf Desktop-Geräten eine gemeinsame Zeile. Auf Tablet-Breiten
  zwei plus eins und auf schmalen Smartphones je eine eigene Zeile.
- Die Layoutregel arbeitet über `isThreeFieldCompactGroup` generisch für
  drei gleichartige Text-, Zahlen-, Passwort- oder Select-Felder; sie
  ist **keine** Simulator-Sonderregel.
- Explizite `layout.width`-Angaben des Treibers haben Vorrang, und das
  stets eigene Treiber-Auswahlfeld bleibt auf seiner bisherigen Zeile.
- Beispiel: Simulator-Startwert, Einspeisungs-Endwert und
  Änderungsgeschwindigkeit werden gemeinsam dreispaltig dargestellt.
  Lange Beschriftungen erhalten im dreispaltigen Desktop-Modus einen
  einheitlichen Labelbereich, damit alle drei Eingabefelder bündig stehen.
- Konfiguration, Core, Batterie und die produktive GUI bleiben unverändert.

## Miner in GUI 2.0 (Phase 0.6)

Entwicklungsstand: GUI **2.0.0-dev.10** auf testvm01 mit Core **0.9.4**.
Die produktive und die Legacy-Minerseite bleiben unverändert.

### Konzeption und Oberfläche

- Ein Miner pro Mantine-Karte über die volle Inhaltsbreite; Status-Badges
  im kompakten Kopf, Konfiguration und Live-Details einklappbar.
- Die vorhandenen DriverField- und DriverAction-Schemata bleiben
  verbindlich. Ein universeller Feldrenderer zeigt Identität,
  Regelung/Leistungsprofile, Treiberkonfiguration und (sofern unterstützt)
  geräteseitige Einstellungen. Kein statisch nachgebautes
  Simulator-/Braiins-/WhatsMiner-/AxeOS-Formular.
- `frontend/src/miners/model.ts`: gemeinsame TypeScript-Modelle,
  FormData-Konvertierung und Validierung, inklusive punktgetrennter
  Feldnamen wie `profiles.p1.power_w`.
- `frontend/src/miners/fields.tsx`: responsive Mantine-Standardfelder,
  Treiber-Layoutbreiten, gesperrte read-only/unknown Felder.
- `frontend/src/miners/MinerCard.tsx`: Minerstatus, separate
  Konfigurations- und Geräteänderungen, Treiberaktionen mit Bestätigung,
  strukturierte Details und Tabellen.
- `frontend/src/pages/Miners.tsx`: Miner-Verzeichnis und
  driverbasiertes Anlegeformular. Die Seite pollt den Status alle
  fünf Sekunden und fordert strukturierte Live-Details nur für
  aufgeklappte Miner an.
- Vorhandene Passwörter verlassen über den neuen GUI-Endpunkt **niemals**
  als Klartext den Backendprozess; in bestehenden Formularen bleibt
  ein leeres Passwort-Feld als „unverändert“ bestehen. Beim Anlegen
  muss ein erforderliches Passwort tatsächlich neu eingegeben werden.
- Der WebGUI-Link basiert auf der **gespeicherten** Backend-Adresse,
  nicht auf einem noch nicht gesicherten Formulareintrag.

### Ergänzte lesende Core-APIs

- **GET `/api/miners/gui`** in `pv2hash/app.py`: explizit ausgewähltes,
  JSON-fähiges Miner-GUI-Modell aus bereits bestehenden Schemafunktionen;
  kein Export des vollständigen Miner-Konfigurationsobjekts.
  Liefert Miner-ID, Name, Treiber, URL, Summary, Feldgruppen, Aktionen,
  Geräte-Einstellungs-Readbacks und strukturierte Details sowie
  Treiberkatalog und Grundfelder fürs Anlegeformular.
  Passwortfelder werden vor der JSON-Serialisierung maskiert.
- **GET `/api/miners/status?open_ids=...`** ergänzt die bestehende
  Live-Antwort für geöffnete Miner um `details` als JSON.
  Das bisherige `details_html` bleibt für die Legacy-GUI erhalten.
- Bestehende Core-Schreibendpunkte unverändert genutzt:
  `POST /api/miners/add`,
  `POST /api/miner/{id}/config`,
  `POST /api/miner/{id}/device-settings`,
  `POST /api/miner/{id}/action` und
  `POST /api/miner/{id}/delete`.
- Änderungen am Controller, an Miner-Treibern und am Datenbankschema:
  **keine**.

### Testvm01 / sichere Entwicklungsbereitstellung

- Der Core auf testvm01 wurde nach Vergleich zum offiziellen Stand
  ausschließlich für die beiden lesenden Miner-API-Erweiterungen
  aktualisiert; der Dienst `pv2hash-dev.service` wurde dafür neu
  gestartet. Die Änderungen bleiben im GUI-Draft-PR und werden
  **nicht** direkt als offizielles Core-Release ausgegeben.
- Die unveränderte Basisdatei des Test-Cores ist separat unter
  `/home/chatgpt/pv2hash-core-before-miner-api.py` gesichert;
  Code-Rückfall benötigt Neuladen des Entwicklungsdienstes.
- Browser-Tests Desktop und Mobil; Simulator mit Profilen, Checkboxen,
  Pool-Konfiguration, Thermal- und anderen strukturierten Details.
  Kein horizontaler Seitenscroll bei 320/390 Pixeln, keine JS-Fehler.
- `npm test`: 14 Tests bestanden, `npm run build`: erfolgreich.
- **Noch keine schreibenden Geräteaktionen getestet.** Anlegen,
  Speichern, Löschen und Anwenden von Geräteeinstellungen sind
  implementiert, aber müssen vor Produktionsfreigabe mit einem
  geeigneten Simulator-Test und kontrollierter Geräteumgebung
  Ende-zu-Ende geprüft werden. Keine produktiven Miner verändert.


### Miner: JSON-Serialisierung nach dem Anlegen (GUI 2.0.0-dev.11)

- **Ursache (testvm01, 2026-10-17):** Der neu erfasste axeOS-Miner
  lieferte verschachtelte Treiber-Details mit
  `DriverDetailColumn`-Dataclasses (insbesondere bei Pool-/ASIC-Tabellen).
  `GET /api/miners/status?open_ids=...` baute zwar die korrekten
  Details, gab sie aber direkt an Starlettes `JSONResponse` weiter.
  Dies führte zu `TypeError: Object of type DriverDetailColumn is not JSON
  serializable`, HTTP 500 und dem missverständlichen Browser-Fehler
  `Unexpected token 'I', 'Internal Server Error' is not valid JSON`.
  Das erfolgreiche Anlegen des Miners selbst war nicht betroffen.
- **Core-Fix:** Alle Elemente der Live-Antwort werden vor
  `JSONResponse` über das bereits verwendete
  `fastapi.encoders.jsonable_encoder` verarbeitet. Damit werden
  verschachtelte Dataclasses, Tabellen-Spalten und andere unterstützte
  Python-Objekte konsistent serialisiert. Das alte `details_html`
  bleibt unverändert verfügbar.
- **GUI-Fix:** Die gemeinsame Miner-API-Verarbeitung prüft den
  Content-Type und liefert verständliche HTTP-/JSON-Fehlertexte
  statt roher Syntaxfehler. Der Badge meldet bei API-Fehlern
  `API-FEHLER` statt eine unzutreffende Behauptung, das komplette
  Backend sei offline.
- **Regression:** Die tatsächlich angelegten beiden Test-Miner
  (Simulator und axeOS) liefern zusammen mit geöffneten Details HTTP 200,
  gültiges JSON und sämtliche verschachtelten axeOS-Tabellen. Auch die
  alte HTML-Detailstruktur bleibt enthalten. Frontend-Tests wurden
  um eine Fehlerbehandlung für HTTP 500 mit Textantwort ergänzt.
- Änderung ausschließlich auf testvm01 und im GUI-2.0-Draft-PR;
  weder Miner-Konfiguration noch produktiver Hashpi verändert.


## Sockets in GUI 2.0 (Phase 0.7)

Stand: **GUI 2.0.0-dev.12**, Core-Basis 0.9.4 mit lesender
Socket-GUI-API-Erweiterung auf `testvm01`.

### Layout und Bedienung

- Full-width-Mantine-Socket-Karten, einzeln ein-/ausklappbar.
  Kopf: Socket-Name, Ein-/Aus-Zustand, *eigene* Messqualität,
  Treiber, Leistung und Priorität; vorhandene Miner-Zuordnung
  wird als kompakter Badge angezeigt.
- Aufgeklappt: Identität, Treiber, Host, Priorität, Verbindungs- und
  Automatikoptionen, treiberspezifische Einstellungen, Live-Details,
  sowie manuelles Ein-/Ausschalten, Neustart (Tasmota) und Löschen.
- Speichern ist bewusst von Geräteaktionen getrennt; ungespeicherte
  Änderungen können verworfen werden. Ausschalten, Neustart und
  Löschen benötigen eine Bestätigung. Doppelaktionen während eines
  API-Aufrufs sind deaktiviert.
- Socket-Erstellung im eingeklappten Mantine-Bereich;
  Tasmota-HTTP-Gerätesuche mit Netzwerk-Interface oder explizitem
  CIDR-Override, Trefferliste, Duplikaterkennung und direkter
  Übernahme von Host, Gerätename und Port. Kein automatisches Scannen.
- Status-Polling über `GET /api/sockets/status` alle fünf Sekunden,
  ohne Formulare zu überschreiben. Keine Seite-Refreshs.
- Zentrale Mantine-Farben/Inputs, QualityBadge und responsives Grid;
  Layout auf 320, 390, 820, 1280 px überprüft (keine horizontale
  Seitenüberbreite).

### Backend-Schema und Endpunkte

- Neu: **`GET /api/sockets/gui`** als lesendes, JSON-fähiges
  Konfigurationsmodell. Liefert vorhandene Socket-Konfigurationen,
  den Treiberkatalog, feldweise Defaults und Layout-Hinweise sowie
  zulässige Interfaces für die manuelle Tasmota-Suche.
- `pv2hash/socket_gui.py`: zentrales Socket-Feldschema für
  `simulator` und `tasmota_http`. Socket-Adapter unterstützen
  noch **keine eigenen DriverField-Schemas** wie Miner-Treiber.
  Bis zur eventuellen Vereinheitlichung liegt das Feldschema zentral
  und explizit hier; keine Tasmota-/Simulator-Sonderseiten in React.
  Das bestehende Legacy-HTML wird unverändert weiter angeboten.
- Der API-Response erlaubt nur explizit definierte
  Konfigurationsfelder. **Passwörter werden nicht übertragen**;
  die GUI zeigt lediglich `password_configured`, und ein leeres
  Passwortfeld sendet für bestehende Tasmota-Sockets `__KEEP__`.
  Miner-Zuordnungsinformationen werden auf Rollen-/Ziel-ID und
  Anzeigename begrenzt.
- Bestehende Schreibendpunkte unverändert:
  `POST /api/sockets/add`, `POST /api/socket/{id}/config`,
  `POST /api/socket/{id}/switch`,
  `POST /api/socket/{id}/delete` und
  `POST /api/sockets/discover/tasmota`.
- Kleine Core-Korrektur: Ein bewusst deaktiviertes
  Tasmota-`use_energy`-Checkboxfeld bleibt nach dem Speichern
  deaktiviert (ein fehlender Checkbox-Eintrag bedeutet `False`).
  Ein leerer Benutzername kann ebenfalls bewusst entfernt werden.

### Tests und Betrieb

- Backend: 3 Python-Unit-Tests zu Feldschema, Passwortmaskierung
  und begrenzten Assignment-Metadaten.
- Frontend: 19 Node-Tests erfolgreich, einschließlich
  Socket-FormData, Tasmota-Validierung, gespeicherter Passwörter
  und API-Fehlerbehandlung. TypeScript/Vite-Build erfolgreich.
- Praktischer End-to-End-Test auf `testvm01`: Simulator-Socket
  über React angelegt, ein- und ausgeschaltet, Konfiguration geändert
  und gespeicherte Leistung in den Live-Details bestätigt.
  Zweiter temporärer Simulator erfolgreich angelegt und gelöscht.
  Ein Socket `Socket GUI2 Test` bleibt zur visuellen Abnahme
  erhalten. Bestehende Batterie/Miner wurden nicht verändert.
- Tasmota-Eingabemaske und Such-Interface im Browser geprüft;
  **kein echter Netzwerkscan und keine Tasmota-Geräteaktionen
  durchgeführt**. Diese stehen vor Produktionsfreigabe noch aus.
- Backend auf `testvm01` unter `/srv/pv2hash-gui2-core`
  aktualisiert; bestehender Core 0.9.4 und Legacy-Seiten bleiben
  weiterhin erreichbar. Rückfall über gesicherte
  `/home/chatgpt/pv2hash-core-before-sockets-ui.py`
  plus Entfernen des Testmoduls `socket_gui.py` und
  Dienst-Neustart. Keine Änderungen am produktiven Hashpi.


## Einstellungen und System in GUI 2.0 (Phase 0.8)

Stand: **GUI 2.0.0-dev.13** auf `testvm01`, Core-Basis 0.9.4
mit den bereits in Phase 0.6/0.7 entwickelten Core-Erweiterungen.
**Keine neuen Backend-/Regleränderungen** für diese Phase.

### Einstellungen

- Route `/ui-next/einstellungen`, Datei `frontend/src/pages/Settings.tsx`.
  Sämtliche Abschnitte kommen aus `GET /api/settings/model`:
  Instanz, Standort, Regelung, Messwertausfall und DataLogger.
- Keine duplizierten Feldlisten in React:
  `frontend/src/configuration/ConfigFields.tsx` rendert die vom Core
  gelieferten generischen Felder (Text, Zahl, Auswahl, Checkbox, Passwort,
  URL), angegebene Breiten und Zusatztexte auf Mantine-Basis.
  Zentrale Formularvalidierung in `configuration/types.ts`.
- Änderungen bleiben lokal im Formular bis zum bewussten Speichern
  über `POST /api/settings/config`. Sichtbarer Ungespeichert-Badge,
  Verwerfen-Funktion und Warnung beim Verlassen. Live-/API-Aktualisierungen
  überschreiben keine nicht gespeicherten Eingaben.
- `POST /api/location/geocode` bleibt als explizite Aktion erhalten:
  Adresse auflösen, Koordinaten eintragen, dann separat speichern.
- Exporter-CRUD in `configuration/Exporters.tsx` mit
  `GET/POST /api/exporters/config`, `DELETE .../{id}`,
  `POST .../{id}/test` und `GET /api/exporters/status`.
  Felder und Layout kommen aus Exporter-Definitionen. Eine
  Verbindung muss vor dem Aktivieren gespeichert und erfolgreich getestet
  sein. Vorhandene Tokens werden nur als leere Passwortfelder angezeigt;
  bei bestehendem Ziel bedeutet leer unverändert.
- Löschen eines Exportziels benötigt eine Bestätigung.

### System

- Route `/ui-next/system`, Datei `frontend/src/pages/SystemPage.tsx`.
  Karten werden aus `GET /api/system/model` bereitgestellt:
  Sicherung & Wiederherstellung, Updates, Instanz, Host, DataLogger
  und Live-Konsole. Keine Host-Metriken auf dem Dashboard.
- Konfigurationsdownload über bestehenden
  `GET /system/config/export`, JSON-Konfigurationsimport über
  `POST /api/system/config/import`. Datei auswählen und ausdrücklich
  bestätigen; es erfolgt kein automatischer Upload.
- Updates über `POST /api/system/update-check` und
  `POST /api/system/self-update` mit expliziter Bestätigung;
  Fortschritt/Fehler im Mantine-Modal via
  `GET /api/system/self-update-status` (1,5-s-Polling bei offenem
  Overlay). Verfügbarkeit ausschließlich laut Core-`can_start` und
  `update_available`, **auf testvm01 ist Start deaktiviert**.
  Manueller Stable-Installationsbefehl und Release-Notizen bleiben
  als lesbare Informationen erhalten.
- Runtime neu laden (`POST /api/system/reload`) mit Bestätigung;
  Log-Level einstellen (`POST /api/system/logging`); Live-Konsole
  über `GET /api/logs` mit Filter, Autoscroll, Ein-/Ausblenden
  und Logdatei-Download `GET /api/logs/download`.
- Laufende Messwerte/Updates/Logs werden im Frontend aktualisiert,
  ohne die Seite periodisch komplett neu zu rendern oder Formulardaten
  zu überschreiben. Die Karte für die Live-Konsole bewahrt Filter und
  Scroll-Modus lokal.
- Der Vite-Entwicklungsproxy leitet den Sonderpfad
  `/system/config/export` an den Core weiter. Nach der Integration
  in die reguläre PV2Hash-Auslieferung ist dies unnötig.

### Prüfung und offene Freigabetests

- `npm run build` erfolgreich; `npm test`:
  **22 Tests** (inkl. generischer Feldmodelle, Pflichtfelder,
  Passwortbehandlung und JSON-/HTTP-Fehlerfällen).
- Browser Desktop/iPhone-/Tabletbreiten (320/390/820/1280 px):
  beide Seiten ohne horizontale Überbreite, keine JavaScript-Fehler.
  Alle fünf Einstellungsabschnitte und sechs System-Karten
  aus echten Core-0.9.4-Modellen geprüft.
- Nicht gespeicherten Instanznamen bearbeitet, Ungespeichert-Status
  verifiziert und verworfen; bestehender Exporter geöffnet,
  gespeichertes API-Token maskiert und Bearbeiten abgebrochen.
- Vorschau-Download `/system/config/export` nach Proxyanpassung:
  HTTP 200 / JSON. System-Update auf testvm01 korrekt deaktiviert,
  Import ohne ausgewählte Datei deaktiviert.
- **Bewusst nicht ausgeführt:** produktive Konfiguration überschreiben,
  Geocoding, Exporter-Test gegen fremde Dienste, Log-Level-Änderung,
  Konfigurationsimport, Runtime-Neuladen oder Self-Update.
  Schreibpfade und Update-Overlay sind zur weiteren kontrollierten
  Ende-zu-Ende-Abnahme vor Produktionsfreigabe noch zu prüfen.
- Bestehende HTML-Seiten `/settings`, `/system` und alle bisher
  betriebenen Endpunkte bleiben unverändert.
  Produktiver Hashpi unangetastet.


## Update-Manager / Release-IDs (GUI 2.0.0-dev.14)

**Status: Entwicklungs-Meilenstein, noch kein Release.**
Core bleibt auf testvm01 0.9.4; Produktions-Hashpis nicht angefasst.
Im GUI-Draft-PR wird der neue Updater vorbereitet.

### Einmaliger Migrationsweg

- Es gibt **drei aktive PV2Hash-Installationen**. Alle drei werden
  kontrolliert zunächst auf Core **0.9.5** (Brückenrelease) gebracht,
  bevor GitHub Latest auf das neue ID-Format wechselt.
- Das Brückenrelease muss über das **bisherige**
  GitHub-Release-Schema `v0.9.5` samt kompatiblem Archiv,
  Manifest und Prüfsummen installierbar bleiben.
- Es enthält aber bereits den neuen Release-ID-Checker,
  den erneuerten Installer/Helper und die GUI 2.0.
- Erst nach verifizierter 0.9.5-Installation auf allen drei Geräten
  folgt das erste Paket mit ID `rYYYY.MM.DD.N`. Kein
  permanenter zweiter Feed, keine ewig festgehaltene Legacy-Latest.
- Zur Unterscheidung: **Core SemVer** (Regler/API),
  **GUI SemVer** (React-Vorderseite),
  **Paket-Release-ID** (Auslieferung/Update).
  Eine zusätzliche Gesamt-SemVer gibt es nicht.

### Backend

- `pv2hash/release_identity.py`: Parser und Vergleichslogik für
  traditionelle `vX.Y.Z`-Tags und neue Paket-IDs
  `rYYYY.MM.DD.N`. Die Paket-ID wird unabhängig von der Core
  SemVer verglichen. Bestehende Installationen ohne Release-ID
  können auf `v0.9.5` über Core SemVer aktualisieren.
- `PV2HASH_RELEASE_ID` in `/etc/pv2hash/install.env`
  bildet künftig die **installierte Paketidentität**. Der
  Release-Installer speichert zusätzlich `PV2HASH_CORE_VERSION`
  und `PV2HASH_GUI_VERSION`, wenn diese im Manifest vorliegen.
- `pv2hash/update_check.py` versteht beide Tag-Arten. Bei
  neuen GitHub-Paketen werden die benötigten
  `core_version`/`gui_version` aus dem zugehörigen
  `manifest.json` geladen und überprüft. Die API bietet
  `release_id`, `installed_release_id`,
  `release_core_version` und `release_gui_version`.
- `pv2hash/self_update.py` akzeptiert neue Paket-Tags und
  prüft bei der Neustart-Recovery die **installierte Release-ID**.
  Gleich gebliebene Core SemVer alleine darf ein GUI-only-Update
  nicht fälschlich als erfolgreich markieren.
- Der bereits vorhandene 7-Phasen-Installer bleibt Grundlage.
  `GET /api/system/self-update-status` liefert jetzt zusätzlich
  `progress_percent`, berechnet aus **abgeschlossenen Schritten**,
  nicht geschätzter Installationszeit.
- `GET /api/system/update-log`: inkrementelle, begrenzte
  Konsolenzeilen aus der **festen** Datei `self_update.log`,
  mit Cursor, Dateiwechsel-Erkennung, Log-Rotation und
  Redaction üblicher Passwort-/Token-Muster. Der Browser kann
  keine beliebigen Dateipfade vorgeben.
- `GET /api/system/update-log/download`: auf maximal
  2 MiB begrenzter, vor Ausgabe maskierter Download
  des Installationsprotokolls; **kein** Rohdatei-Download.
- Die Installer-API bleibt weiter von einem geöffneten
  Browserfenster unabhängig.

### Frontend

- Menühinweis: ruhiger Text `Update verfügbar` und subtil
  pulsierender orangefarbener Punkt (respektiert Reduced Motion).
- Update-Karte an **erster Stelle** auf der Systemseite:
  Core-/GUI-Version, Paket-ID, Update-Prüfung, Release-Details.
- Overlay in `frontend/src/update/UpdateOverlay.tsx`:
  sieben reale Installationsphasen, aktueller Schritt, Fortschritt,
  aufklappbare ioBroker-artige Browserkonsole mit Textfilter,
  Auto-Scroll, Kopieren und Protokoll-Download.
- Zeigt beim zwischenzeitlichen Dienstausfall
  `Verbindung wird wiederhergestellt` statt sofort einen
  Installationsfehler. Nach Browser-Reload wird der Overlay-
  Zustand bei laufendem Update wieder aus dem Backend abgeleitet.
- Installationsprotokoll jederzeit erneut über die Systemkarte
  aufrufbar, auch nach Fehlschlag oder Seiten-Reload.
- **Reine Vorschau** ausschließlich im Vite-Entwicklungsmodus:
  `Update-Manager als Simulation ansehen`. Die sieben Schritte
  lassen sich manuell durchklicken und zeigen künstliche,
  ausdrücklich als SIMULATION markierte Logzeilen. Keine
  Installation wird durch diesen Knopf gestartet.
- Aktualisierungen starten weiterhin ausschließlich mit
  expliziter Bestätigung.

### Tests & offene Schritte zur 0.9.5

- Sechs neue Python-Unit-Tests (Release-IDs, Reihenfolge,
  ungültige Datumswerte, installierte ID, Neustart-Recovery,
  Log-Cursor/Rotation und Secret-Maskierung).
  Dazu zwei weitere Tests für vollständige simulierte GitHub-Checks
  (Legacy-0.9.5 und Paket-ID samt Manifest und Core-/GUI-Versionen).
  Die bestehenden acht Installer-/Fortschrittstests bestehen.
- Browser-Tests: Simulation mit echtem Overlay von Schritt 3
  auf Schritt 4, Fortschrittsanzeige, nachgeführte Konsole;
  Mobilbreite 390 px ohne horizontalen Überlauf;
  keine JS-Fehler.
- Test-Core auf testvm01 um lesende API-Funktionen erweitert,
  API antwortet HTTP 200, ohne echte Installer-Aktivität.
  Bestehende Dateien vorher mit
  `/home/chatgpt/pv2hash-dev14-before-*.py` gesichert.
- **Noch ausstehend und vor Release verpflichtend:**
  Erstellung eines **0.9.5-Pakets mit tatsächlich integrierter
  gebaute React-GUI**, Umsetzung der neuen paketbasierten
  Release-Pipeline inklusive GitHub-Manifests und Tag-Schema;
  vollständige Fake-GitHub-/Fake-Installer-End-to-End-Tests
  samt Dienstneustart und Fehler-Rollback;
  abschließende Review und Freigabe.
  Weder `v0.9.5` noch ein neues Paket-ID-Release wurde
  veröffentlicht.
