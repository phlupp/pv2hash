# PV2Hash – lernende PV-Prognose im Shadow Mode

**Stand: 20.10.2026 · Status: abgestimmtes Fachkonzept, noch keine Implementierung.**
Die Entwicklung erfolgt ausschließlich auf **testvm01**, zunächst ohne Änderung der produktiven Hashpis. Die lernende PV-Prognose ist vom ebenfalls geplanten **Batterie-/Mining-Energieplaner im Shadow Mode** zu unterscheiden. Beide bleiben vollständig von der aktiven Minerregelung entkoppelt.

Weiterführende Dokumente: [PV-Prognose und Generatorflächen](forecast-prototype.md), [Revisionsarchiv und Prognosequalität](forecast-quality.md), [Batterie-Ladeziel und Energieplaner](energy-planner.md), [lokale SQLite-Speicherung und zentraler Export](sqlite-storage.md).

## 1. Ausgangspunkt und gemeinsame Erkenntnisse

PV2Hash berechnet heute eine Wetter-/Einstrahlungsprognose auf Basis von Open-Meteo (Global Tilted Irradiance, GTI) und konfigurierten Generatorflächen. **Je Wechselrichter können mehrere Generatorflächen** mit eigenem kWp-Wert, Neigung, Azimut und pauschalem Verlustfaktor hinterlegt werden. Die Rohprognose entsteht pro Generatorfläche, wird je WR und schließlich zur Standortsumme aggregiert.

Das bisherige einfache Leistungsmodell berücksichtigt Temperatur, Wechselrichter-Clipping und lokale Verschattung noch nicht explizit. Die tatsächliche Erzeugung kann deshalb von der Vorhersage abweichen.

**Beobachtung am 20.10.2026:** In der ersten Grafana-Auswertung der Prognosequalität zeigt die Anlage HashPi@Phlupp über mehrere Stunden wiederholt deutliche positive absolute Fehler (grob im Bereich einiger kW) sowie einen auffälligen kurzfristigen Peak gegen Ende der Erzeugungszeit. Der Nutzer berichtet für seine Anlage eine **saisonal auftretende Abschattung durch ein Nachbarhaus** bei tief stehender Nachmittags-/Abendsonne, die im Frühling wieder verschwindet. Das ist eine **plausible Hypothese**, keine aus einem einzelnen Diagramm bewiesene Ursache.

**Ebenso wichtig:** Eine über längere Zeit annähernd gleichbleibende Abweichung legt zuerst **Anlagenparameter / Modellkalibrierung** als möglichen Grund nahe – kWp, Neigung, Azimut oder zu optimistischer Verlust-/Korrekturfaktor. Ein ausgeprägter Einzelpeak ist vorerst **Sonderfall/Anomalie**: mögliche Abschattung, kurzfristige Wetteränderung, Messfehler oder fehlende Modellphysik. Nicht automatisch jede negative Abweichung als Verschattung oder jede Spitze als Wetterfehler klassifizieren.

Ein erster Tagesverlauf ist keine ausreichende Evidenz für automatische Korrekturen. Besonders wichtig sind vergleichbare Tage, die Richtung des Fehlers, reale Messqualität, Sonnenstand und Konfiguration.

## 2. Verbindliche UI-Entscheidung: drei Kurven

Im **bestehenden Tages-Prognose-Diagramm** (heute / historische Tage / morgen) sollen drei klar unterscheidbare Zeitreihen stehen:

| Kurve | Bedeutung | Darstellung |
| --- | --- | --- |
| Bestehende Wetter-/Basisprognose | Unveränderte, anhand der aktuell hinterlegten Generatorparameter berechnete Prognose | Bestehender Stil (derzeit grau gestrichelt) |
| Tatsächlich gemessene PV-Erzeugung | Nur gültige Live-Istwerte | Bestehender Stil (derzeit gelb durchgezogen) |
| **PV2Hash Shadow-Prognose** | Parallel berechnete, ggf. lernend korrigierte PV-Prognose | **Lila, gestrichelt** |

- Darstellung für **die gesamte Standortsumme**, keine zusätzlichen Generator-Einzelkurven im Hauptdiagramm. Bestehende Navigation, automatische Aktualisierung, Zeitzonen- und kW-Darstellung erhalten.
- Tooltip/Legende müssen drei Reihen eindeutig unterscheiden; Shadow-Kurve soll ein-/ausblendbar sein, der Status **„Lernt noch / keine belastbare Korrektur“** deutlich erscheinen.
- Am Anfang darf Shadow mit der Basisprognose **identisch sein**, wenn noch kein validiertes Modell vorliegt. Keine künstliche Korrektur nur, damit die Linien unterschiedlich aussehen.
- Nur zukünftige Vorhersagepunkte dürfen bei einem neuen Abruf geändert werden. **Einmal abgelaufene, tatsächlich ausgegebene Shadow-Prognosen bleiben wie die Basisprognosen unverändert.** Historische Schattenkurven dürfen niemals mit dem neueren Modell rückwirkend „verbessert“ werden.
- **Unterschied zu Grafana:** Das jetzige MAE-Panel zeigt einen absoluten Fehlerverlauf, **nicht** die eigentlichen Basis-/Shadow-/Ist-Leistungskurven. Ein eigenes Grafana-Prognoseanalyse-Dashboard wird separat geplant.

