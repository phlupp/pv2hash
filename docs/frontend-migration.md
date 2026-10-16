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
