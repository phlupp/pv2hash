# PV2Hash-Regler

Stand: PV2Hash 0.7.7 mit Regler-Anpassung für stufenweises Batterie-Entladen.

Diese Dokumentation beschreibt den aktuellen Aufbau des Reglers, die Prioritäten und typische Beispiele. Ziel ist, spätere Änderungen am Regler nachvollziehbar und sicher durchführen zu können.

## Ziel

PV2Hash steuert Miner-Profile anhand des PV-Überschusses am Netzanschlusspunkt.

Grundprinzip:

```text
Netzeinspeisung  -> Miner dürfen hochregeln
Netzbezug        -> Miner müssen runterregeln
Batterie lädt    -> Batterie wird bevorzugt, blockiert echten Netzexport aber nicht mehr hart
Batterie entlädt -> Batterieprofil wird aktiv genutzt, oberhalb davon stufenweise reduziert
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
5. Harte Batterie-Limits anwenden, falls Batterieentladung nicht erlaubt ist oder SOC fehlt/zu niedrig ist
6. Batterie-Zielprofil anwenden, falls erlaubt und das aktuelle Profil darunter liegt
7. Bei Batterieentladung oberhalb des Entladeprofils stufenweise bis zum Entladeprofil runterregeln
8. Netzbezug prüfen und nach Hold-Zeit runterregeln
9. Netzeinspeisung prüfen und bei ausreichendem Überschuss hochregeln
10. Mindest-Schaltintervall beachten
11. Ergebnisprofile setzen oder Zustand halten
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

Batterieentladung ist eine bewusste Betriebsfreigabe mit Schutzgrenze.

Pro Miner wird geprüft:

```text
Darf der Miner bei Batterieentladung laufen?
Ist ein SOC-Wert vorhanden?
Ist der SOC hoch genug?
Welches Profil ist bei Entladung vorgesehen?
```

Wenn Entladung nicht erlaubt ist, der SOC fehlt oder der SOC zu niedrig ist, wird der Miner auf sein kleinstes geregeltes Profil begrenzt. Diese Fälle bleiben harte Limits.

Beispiel:

```text
Batterie entlädt
SOC = 40 %
Mindest-SOC Entladung = 60 %
Miner-Floor = off

-> Miner wird auf off begrenzt
```

Wenn Entladung erlaubt ist und der SOC hoch genug ist, ist das Entladeprofil das gewünschte Zielniveau für Batteriebetrieb. Der Regler nutzt dieses Profil aber nicht mehr als harte Sofort-Kappung, solange der Miner oberhalb davon läuft. Stattdessen wird stufenweise heruntergeregelt.

Beispiel:

```text
Batterie entlädt
SOC = 80 %
Profil bei Entladung = p1
Miner läuft aktuell auf p3

-> erster Regelschritt: p3 -> p2
-> nach Mindest-Schaltintervall, falls Batterie weiter entlädt: p2 -> p1
-> bei p1 wird gehalten
```

Läuft der Miner unterhalb des Entladeprofils, darf der Regler ihn bewusst bis zum Entladeprofil hochregeln, sofern die übrigen Schutzbedingungen erfüllt sind.

Beispiel:

```text
Batterie entlädt
SOC = 80 %
Profil bei Entladung = p1
Miner läuft aktuell auf off

-> Miner darf auf p1 gehen
```

Während aktiver Batterieentladung wird oberhalb des Entladeprofils nicht normal hochgeregelt. Erst wenn die Batterie nicht mehr entlädt oder wieder echte Netzeinspeisung über die normale Netzanschlusslogik nutzbar ist, übernimmt wieder der reguläre Step-Up-Pfad.

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
-> Entladeprofil wird aktiv
-> Miner wird nicht sofort hart gekappt
-> Regler reduziert in normalen Regelschritten bis zum konfigurierten Entladeprofil, z. B. p1
```

Damit wird die Übergangsphase ruhiger: ein kleiner Batteriebezug führt nicht mehr sofort zu einem großen Leistungssprung nach unten und anschließendem erneuten Hochregeln.

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
    bewusster Batteriebetrieb bis zum Entlade-SOC
    unterhalb des Entladeprofils darf hochgeregelt werden
    oberhalb des Entladeprofils wird stufenweise reduziert
    harte Limits gelten weiterhin, wenn Entladen nicht erlaubt ist oder SOC fehlt/zu niedrig ist

Batterie lädt:
    weiche Priorisierung
    Batterie wird bevorzugt
    echter Netzexport darf aber genutzt werden

Mindest-Schaltintervall:
    verhindert zu häufige Wechsel

Source-Loss:
    definiertes Sicherheitsverhalten bei Messwertausfall
```
