# PV2Hash – Energieplaner: Batterie-Ladeziel und Shadow Mode

**Status (19.10.2026): Beschlossene fachliche Vorgabe, noch keine Implementierung.**
Entwicklung und Erprobung ausschließlich auf `testvm01`; die produktive
Miner-/Batterieregelung bleibt unverändert. Die PV-Prognose dient vorerst
nur als Informations- und Datengrundlage.

**Zwei getrennte Shadow-Modelle:** Die geplante
[lernende PV-Shadow-Prognose](forecast-shadow-learning.md) soll eine
bessere PV-Leistungskurve vorhersagen und bleibt selbst read-only;
der hier beschriebene Energieplaner soll später Batterie-/Mining-Abläufe
simulieren. Keines der beiden Modelle steuert ohne gesonderte Freigabe Miner.

## Verbindliche fachliche Grundeinstellung

| Parameter | Vorgabe | Änderbarkeit |
| --- | --- | --- |
| Ziel-Ladezustand | **100 % SOC** | Einstellbar |
| Bezugszeitpunkt | **Lokaler Sonnenuntergang** | Einstellbar; später optional feste Uhrzeit |
| Zeitverschiebung | **−120 Minuten (−2 h)** | Einstellbar; `0` = genau Sonnenuntergang |
| Zielsetzung | Batterie soll bis zum Zielzeitpunkt den Ziel-SOC erreichen | Planung, keine Ladegarantie |

**Semantik:** `Zielzeit = Sonnenuntergang am Anlagenstandort + Offset`.
Ein negativer Offset bedeutet *vor* Sonnenuntergang. Beispiel:
Sonnenuntergang **18:20 Uhr** lokal, Offset **−2 h** →
Ziel-SOC **100 % bis 16:20 Uhr** lokal. Das Ziel wird jeden Tag anhand
Standortkoordinaten und der lokalen IANA-Zeitzone neu bestimmt. Die
interne Speicherung und Rechnung erfolgt mit eindeutigen UTC-Zeitpunkten
(DST-/Winterzeitwechsel beachten). Ohne verlässliche Standort-/Zeit- oder
Sonnenuntergangsdaten darf keine Zielzeit erfunden werden.

Die Werte sind **Voreinstellungen**, keine fest kodierten Regeln. Der
Benutzer soll später SOC und Zielzeitmodus/Offset getrennt ändern können.
Eine alternative feste lokale Uhrzeit ist als zukünftige Option vorgesehen;
sie ist **nicht** die beschlossene Grundeinstellung.

## Fachliche Planungsentscheidung

Der Planer soll nicht erst warten, bis die Batterie voll ist. Bereits
morgens darf er **rein rechnerisch** eine teilweise PV-Energienutzung zum
Mining vorschlagen, wenn die prognostizierte Energie die Batterie mit
angemessener Sicherheitsreserve rechtzeitig auf den Ziel-SOC bringen
sollte. Die Batterie kann währenddessen langsamer laden. Prognostizierte
Leistung zum passenden Zeitpunkt und zeitlich erreichbare Batterieladung
sind wichtiger als eine reine Summe täglicher kWh.

Ein erreichbares Ladeziel ist **keine Garantie**: Der Planer muss
Unsicherheiten, Wetterabweichungen, Messausfälle und Prognosequalität
berücksichtigen und das Ziel als erreichbar / gefährdet / nicht
verlässlich bewertbar / voraussichtlich unerreichbar kennzeichnen.
Wenn das Ladeziel gefährdet wäre, bleibt die Batterieladung prioritär.

## Benötigte Eingangsdaten und Qualitätsgrenzen

- PV-Prognose im 15-Minuten-Raster einschließlich Generatorausrichtungen,
  Zeitstempeln, Prognoseausgabezeit und Fehler-/Unsicherheitsabschätzung.
- Aktueller SOC, gültige Batterie-Qualität, **nutzbare** Batteriekapazität
  (nicht unkritisch die nominelle Kapazität), max. Ladeleistung,
  Ladewirkungsgrad, ggf. Ladeleistungsbegrenzung nahe 100 %.
- Aktuelle Netzwirkleistung (Vorzeichen vereinheitlichen), PV-Erzeugung,
  Batterie-Lade-/Entladeleistung und gesamte Minerleistung.
- Geschätzter Hausverbrauch **ohne Miner**; anfangs aus vorhandenen
  Messgrößen rekonstruieren, dann Prognose/Lastprofil und Unsicherheit
  separat entwickeln. Keine Gleichsetzung von PV-Erzeugung mit frei
  nutzbarem PV-Überschuss.
