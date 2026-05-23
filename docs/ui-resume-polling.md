# UI resume polling hardening

Some desktop browsers can keep stale or sleeping network connections around while a tab is hidden. When the tab becomes visible again, the first same-origin fetches may spend many seconds in `Initial connection` although the PV2Hash backend responds quickly once the connection exists.

This behavior was observed with Chrome/Edge on Windows after a tab resumed from the background. The server response itself was fast; the long time was spent in the browser/network connection setup.

## Mitigation

PV2Hash keeps the existing page-specific polling logic, but adds a small frontend helper for soft polling endpoints:

- `/api/status`
- `/api/ui/versionstatus`
- `/api/dashboard/status`
- `/api/miners/status`
- `/api/miners/socket-assignment/model`
- `/api/system/update-status`

For these GET polling endpoints the helper:

1. Aborts in-flight polling requests when the tab is hidden.
2. Avoids duplicate in-flight requests for the same polling URL.
3. Adds a 5-second timeout to soft polling requests.
4. Staggers the first few polling requests after the tab becomes visible again.

POST requests and normal user actions are not affected.

## Goal

The goal is not to fix the underlying browser/network behavior. Instead, the UI should avoid waiting for stale 20-second connection attempts and should recover more smoothly when the tab becomes visible again.
