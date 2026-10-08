"""Validate exporter-specific settings from their own declarative schema."""
from __future__ import annotations
from urllib.parse import urlsplit
from pv2hash.exporters.schema import DEFINITIONS


def normalize(kind: str, submitted: dict, previous: dict | None = None) -> dict:
    if kind not in DEFINITIONS:
        raise ValueError('Unbekannter Exportertyp')
    previous = previous or {}
    output = {}
    for field in DEFINITIONS[kind].get_config_schema():
        raw = submitted.get(field.name, field.default)
        if field.type == 'password' and not raw:
            raw = previous.get(field.name, '')
        if field.type == 'number':
            try:
                value = float(raw)
            except (ValueError, TypeError) as exc:
                raise ValueError(f'{field.label}: Ungültige Zahl') from exc
            if not value.is_integer() or (field.min is not None and value < field.min) or (field.max is not None and value > field.max):
                raise ValueError(f'{field.label}: Wert außerhalb des gültigen Bereichs')
            raw = int(value)
        elif field.type == 'checkbox':
            raw = raw is True or str(raw).lower() in ('true', '1', 'on')
        else:
            raw = str(raw or '').strip()
        if field.required and not raw:
            raise ValueError(f'{field.label} ist erforderlich')
        if field.choices and str(raw) not in {choice.value for choice in field.choices}:
            raise ValueError(f'{field.label}: Ungültige Auswahl')
        if field.type == 'url':
            parsed = urlsplit(raw)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError(f'{field.label}: Ungültige HTTP(S)-URL')
        output[field.name] = raw
    return output