## 3. Lernziel und Fehlerklassen

**A – Konfiguration / systematische Abweichung (vorrangig prüfen):**
- Gleichgerichteter Bias über viele vergleichbare Messpunkte/Tage oder stabiler Leistungsfaktor → kWp, Verlustfaktor, AC-/DC-Annahmen, Clipping, Messdefinition, unvollständige Generatorflächen.
- Tageszeitlich verschobenes Leistungsmaximum oder systematische Formabweichung → mögliche falsche Azimut-/Neigungsdaten, unterschiedliche Generatorausrichtungen oder Modellphysik.
- Das System darf eine **Diagnose bzw. begründeten Konfigurationshinweis** geben, aber **Konfigurationswerte nicht stillschweigend umschreiben**. Nur nachvollziehbare Vorschläge mit Datengrundlage und Konfidenz; keine definitive Ursachenbehauptung aus Bias allein.

**B – Sonnenstandsabhängige, wiederkehrende Mindererträge (spätere Stufe):**
- Berechnung von **Sonnenazimut und Sonnenhöhe** aus Anlagenkoordinaten und Zielzeitpunkt unter Berücksichtigung der lokalen Zeitzone und UTC-Speicherung.
- Prüfung, ob ähnliche Mindererträge bei vergleichbaren Sonnenständen **über mehrere geeignete Tage** auftreten. Jahreszeitlich wandernde Sonnenbahnen erklären das Auftreten im Herbst/Winter und Ausbleiben im Frühling/Sommer besser als feste Uhrzeit-/Monatsregeln.
- Geometrische Verschattung nur als wahrscheinliche Ursache kennzeichnen, wenn die Evidenz im Vergleich zu Wolken/anderen Effekten tragfähig ist. Auch saisonale Verschmutzung, Schnee, Temperatur, Clipping, begrenzte Netzleistung und defekte Sensorik berücksichtigen.

**C – Lokale/kurze Ausreißer (zunächst nicht „lernen“):**
- Einzelne starke Peaks, abrupte Sprünge oder kurzfristige Wolkeneffekte gesondert markieren, in der Diagnose anzeigen und für die robuste Grundkalibrierung möglichst nicht als dauerhaften Korrekturgrund missbrauchen.
- Nicht jeder Peak ist ein Vorhersagefehler des Wetterdienstes: Auch das Nachbarhaus kann ein abruptes, reproduzierbares Ereignis erzeugen. Ursache erst mit wiederholten Messungen beurteilen.

**Identifizierbarkeitsgrenze:** Die vorhandene zentrale Qualitätsauswertung vergleicht die **Standortsumme**. Aus einer Gesamt-PV-Istkurve kann man die Verschattung **einer bestimmten Generatorfläche nicht eindeutig bestimmen**. Das Modell darf zwar alle Generatorflächen im Basismodell einzeln berechnen, aber Korrekturen zunächst nur dort lernen, wo **tatsächlich passende, zeitlich gültige WR-/Generator-Messwerte** existieren. Sind diese nicht vorhanden, nur standortbezogene Korrekturen/Diagnosen mit entsprechendem Hinweis. Keine erfundenen Generator-Istwerte; keine automatische Zuschreibung an eine Fläche.

## 4. Geplanter Modellaufbau – bewusst einfach beginnen

**Stufe 0: Dateninventar und Baseline**
- Vorhandene GTI-/Prognosewerte, Generatorparameter, lokale Live-PV-Messwerte, Revisionen und Modell-/Buildstände prüfen.
- Datenlücken, Degradation, Nachtstunden, historische Konfigurationsänderungen sowie Frequenz von Providerabrufen und 15-/60-Minuten-Zielraster dokumentieren.
- Laufende Baseline unverändert lassen. Für unbekannte oder ungenügende Eingangsdaten keine Scheingenauigkeit ausgeben.

