# Release Notes – Arbeitsweise

Jedes öffentliche PV2Hash-Release erhält einen **redaktionell geprüften** Text.

- Datei: `docs/releases/<RELEASE-ID>.md`, z. B. `v0.9.5.md` oder `r2026.11.02.1.md`.
- Erste Zeile genau `# PV2Hash <RELEASE-ID>`.
- Kurze, verständliche Stichpunkte unter `## Neue Funktionen`, `## Fehlerbehebungen` und `## Verbesserungen`. Nur passende Abschnitte aufführen.
- Optional einen Abschnitt `## Hinweis zur Umstellung` für Migrationen, erforderliche Schritte oder bekannte Einschränkungen.
- Keine Entwickler-Commits, Versionslisten ohne Nutzwert, Platzhalter oder ungeprüfte Erfolgsmeldungen.
- Die Datei wird gemeinsam mit Code und GUI vor Freigabe überprüft. **Erst danach veröffentlichen.**

Der Release-Builder validiert den Text, legt eine identische `RELEASE_NOTES.md` im Installationsarchiv sowie neben Manifest und Prüfsummen ab. Die GitHub-Action veröffentlicht **dieselbe Datei** mit `gh release create --notes-file`. Die Systemseite des PV2Hash Update-Managers liest den GitHub-Release-Text über die bestehende Updatecheck-API und rendert Überschriften und Stichpunkte.

Fehlt der Text, stimmt die Release-ID nicht mit der Datei überein oder sind Platzhalter vorhanden, schlägt der öffentliche Build fehl. Die Entwicklungs-Build-Ausnahme ist ausdrücklich nur für interne Testpakete zulässig.

**Versionswechsel:** `v0.9.5` ist das letzte Release mit herkömmlicher Paketbezeichnung. Ab der folgenden Veröffentlichung werden Paket-IDs `rYYYY.MM.DD.N` unabhängig von den separat versionierten Core- und GUI-Komponenten vergeben. Ob ein Paket tatsächlich veröffentlicht wurde, ist ausschließlich über den GitHub-Release-Eintrag ersichtlich.
