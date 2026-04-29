# PV2Hash-Regler

Stand: PV2Hash 0.6.14 mit Regler-Anpassung für Batterie-Laden und Netzeinspeisung.

Diese Dokumentation beschreibt den aktuellen Aufbau des Reglers, die Prioritäten und typische Beispiele. Ziel ist, spätere Änderungen am Regler nachvollziehbar und sicher durchführen zu können.

## Ziel

PV2Hash steuert Miner-Profile anhand des PV-Überschusses am Netzanschlusspunkt.

Grundprinzip:

```text
Netzeinspeisung  -> Miner dürfen hochregeln
Netzbezug        -> Miner müssen runterregeln
Batterie lädt    -> Batterie wird bevorzugt, blockiert echten Netzexport aber nicht mehr hart
Batterie entlädt -> Batterieschutz greift und begrenzt Miner
```

Die zentrale Führungsgröße ist immer der Netzanschlusspunkt. Die Batterie ist eine zusätzliche Schutz- und Priorisierungslogik.

## Netzleistung

`grid_power_w` wird so interpretiert:

```text
grid_power_w > 0  = Netzbezug
grid_power_w = 0  = ausgeglichen
grid_power_w < 0  = Einspeisung
```

Beispiele:

```text
grid_power_w =  300 W   -> 300 W Netzbezug
grid_power_w = -1200 W  -> 1200 W Einspeisung
```

## Profile

Miner werden über Profile geregelt:

```text
off -> p1 -> p2 -> p3 -> p4
```

Die reale Leistung pro Profil kommt aus der Miner-Konfiguration. Ein Miner kann außerdem einen Floor haben. Dann ist das kleinste geregelte Profil nicht zwingend `off`.

## Grundprioritäten

Vereinfacht arbeitet der Regler in dieser Reihenfolge:

```text
1. Messwertqualität prüfen
2. Bei Messwertausfall Source-Loss-Verhalten anwenden
3. Bei Live-Werten Batterie-Kontext bestimmen
4. Batterie-Policies pro Miner berechnen
5. Batterie-Limit anwenden, falls aktiv
6. Batterie-Zielprofil anwenden, falls erlaubt
7. Netzbezug prüfen und nach Hold-Zeit runterregeln
8. Netzeinspeisung prüfen und bei ausreichendem Überschuss hochregeln
9. Mindest-Schaltintervall beachten
10. Ergebnisprofile setzen oder Zustand halten
```

## Messwertausfall

Wenn die Quelle nicht live ist, läuft nicht der normale Regler. Stattdessen wird das konfigurierte Source-Loss-Verhalten verwendet.

Mögliche Modi:

```text
off_all       -> alle Miner auf off
hold_current  -> aktuelle Profile halten
force_profile -> definiertes Fallback-Profil setzen
```

Bei `hold_current` und `force_profile` kann optional eine Haltedauer gesetzt werden. Danach fällt der Regler auf `off_all` zurück.

## Netzbezug

Netzbezug ist die wichtigste Bremse.

Der Regler nutzt:

```text
max_import_w
import_hold_seconds
switch_hysteresis_w
```

Beispiel:

```text
max_import_w = 200 W
import_hold_seconds = 15 s
```

Ablauf:

```text
Netzbezug <= 200 W
-> halten

Netzbezug > 200 W
-> Import-Hold startet

Netzbezug bleibt mindestens 15 s über 200 W
-> Regler schaltet einen Schritt runter
```

Kurze Spitzen führen dadurch nicht sofort zu Profilwechseln.

## Netzeinspeisung

Bei Einspeisung darf der Regler hochschalten, wenn genug Reserve für den nächsten Profilschritt vorhanden ist.

Vereinfacht:

```text
benötigte Einspeisung = Leistung nächster Schritt + switch_hysteresis_w
```

Beispiel:

```text
aktuelles Profil: p1
nächster Schritt: p2
Mehrleistung p1 -> p2: 700 W
Hysterese: 100 W
Netzeinspeisung: 1000 W

benötigt: 800 W
vorhanden: 1000 W

-> Step-Up erlaubt
```

## Mindest-Schaltintervall

`min_switch_interval_seconds` verhindert zu häufige Profilwechsel.

Beispiel:

```text
Mindestintervall: 120 s
letzter Wechsel: vor 45 s
Regler möchte hochschalten

-> Wechsel wird unterdrückt
```

## Verteilstrategie

### Equal

Bei `equal` werden aktive Miner möglichst gemeinsam stufenweise bewegt.

Beispiel:

```text
Miner 1: p1 -> p2
Miner 2: p1 -> p2
Miner 3: p1 -> p2
```

Vorteil: gleichmäßige Miner-Auslastung.

Nachteil: Schritte können groß sein.

### Cascade

Bei `cascade` wird nach Priorität geregelt.

Hochregeln:

```text
Miner 1 zuerst
danach Miner 2
danach Miner 3
```

Runterregeln:

```text
Miner 3 zuerst
danach Miner 2
danach Miner 1
```

Vorteil: feinere Schritte.

Nachteil: Miner laufen absichtlich unterschiedlich stark.

## Batterie-Kontext

Der Regler erkennt einen Batteriemodus:

```text
charging     -> Batterie lädt
discharging  -> Batterie entlädt
inactive     -> Batterie weder lädt noch entlädt
```

Die Erkennung nutzt die vom Source-Treiber gelieferten Flags oder die gemessene Lade-/Entladeleistung mit Schwellwerten.

## Batterie entlädt

Batterieentladung ist ein Schutzmodus.

Pro Miner wird geprüft:

```text
Darf der Miner bei Batterieentladung laufen?
Ist ein SOC-Wert vorhanden?
Ist der SOC hoch genug?
Welches Profil ist bei Entladung erlaubt?
```