**Stufe 1: transparente Kalibrierung im reinen Shadow Mode**
- Robusten, konservativen Korrekturansatz mit wenigen nachvollziehbaren Parametern entwickeln, z. B. einen vorsichtig geschätzten systematischen Leistungs-/Verlustfaktor, **ohne** gespeicherte Generatorparameter automatisch zu ändern.
- Konfigurationswarnung und Shadow-Vorhersage getrennt ausweisen. Training nur anhand **zum Trainingszeitpunkt verfügbarer** Daten; Mindestanzahl geeigneter Tageslicht-Messungen und Begrenzung extremer Korrekturen festlegen.
- Bestehende **Basisprognose bleibt unverändert**, nur separate Shadow-Prognose ergänzen. Modellversion, Parameter, Trainingsfenster, Stichprobenzahl und Entscheidung nachvollziehbar protokollieren.

**Stufe 2: Sonnenstandsmodell**
- Wiederkehrende, geometrisch plausible Einbußen vorsichtig aus Sonnenazimut/-höhe lernen; Daten über unterschiedliche Sonnenstände/geeignete Tage sammeln.
- Keine starren Regeln wie „Oktober um 16:30“; bei Sonnenständen außerhalb zuverlässig gelernter Bereiche auf Basis-/einfache Shadow-Prognose zurückfallen.
- Eigene Korrektur pro WR/Generatorfläche **nur** bei messtechnisch identifizierbarer Datenbasis, ansonsten Standortkorrektur.

**Stufe 3: Vergleich / Modellverbesserung**
- Out-of-sample-/Walk-forward-Vergleich mit einer **zuvor ausgegebenen** Basis- und Shadow-Prognose auf derselben Zielzeit. Modellvarianten und Trainingszeitfenster vergleichen.
- Nur bei wiederholt nachgewiesener Verbesserung die Shadow-Version intern als bevorzugte Prognose kennzeichnen; **niemals automatisch die Minerregelung freischalten**.
- Komplexere Verfahren / KI erst erwägen, wenn ein transparenter Ansatz nicht ausreicht und genügend qualitätsgesicherte Daten existieren.

## 5. Datenvertrag, Zeit und Speicherung (Entwurfsanforderung)

Vorhanden: **data/forecast.sqlite** mit unveränderlich archivierten Basisrevisionen in **forecast_revisions / forecast_revision_points** und bewerteten Basisfehlern in **forecast_quality_samples**. Die historische Tageskurve entsteht aus **forecast_points**. Das 365-Tage-Revisionsarchiv und die aktuellen Qualitätsprüfungen (15/30/60/180 Minuten) dürfen nicht in ihrer Semantik geändert werden.

Für Shadow *neu zu entwerfen* (noch nicht implementiert):
- Eigene persistente, versionierte Modell-/Trainingsinformationen: Modellkennung, Zeitpunkt der Modellberechnung, Quelle und Zeitraum der Trainingsdaten, Parameter und Provenienz, Minimum-Sample-/Qualitätsstatus; keine geheimen Zugangsdaten.
- Separate **append-only Shadow-Ausgaben** je Ausgabezeit und Zielzeit, eindeutige Zuordnung zur Basisprognoserevision, ggf. WR-/Generatorbezug **nur soweit belegbar**, plus Modellversion und Diagnoseflag. Die schnell abrufbare Tageskurve muss abgeschlossene Zeitpunkte einfrieren.
- Erweiterung des Lesevertrags **GET /api/forecast/day** um eine optionale Shadow-Serie und geeigneten Modellstatus (Rückwärtskompatibilität zum bestehenden Frontend).
- Spätere Bewertung **für beide Modelle** an exakt denselben gültigen Zielzeiten und Vorlaufhorizonten. Basis und Shadow müssen **gleichzeitig vor dem Zielzeitpunkt** tatsächlich ausgegeben worden sein. Keine nachträglich trainierte Prognose zum historischen Gewinner erklären.
- **Bias = Prognose − Ist** (positive Werte = Überschätzung); **MAE** und **RMSE** in W; Stichprobenzahl und Abdeckungsgrad je Horizont. Tageslicht- und Gesamtansicht getrennt; gültige 0 W nachts dürfen kein dominierendes, künstlich gutes Tageslicht-MAE erzeugen. Tageslichtdefinition nachvollziehbar und über alle Modelle konsistent.
- **Gültige Ist-Messungen nur bei pv_quality=live**; fehlende/degradierte Werte bleiben unbekannt, keine Rückrechnung aus Wetterdaten. Bestehende Nähe-/Vorlaufgrenzen der Qualitätsauswertung (±60 s Ist-Zielzeit, 15/30/60/180 Minuten plus definierte Toleranzen) übernehmen.
- Bei fehlender Trainingsgrundlage/Abdeckung: Shadow = Baseline mit explizitem „noch nicht gelernt“ oder Shadow nicht verfügbar; kein erfundener Prognosefehler, keine automatische Verwendung in einer produktiven Regelung.
- Die Datenmenge der zusätzlichen Revisionen, Datenbank-/WAL-Zunahme, Speicherung, Migration und Aufbewahrungsgrenzen vor Implementierung spezifizieren, SQLite-Status um Shadow-Anteil ergänzen; keine unbeschränkten Eventlogs.

