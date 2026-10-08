"""Optional local site location and address geocoding."""
from __future__ import annotations
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def geocode(address: str) -> dict:
    address = str(address or '').strip()
    if len(address) < 3 or len(address) > 250:
        raise ValueError('Bitte eine gültige Adresse mit 3 bis 250 Zeichen angeben.')
    url = 'https://nominatim.openstreetmap.org/search?' + urlencode({'q':address,'format':'json','limit':1})
    request = Request(url, headers={'User-Agent':'PV2Hash/0.7 (location geocoder)', 'Accept':'application/json'})
    try:
        with urlopen(request, timeout=8) as response:
            results = json.load(response)
    except Exception as exc:
        raise ValueError('Geocoding-Dienst momentan nicht erreichbar.') from exc
    if not results:
        raise ValueError('Keine Koordinaten zu dieser Adresse gefunden.')
    result = results[0]
    return {'address':address, 'display_name':str(result.get('display_name') or address),
            'lat':float(result['lat']), 'lon':float(result['lon']), 'source':'nominatim'}
