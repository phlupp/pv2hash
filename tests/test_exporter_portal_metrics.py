"""Regression tests for portal-compatible InfluxDB exporter telemetry."""
import unittest
from unittest.mock import patch
from pv2hash.exporters.influxdb2 import mining_totals, line, InfluxDB2Destination


class ExporterPortalMetricsTest(unittest.TestCase):
    def test_running_only_and_case_insensitive(self):
        miners = [
            {'runtime_state': 'running', 'power_w': 170, 'hashrate_ghs': 11000},
            {'runtime_state': ' RUNNING ', 'power_w': 90, 'hashrate_ghs': 6000},
            {'runtime_state': 'paused', 'power_w': 0, 'hashrate_ghs': 21000},
            {'runtime_state': 'socket_off', 'power_w': 0, 'hashrate_ghs': 20000},
        ]
        totals = mining_totals(miners)
        self.assertEqual(totals['registered_miner_count'], 4)
        self.assertEqual(totals['running_miner_count'], 2)
        self.assertEqual(totals['running_miner_power_w'], 260)
        self.assertEqual(totals['running_hashrate_ghs'], 17000)
        self.assertEqual(totals['running_hashrate_ths'], 17)

    def test_no_miners(self):
        totals = mining_totals([])
        self.assertEqual(totals['registered_miner_count'], 0)
        self.assertEqual(totals['running_miner_count'], 0)
        self.assertEqual(totals['running_hashrate_ths'], 0)

    def test_text_fields_explicit_allowlist(self):
        result = line('pv2hash_miner', {'miner_name': 'Miner A'},
                      {'power_w': 90, 'runtime_state': 'running',
                       'private_message': 'password example'},
                      '2026-10-10T20:00:00Z', text_fields=('runtime_state',))
        self.assertIn('power_w=90i', result)
        self.assertIn('runtime_state="running"', result)
        self.assertNotIn('private_message', result)

    def test_send_emits_running_and_per_miner_fields(self):
        cfg = {'settings': {'url': 'http://localhost:8086', 'org': 'test',
                            'bucket': 'test', 'token': 'secret'},
               'instance_id': 'node1', 'instance_name': 'Test Node'}
        samples = [{'ts': '2026-10-10T20:00:00Z',
                    'instance_id': 'node1', 'grid_power_w': 0,
                    'miners': [
                        {'miner_id': 'm1', 'name': 'One', 'runtime_state': 'running',
                         'profile': 'p1', 'power_w': 100, 'hashrate_ghs': 5000},
                        {'miner_id': 'm2', 'name': 'Two', 'runtime_state': 'paused',
                         'profile': 'off', 'power_w': 0, 'hashrate_ghs': 8000},
                    ]}]
        with patch('pv2hash.exporters.influxdb2.httpx.Client') as client:
            client.return_value.__enter__.return_value.post.return_value.status_code = 204
            InfluxDB2Destination(cfg).send('samples', samples)
            data = client.return_value.__enter__.return_value.post.call_args.kwargs['content'].decode()
        self.assertIn('registered_miner_count=2i', data)
        self.assertIn('running_miner_count=1i', data)
        self.assertIn('running_miner_power_w=100.0', data)
        self.assertIn('running_hashrate_ghs=5000.0', data)
        self.assertIn('mining_active=1i', data)
        self.assertIn('mining_active=0i', data)
        self.assertNotIn('password', data)


if __name__ == '__main__':
    unittest.main()
