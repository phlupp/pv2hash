# Automatischer Dashboard-Wechsel nach einem Web-Update

**Stand:** Fix im Hotfix-Zweig `fix/influx-forecast-export-test-approval`.

## Ablauf

1. Beim aktiven Update beobachtet die Systemseite einen eindeutigen
   Updateversuch anhand von `target_tag` und `started_at`. Ein bereits
   früher abgeschlossener Lauf darf keinen erneuten Wechsel auslösen.
2. Das Update-Overlay zeigt Installationsfortschritt und Protokoll auch während
   des regulären Backend-Neustarts an. Der laufende Frontend-Code behält dabei
   den beobachteten Updateversuch.
3. Sobald der Updater `success` für denselben Versuch meldet, fragt die
   Systemseite den **tatsächlich wieder gestarteten Dienst** über
   `/api/system/build-info` ab. Bei einem Zeitüberschreitungs-/Netzfehler
   wird die Abfrage erneut versucht. Es erfolgt **keine** Navigation auf
   Basis von Installer-Logs oder ausschließlich des Status `success`.
4. Bei datumsbasierten Paketkennungen (`rYYYY.MM.DD.N`) muss die exakte
   `build_info.release_id` dem Ziel-Tag entsprechen. Wenn aus der
   Update-Startantwort zusätzlich eine gültige erwartete Core-SemVer
   vorliegt, muss auch `build_info.core_version` übereinstimmen. Das
   Feld `target_version_full` darf der Installer später auf die
   **Release-ID** überschreiben; das ist **kein** Core-SemVer-Vergleich.
   Bei Legacy-Versionen ist der installierte Core-SemVer maßgeblich.
5. Nach bestätigtem Ziel-Build erscheint die grüne Erfolgsmeldung.
   Nach **1,5 Sekunden** wird das Overlay geschlossen. Nach **3 Sekunden**
   lädt der Browser per `window.location.replace('/ui-next/')` das
   Dashboard einschließlich der neuen Frontend-Dateien vollständig neu.

Die reine Simulation/Log-Nachansicht darf weder diese Timer starten noch einen
alten erfolgreichen Updateversuch nachträglich aktivieren. Ein fehlgeschlagenes
Update navigiert nicht zum Dashboard; sein Protokoll bleibt einsehbar.

## Ursache des ursprünglichen Fehlers

Der auf Hashpi bestätigte Abschluss von `r2026.10.19.2` lautete
`status=success`, `target_tag=r2026.10.19.2`,
`target_version_full=r2026.10.19.2`, während die laufende Version
`core_version=0.10.6` meldete. Die frühere UI verglich irrtümlich
`core_version` mit dem **Datums-Release-Tag**, hielt das Update daher trotz
korrektem Paket für nicht bestätigt und unterdrückte das automatische
Schließen/Weiterleiten. Ein reproduzierender Frontend-Regressionstest
verwendet genau diese Feldkombination.

Die Abschlussprüfung ist eine Frontend-Schutzfunktion. Die produktive
Minerregelung, das Installationsskript und Update-/Backupdaten werden durch
diesen Fix nicht verändert.