Zentraler InfluxDB-Exporter sendet gegenwärtig nur **Qualitätswerte des Basismodells** in die Messreihe **pv2hash_forecast_quality**. Er überträgt **keine vollständigen Forecast-Revisionen**. Shadow-Qualität für ein zukünftiges internes Grafana-Prognoseanalyse-Dashboard braucht einen **explizit geplanten, opt-in und rückwärtskompatiblen Exportvertrag**; die Baseline-Daten dürfen dadurch nicht überschrieben werden.

## 6. Qualitäts- und Sicherheitskriterien

1. Die bestehende reaktive Mining-Regelung, ihre Batterie-/Netz-Schutzmechanismen, Miner-/Socket-Befehle und der Batterie-Energieplaner bleiben **vollständig unverändert**. Shadow ist Berechnung, Speicherung, Bewertung und Anzeige – nicht Regelung.
2. Kein Cloud-Training und kein undokumentiertes „Black Box“-Lernen in der ersten Stufe. Schätzungen mit klarer Herkunft, Datenumfang und Unsicherheit.
3. Fehlende/ungültige Messungen, wechselnde PV-Konfigurationen, Nacht, niedrige Leistungswerte, saisonale Effekte, DST und API-Ausfälle gesondert testen.
4. Keine Rückschreibung in abgelaufene Basis- oder Shadow-Prognosen; **Train/Test-Trennung nach Zeit**, nicht zufälliger Mix zukünftiger Werte. Wiederholte Läufe idempotent, Neustarts möglich.
5. Für jeden Auswertungszeitpunkt: identische gültige Ist-Werte für beide Modelle, gleiche Vorlaufzeit, vorherige Ausgabezeit; nach Möglichkeit getrennte Abnahme bei 15/30/60/180 Minuten und positiven/negativen Bias.
6. Keine Überanpassung an einen einzelnen sonnigen, bewölkten oder verschatteten Tag; konservativer Fallback, bei Unsicherheit keine selbstsichere Verschattungsdiagnose.
7. Erst auf **testvm01** entwickeln und testen (Unit-/Integrations-/GUI-Tests, kontrollierter Probedurchlauf); PR auf GitHub, Regressionen und zentrale Dokumentation aktualisieren. Release, Merge, produktives Hashpi oder andere Hashpis nur nach gesonderter Freigabe.

## 7. Konkreter Startumfang des nächsten Entwicklungs-Chats

1. Aktuelles main, Testumgebung **testvm01**, Forecast-Adapter/Store/Qualitäts-Worker/API/ECharts und Konfigurationsmodell inventarisieren; keine produktiven Zugriffe verändern.
2. Speicherkonzept und minimalen API-Vertrag für append-only Shadow-Kurve samt Modell-/Trainingsstatus festlegen. Zuerst klären, welche **WR-Istwerte** verfügbar sind; Aggregation respektieren.
3. **Phase 1 als einfaches nachvollziehbares Kalibriermodell** aufbauen, mit konservativem Null-/Baseline-Fallback, Warnung bei systematischem Konfigurationsverdacht und Testdatensätzen für stabilen Bias versus Einzelpeak.
4. Die **lila gestrichelte Shadow-Kurve** im bestehenden Tageschart ergänzen; ausschließlich zur Anzeige, im Tooltip klar benennen.
5. Baseline-vs-Shadow-Qualitätsvergleich und zeitlich saubere Regressionstests auf testvm01, bevor weitere Lerneffekte, Verschattungskarte oder Influx-/Grafana-Erweiterungen erfolgen.

## 8. Noch bewusst offen

- Exakte Trainingsfenster, Mindeststichproben und Konfidenz-/Verbesserungsschwellen.
- Erforderliche Messtrennung pro WR/Generatorfläche und eventuell nötige neue Messquellen.
- Umrechnung/Einbeziehung von Temperatur, DC-/AC-Leistung, Clipping und Anlagenbegrenzung.
- Genaue Datenbanktabellen, Versions-/Migrationsstrategie, Aufbewahrungsfrist des Shadow-Archivs.
- Definition „Tageslicht“ und geeignete Robustheitsmethoden für kleine Leistungen an Sonnenauf- und -untergang.
- Zentraler Shadow-Qualitätsexport und Aufbau eines **eigenen internen Grafana-Prognoseanalyse-Dashboards** nach validierter Datenbasis.
