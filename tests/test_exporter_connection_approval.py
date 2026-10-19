"""Regression: changing export streams must not require a new InfluxDB test.

All HTTP API checks run against a temporary in-process FastAPI app with no
network, no real tokens and no writes to the production configuration.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
import unittest

from fastapi import FastAPI
import httpx

from pv2hash.exporters.api import install, fingerprint, _fingerprint
from pv2hash.exporters.configuration import normalize


CONNECTION = {
    'url': 'https://influx.example.invalid:8086',
    'org': 'forecast-lab',
    'bucket': 'pv2hash-test',
    'token': 'FAKE-TEST-TOKEN-NOT-REAL',
    'precision': 'ns',
    'timeout_seconds': 10,
    'batch_size': 120,
}


class TestExporterConnectionApproval(unittest.TestCase):
    def setUp(self):
        self.entry = {
            'id': 'existing', 'name': 'Phlupp Portal',
            'type': 'influxdb2', 'enabled': True,
            'settings': normalize('influxdb2', CONNECTION),
        }
        self.entry['tested_at'] = datetime(2026, 10, 18, tzinfo=UTC).isoformat()
        # Existing production installation has the *old* fingerprint format.
        self.entry['tested_fingerprint'] = _fingerprint(self.entry, legacy=True)
        self.state = SimpleNamespace(config={'exporters': [self.entry]})
        self.saved = []
        self.manager = SimpleNamespace(adapters={})
        self.app = FastAPI()
        install(self.app, self.state,
                lambda config: self.saved.append(deepcopy(config)), self.manager)

    async def _request(self, method, url, data=None):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url='http://isolated.test',
        ) as client:
            return await client.request(method, url, json=data)

    def request(self, method, url, data=None):
        return asyncio.run(self._request(method, url, data))

    def payload(self, **changes):
        data = {
            'id': 'existing', 'type': 'influxdb2',
            'name': 'Phlupp Portal', 'enabled': True,
            'settings': deepcopy(self.state.config['exporters'][0]['settings']),
        }
        data['settings'].update(changes)
        return data

    def test_current_checked_badge_remains_valid_with_legacy_false_flag(self):
        initial = self.request('GET', '/api/exporters/config').json()
        self.assertTrue(initial['exporters'][0]['test_ok'])
        result = self.request('POST', '/api/exporters/config',
                              self.payload(forecast_quality_enabled=True))
        self.assertEqual(result.status_code, 200, result.text)
        saved = self.state.config['exporters'][0]
        self.assertTrue(saved['enabled'])
        self.assertTrue(saved['settings']['forecast_quality_enabled'])
        self.assertEqual(saved['tested_at'], self.entry['tested_at'])
        self.assertEqual(saved['tested_fingerprint'], fingerprint(saved))
        self.assertEqual(len(self.saved), 1)
        self.assertTrue(self.request('GET', '/api/exporters/config').json()['exporters'][0]['test_ok'])
        self.assertEqual(self.request('POST', '/api/exporters/config',
                             self.payload(forecast_quality_enabled=False)).status_code, 200)
        self.assertTrue(self.state.config['exporters'][0]['enabled'])
        self.assertTrue(self.request('GET', '/api/exporters/config').json()['exporters'][0]['test_ok'])

    def test_existing_checked_exporter_from_older_release_without_new_setting(self):
        self.entry['settings'].pop('forecast_quality_enabled')
        self.entry['tested_fingerprint'] = _fingerprint(self.entry, legacy=True)
        self.assertTrue(self.request('GET','/api/exporters/config').json()['exporters'][0]['test_ok'])
        result = self.request('POST','/api/exporters/config',
                              self.payload(forecast_quality_enabled=True))
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(self.state.config['exporters'][0]['settings']['forecast_quality_enabled'])

    def test_option_toggle_false_true_does_not_change_connection_fingerprint(self):
        before = fingerprint(self.entry)
        self.entry['settings']['forecast_quality_enabled'] = True
        self.assertEqual(fingerprint(self.entry), before)
        self.entry['settings']['batch_size'] = 300
        self.assertEqual(fingerprint(self.entry), before)

    def test_actual_connection_changes_must_be_tested_again(self):
        cases = {
            'url': 'https://other.example.invalid',
            'org': 'wrong-org',
            'bucket': 'wrong-bucket',
            'token': 'DIFFERENT-FAKE-TOKEN',
            'timeout_seconds': 25,
            'precision': 'ms',
        }
        for key, value in cases.items():
            with self.subTest(setting=key):
                response = self.request('POST', '/api/exporters/config',
                                        self.payload(**{key:value,
                                            'forecast_quality_enabled': True}))
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn('Verbindung erfolgreich getestet',response.json()['message'])
                self.assertEqual(len(self.saved), 0)
                self.assertTrue(self.state.config['exporters'][0]['enabled'])
                self.assertFalse(self.state.config['exporters'][0]['settings']['forecast_quality_enabled'])

    def test_invalid_previous_approval_cannot_be_reused(self):
        self.entry['tested_fingerprint'] = 'not-an-approval'
        self.assertFalse(self.request('GET','/api/exporters/config').json()['exporters'][0]['test_ok'])
        response = self.request('POST','/api/exporters/config',
                                self.payload(forecast_quality_enabled=True))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.saved), 0)

    def test_new_exporter_cannot_be_enabled_without_test(self):
        payload = self.payload(forecast_quality_enabled=True)
        payload.pop('id')
        response = self.request('POST','/api/exporters/config',payload)
        self.assertEqual(response.status_code,400)
        self.assertEqual(len(self.saved),0)

    def test_untested_changed_connection_saved_only_as_inactive(self):
        request = self.payload(url='https://other.example.invalid',
                               forecast_quality_enabled=True)
        request['enabled'] = False
        response = self.request('POST','/api/exporters/config',request)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.state.config['exporters'][0]['enabled'])
        self.assertFalse(self.request('GET','/api/exporters/config').json()['exporters'][0]['test_ok'])


if __name__=='__main__':
    unittest.main()
