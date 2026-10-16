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
- Alle weiteren Seiten sind ehrlich als Platzhalter markiert.
- **Keine schreibenden APIs. Keine Änderung am Regler. Kein Produktionsdeployment.**

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