Wenn Entladung nicht erlaubt ist, der SOC fehlt oder der SOC zu niedrig ist, wird der Miner auf sein kleinstes geregeltes Profil begrenzt.

Beispiel:

```text
Batterie entlädt
SOC = 40 %
Mindest-SOC Entladung = 60 %
Miner-Floor = off

-> Miner wird auf off begrenzt
```

Wenn Entladung erlaubt ist und der SOC hoch genug ist:

```text
Batterie entlädt
SOC = 80 %
Profil bei Entladung = p1

-> Miner wird maximal auf p1 begrenzt
```

Diese Regel bleibt absichtlich hart. Sie verhindert, dass Miner die Batterie leerziehen, auch wenn am Netzanschlusspunkt noch kein Netzbezug sichtbar ist.

## Batterie lädt

Batterieladung ist eine Priorisierungslogik.

Pro Miner wird geprüft:

```text
Darf der Miner bei Batterieladung laufen?
Ist ein SOC-Wert vorhanden?
Ist der SOC über dem Mindest-SOC Laden?
Welches Profil ist bei Ladung konfiguriert?
```

Wenn die Bedingungen erfüllt sind, kann der Regler das Batterie-Ladeprofil als Zielprofil verwenden.

Beispiel:

```text
Batterie lädt
SOC = 95 %
Mindest-SOC Laden = 90 %
Profil bei Laden = p1

-> Miner darf auf p1 gehen
```

## Anpassung: Batterie-Laden blockiert echten Netzexport nicht mehr hart

Früher war das Batterie-Ladeprofil gleichzeitig Zielprofil und harte Obergrenze. Dadurch konnte folgender Fall entstehen:

```text
Batterie fast voll
Batterie lädt noch leicht
Netzanschluss speist bereits ein
Miner bleibt trotzdem auf dem Batterie-Ladeprofil
```

Das führte dazu, dass vorhandener Netzexport nicht genutzt wurde, bis die Batterie vollständig inaktiv wurde.

Die aktuelle Logik unterscheidet deshalb:

```text
Batterie lädt + keine echte Netzeinspeisung
-> Batterie-Ladeprofil bleibt die Obergrenze

Batterie lädt + echte Netzeinspeisung über der Hysterese
-> Batterie-Ladeprofil bleibt Ziel/Freigabe
-> harte Obergrenze wird gelockert
-> normaler Netzanschluss-Regler darf weiter hochregeln
```

"Echte Netzeinspeisung" bedeutet:

```text
-grid_power_w > switch_hysteresis_w
```

Dadurch reagiert der Regler nicht auf kleine Messwertschwankungen.

## Beispiel: Batterie wird voll und PV-Überschuss entsteht

Ausgangslage:

```text
SOC = 98 %
Batterie lädt noch mit 200 W
Netzanschluss speist 1200 W ein
Miner läuft auf p1
Batterie-Ladeprofil = p1
Hysterese = 100 W
```

Bewertung:

```text
Batterie lädt
SOC ist hoch genug
Netzeinspeisung ist größer als Hysterese
-> Ladeprofil blockiert nicht mehr als harte Obergrenze
-> Step-Up darf anhand des Netzexports geprüft werden
```

Wenn der nächste Profilschritt z. B. 700 W benötigt:

```text
benötigt: 700 W + 100 W Hysterese = 800 W
vorhanden: 1200 W Einspeisung

-> Miner darf hochregeln
```

## Beispiel: PV sinkt später wieder

Später kommt eine Wolke:

```text
PV-Leistung sinkt
Miner läuft auf p3
Batterie beginnt zu entladen
Netzanschluss ist noch nahe 0 W, weil die Batterie puffert
```

Dann greift die Batterie-Entlade-Regel:

```text
Batterie entlädt
-> Batterieschutz aktiv
-> Miner wird auf das konfigurierte Entladeprofil begrenzt, z. B. p1 oder off
```

Damit bleibt die Batterie geschützt, auch wenn am Netzanschluss noch kein Netzbezug sichtbar wird.

## Beispiel: Batterie lädt, aber kein Netzexport

```text
SOC = 95 %
Batterie lädt mit 2000 W
Netzleistung = 0 W
Batterie-Ladeprofil = p1
Miner läuft auf p1
```

Bewertung:

```text
Batterie lädt
kein echter Netzexport
-> Ladeprofil bleibt Obergrenze
-> Miner bleibt auf p1
```

So bleibt die Batterie vorrangig.

## Beispiel: Netzbezug entsteht

```text
Miner läuft hoch
PV fällt stärker ab
Netzbezug steigt auf 300 W
max_import_w = 200 W
import_hold_seconds = 15 s
```

Ablauf:

```text
Netzbezug > max_import_w
-> Import-Hold startet

Netzbezug bleibt länger als 15 s über max_import_w
-> Regler schaltet runter
```

Netzbezug bleibt die oberste Bremse.

## Startverhalten der Runtime

Beim Start oder Neustart der Runtime darf PV2Hash nicht blind mit `off` starten.

Der aktuelle Miner-Zustand wird zuerst gelesen und als Live-Zustand übernommen. Diese Logik ist wichtig und darf bei späteren Performance-Optimierungen nicht entfernt werden.

## Kurzfassung

```text
Netzanschluss:
    harte Führungsgröße
    Netzbezug reduziert immer

Batterie entlädt:
    harte Schutzlogik
    Miner werden begrenzt

Batterie lädt:
    weiche Priorisierung
    Batterie wird bevorzugt
    echter Netzexport darf aber genutzt werden

Mindest-Schaltintervall:
    verhindert zu häufige Wechsel

Source-Loss:
    definiertes Sicherheitsverhalten bei Messwertausfall
```
