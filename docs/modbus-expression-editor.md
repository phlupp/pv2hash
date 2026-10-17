# Modbus-Registerformeln (Prototyp auf testvm01)

## Architektur

- **`frontend/src/formula/`** stellt einen generischen Formeleditor bereit. Der eigentliche `FormulaEditor` verwaltet ausschließlich Variable-Badges,
  Zahlen, Operatoren und die Formel; er kennt keine Modbus-Adressen. Der
  PV-spezifische Wrapper `frontend/src/sources/ModbusCalculationEditor.tsx`
  ergänzt die Registerkonfiguration, die künftig auch andere Adapter nutzen
  können. Das Feldschema `type: modbus_calculation` aktiviert diesen Wrapper.
  Registerschema und Modbus-Validierung liegen getrennt in
  `frontend/src/sources/modbusFormulaModel.ts`; die universelle Komponente
  kennt nur Tokens und eine Liste von Variablen.
- **`pv2hash/sources/expression.py`** kompiliert und wertet ausschließlich
  geprüfte, serialisierte Tokens aus. **Kein `eval`**, keine Python-Ausdrücke,
  Funktionsaufrufe oder Netzwerkkontakte. Rechenzeichen: `+`, `-`, `*`, `/`,
  `(` und `)`; Literale: endliche numerische Werte. Punkt-vor-Strich.
- Der PV-Modbus-Adapter ist der erste Verbraucher. Er liefert wie bisher
  **genau einen Leistungsmesswert** an die PV-Gruppe. Alle Register einer
  Berechnung werden innerhalb desselben Lesevorgangs gelesen; fehlt ein
  Messwert, werden keine unvollständigen Werte als LIVE publiziert.
- Der Berechnungsmodus verlangt **mindestens zwei verschiedene tatsächlich
  referenzierte Modbusregister**; bis zu 16 Register und 96 Formeltokens.
  Register enthalten stabile `id` und änderbaren `name`, `address`,
  `register_type`, `value_type`, `endian` und `factor`.
- Die PV-Gruppe enthält die letzten vollständigen Registerwerte in ihrem
  atomaren Gerätesnapshot; in der GUI stehen sie ausschließlich bei
  **Messdetails**. Die Prognose liest weiterhin die Gesamt-PV-Messung.

## Konfigurationsformat

```json
{
  "power_mode": "calculated",
  "calculation": {
    "registers": [
      {"id":"ac","name":"AC","address":30775,"register_type":"input","value_type":"int32","endian":"big_endian","factor":1},
      {"id":"charge","name":"Laden","address":31393,"register_type":"input","value_type":"uint32","endian":"big_endian","factor":1},
      {"id":"discharge","name":"Entladen","address":31395,"register_type":"input","value_type":"uint32","endian":"big_endian","factor":1}
    ],
    "tokens": [
      {"type":"variable","id":"ac"},"+",
      {"type":"variable","id":"charge"},"-",
      {"type":"variable","id":"discharge"}
    ]
  }
}
```

Alternativ bleibt `power_mode: single` vollständig kompatibel mit der bisherigen
`power`-Konfiguration. Alte Einstellungen ohne `power_mode` verwenden automatisch
`single`. Beim Laden bisheriger Batterie- und PV-Modbus-Konfigurationen wird
fehlenden Registernamen deren **dezimal angegebene Adresse** zugewiesen, ohne
Messwerte oder Parameter zu ändern. Für konfigurierte Register sind Namen
Pflicht, leere optionale Batterie-Register bleiben optional.

## Bedienung

Im berechneten Modus: Register hinzufügen, beschriften und vollständig
konfigurieren; durch Antippen der Register-Badges, Operatoren, Klammern und
manuell eingegebener Zahlen die Formel aufbauen. Ein Token kann durch
Antippen wieder entfernt werden, zusätzlich gibt es „Rückgängig“ und
„Leeren“. Die Vorschau validiert den Ausdruck unmittelbar; serverseitig
wird vor dem Speichern erneut validiert. Auch der Name einer bestehenden
Registerzeile kann geändert werden, ohne ihre stabile Referenz zu verlieren.

## Grenzen / folgende Schritte

- Die Vorschau erzeugt **keine** erfundenen Live-Messwerte; die tatsächlich
  eingelesenen Registerwerte erscheinen erst nach Speichern und erfolgreichem
  Abfragedurchlauf bei den Messdetails.
- Die berechnete PV-Leistung wird nicht in den Minercontroller eingespeist.
- Es erfolgt keine rückwirkende Änderung bereits gespeicherter PV-Ist-Werte.
- GUI-Vorschau auf der Dev-Instanz, Berechnungs- und Formularvalidierung sowie
  simulierte Modbus-TCP-Lesevorgänge sind getestet. Für eine Veröffentlichung
  sind Tests mit realen SMA-Geräten und eine abschließende
  Versions-/Migrationsprüfung nötig.
- Die SMA-Registerbelegung und die Bedeutung der Batterieleistung müssen am
  tatsächlichen Geräteprofil geprüft werden (insbesondere FC04/0-basiert).

## Kompakte Registerdarstellung

Auf Desktopbreite werden die sechs Modbus-Felder **Name, Adresse,
Registerart, Datentyp, Byte-Reihenfolge, Faktor** pro Register in einer
Zeile dargestellt. Das gilt im berechneten Modus und in den bestehenden
PV-/Batterie-Register-Feldgruppen. Auf kleineren Viewports wird responsiv
auf drei, zwei beziehungsweise ein Feld pro Zeile umgebrochen. Andere
Einstellungsgruppen behalten ihr eigenes Raster.