- Nutzbare Minerprofile, Wechselzeiten, Hysterese, minimale Laufzeiten
  sowie bereits bestehende Batterie-/Netzbezugs-Sicherheitsgrenzen.

**Vorab geprüft:** PV2Hash hat SOC sowie Lade-/Entladeleistung, die
Netzwirkleistung, PV-Leistung und Minerleistung im DataLogger. Der
Batterie-Modbus-Treiber kann die Nennkapazität optional auslesen,
stellt sie aktuell jedoch über seine Diagnoseinformationen bereit.
Für den Planer muss eine belastbare **nutzbare** Kapazität samt
Konfigurations-/Validierungsweg erst eingeführt werden. Bei fehlenden
oder veralteten Pflichtdaten: `Planung nicht verfügbar` statt
scheinpräziser Prognose oder stillschweigender Nullannahmen.

## Geplante Berechnung (zunächst ausschließlich Simulation)

1. Täglich den lokalen Zielzeitpunkt aus Sonnenuntergang und Offset
   bestimmen und die Batterie-Restenergie bis zum Ziel-SOC abschätzen.
2. Die prognostizierte PV-Leistung, Hausgrundlast, Ladeleistungsgrenzen
   und Minerprofile auf einem 15-Minuten-Zeitraster bis zum Zielpunkt
   simulieren. Ladeverluste und Ladeverhalten nahe 100 % berücksichtigen.
3. Verschiedene Minerprofile/Zeitfenster gegenrechnen: resultierender
   SOC zum Zielzeitpunkt, voraussichtlicher Netzbezug,
   verfügbares Mining-Energiebudget und Sicherheitsreserve.
4. Nur als **Empfehlung / Shadow Mode** speichern, was bei gültigen
   Eingangsdaten plausibel und sicher erscheint – niemals direkt
   Miner oder Batterien schalten.
5. Tatsächlichen Verlauf und die simulierten Entscheidungen später mit
   den zuvor archivierten Prognoserevisionen vergleichen. Prognosegüte
   für 15/30/60/180 Minuten im Voraus auswerten; Sicherheitsreserven
   erst mit gemessenen Fehlerverteilungen sinnvoll kalibrieren.

**Sicherheitsgrenze:** Die bestehende reaktive Regelung und ihre
Schutzmechanismen (Netzbezug, Batterieentladung, Datenverlust, Profile,
Haltezeiten) werden durch diese Planung weder verändert noch umgangen.
Eine spätere produktive Regelungsintegration erfordert einen gesonderten
Entwurf, Tests und eine ausdrückliche Freigabe.

## Abnahmekriterien für zukünftige Implementierung auf `testvm01`

- Konfiguration/Anzeige: `100 %`, `Sonnenuntergang`, `−2 h` ist die
  Grundeinstellung; beide Dimensionen lassen sich unabhängig ändern.
- Beispiel: lokaler Sonnenuntergang 18:20 → Zielzeit 16:20;
  Offset 0 → 18:20; Offset −120 Minuten → 16:20.
- Unabhängige, zeitzonensichere Berechnung an Tagen mit 23 und 25
  Stunden und bei ganzjährig wechselnden Sonnenuntergangszeiten.
- Fehlende Orts-, Batterie-, Hauslast- oder Qualitätsinformationen
  führen zu klarer Teil-/Nichtverfügbarkeitsanzeige, **nicht** zu
  impliziter Minerfreigabe.
- Die Simulation berücksichtigt Zeitfenster und reale Ladegrenzen,
  nicht nur die gesamte Tagesenergiebilanz.
- Prognosefehler, gefährdetes SOC-Ziel und schwankende PV-Leistung
  werden im Shadow Mode nachvollziehbar protokolliert.
- Test auf `testvm01`; alle produktiven Prozesse, Minerprofile,
  Datenbanken und Steuerbefehle bleiben unberührt.

## Reihenfolge

1. Prognose-Revisionen unveränderlich archivieren und Prognosefehler
   ohne Beeinflussung der Live-Prognose auswerten.
2. Nutzbare Batteriekapazität/Ladegrenzen und Hausgrundlast als
   qualitätsgeprüfte Planer-Eingaben vorbereiten.
3. Batterie-/PV-/Mining-Zeitplan im **Shadow Mode** entwickeln,
   vergleichen und fortlaufend verbessern.
4. Erst nach separater fachlicher Freigabe über produktive Regelung
   entscheiden.
