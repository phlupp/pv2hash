"""Driver-independent arithmetic and read-only multi-register PV device integration."""
from __future__ import annotations

import asyncio
import math
import socket
import struct
import threading
import unittest
from copy import deepcopy
from unittest.mock import patch

from pv2hash.config.defaults import DEFAULT_CONFIG
from pv2hash.config.store import deep_merge, normalize_config
from pv2hash.factory import build_pv_source
from pv2hash.sources.expression import compile_expression, evaluate_expression


def calculation():
    return {
        'registers': [
            {'id': 'ac', 'name': 'AC', 'address': 30775, 'register_type': 'input', 'value_type': 'int32', 'endian': 'big_endian', 'factor': 1},
            {'id': 'charge', 'name': 'Laden', 'address': 31393, 'register_type': 'input', 'value_type': 'uint32', 'endian': 'big_endian', 'factor': 1},
            {'id': 'discharge', 'name': 'Entladen', 'address': 31395, 'register_type': 'input', 'value_type': 'uint32', 'endian': 'big_endian', 'factor': 1},
        ],
        'tokens': [{'type': 'register', 'id': 'ac'}, '+', {'type': 'register', 'id': 'charge'}, '-', {'type': 'register', 'id': 'discharge'}],
    }


def pv_config(calc=None):
    settings = {'host': 'localhost', 'power_mode': 'calculated', 'calculation': calc or calculation()}
    config = deepcopy(DEFAULT_CONFIG)
    config['pv'] = {'type': 'pv_group', 'enabled': True,
                    'devices': [{'id': 'test', 'enabled': True, 'driver': 'pv_modbus', 'name': 'Test-Hybrid', 'settings': settings}]}
    return normalize_config(config)


class MultiModbusServer:
    def __init__(self, values):
        self.values = values
        self.requests = []
        self.server = socket.socket()
        self.server.bind(('127.0.0.1', 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.thread = threading.Thread(target=self.serve, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def serve(self):
        try:
            connection, _ = self.server.accept()
            with connection:
                for _ in range(3):
                    head = connection.recv(12)
                    if len(head) != 12:
                        return
                    tid, protocol, length, unit, fn, address, quantity = struct.unpack('>HHHBBHH', head)
                    self.requests.append((fn, address, quantity))
                    val = self.values[address]
                    data = struct.pack('>i' if address == 30775 else '>I', val)
                    payload = bytes([fn, len(data)]) + data
                    connection.sendall(struct.pack('>HHHB', tid, 0, 1 + len(payload), unit) + payload)
        finally:
            self.server.close()

    def __exit__(self, *exc):
        self.thread.join(timeout=3)
        self.server.close()


class FormulaTests(unittest.TestCase):
    def test_precedence_parentheses_offset_factor_and_negative_constants(self):
        ids = {'a','b'}
        expr = [ {'type':'register','id':'a'}, '+', {'type':'register','id':'b'}, '*', {'type':'number','value':2}, '-', {'type':'number','value':25}]
        prog,_ = compile_expression(expr, ids)
        self.assertEqual(evaluate_expression(prog, {'a':100,'b':30}),135)
        grouped = ['(', {'type':'register','id':'a'}, '+', {'type':'register','id':'b'}, ')', '*', {'type':'number','value':2}, '+', {'type':'number','value':-5}]
        prog,_ = compile_expression(grouped, ids)
        self.assertEqual(evaluate_expression(prog, {'a':100,'b':30}),255)

    def test_general_variable_tokens_work_without_modbus_knowledge(self):
        expression=[{'type':'variable','id':'solar'},'*',{'type':'number','value':0.8},'-',{'type':'variable','id':'house'}]
        program, used=compile_expression(expression, {'solar','house'})
        self.assertEqual(used, {'solar','house'})
        self.assertEqual(evaluate_expression(program, {'solar':2000,'house':400}),1200)

    def test_unsafe_and_invalid_expressions_refused(self):
        for expr in [[], ['x'], [{'type':'register','id':'a'},'**',{'type':'number','value':2}],
                     [{'type':'number','value':math.inf}], [{'type':'number','value':True}],
                     [{'type':'register','id':'a'}, '+'], ['(', {'type':'number','value':2}],
                     [{'type':'number','value':2}, {'type':'number','value':3}],
                     [{'type':'register','id':'not-present'}]]:
            with self.subTest(expr=expr), self.assertRaises(ValueError):
                compile_expression(expr, {'a','b'})
        prog,_ = compile_expression([{'type':'register','id':'a'}, '/', {'type':'register','id':'b'}], {'a','b'})
        with self.assertRaisesRegex(ValueError, 'Division durch 0'):
            evaluate_expression(prog, {'a':1,'b':0})
        with self.assertRaisesRegex(ValueError, 'fehlt'):
            evaluate_expression(prog, {'a':1})

    def test_requires_two_registers_actually_used(self):
        doc = calculation()
        doc['tokens'] = [{'type':'register','id':'ac'},'+',{'type':'number','value':1}]
        with self.assertRaisesRegex(ValueError,'mindestens zwei Register'):
            build_pv_source(pv_config(doc))

    def test_new_calculated_register_name_cannot_be_blank(self):
        doc = calculation()
        doc['registers'][1]['name'] = ''
        with self.assertRaisesRegex(ValueError, 'Registername'):
            build_pv_source(pv_config(doc))

    def test_round_timeout_scales_with_sequential_modbus_reads(self):
        adapter=build_pv_source(pv_config()).devices[0][1]
        self.assertGreater(adapter.round_timeout_seconds, adapter.request_timeout_seconds)
        self.assertLessEqual(adapter.round_timeout_seconds, 10)

    def test_duplicate_identifiers_and_names_invalid(self):
        for key, value in [('id', 'ac'), ('name', 'AC')]:
            doc = calculation()
            doc['registers'][1][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                build_pv_source(pv_config(doc))

    def test_required_register_name_in_form_but_missing_legacy_name_is_migrated(self):
        from pv2hash.app import _parse_modbus_value_form
        with self.assertRaisesRegex(ValueError, 'Registername'):
            _parse_modbus_value_form({'battery_soc_address': '30775', 'battery_soc_name': ''}, 'battery_soc')
        self.assertEqual(_parse_modbus_value_form({'battery_soc_address':'30775'},'battery_soc')['name'], '30775')

    def test_legacy_single_register_and_battery_names_default_to_address(self):
        config = deepcopy(DEFAULT_CONFIG)
        config['battery']['settings']['soc']={'address':303, 'factor': 1}
        config['battery']['settings']['charge_power']={'address':304}
        config['pv']={'enabled':True, 'type':'pv_modbus','settings':{'host':'localhost','power':{'address':30775}}}
        normalized = normalize_config(config)
        self.assertEqual(normalized['battery']['settings']['soc']['name'],'303')
        self.assertEqual(normalized['pv']['settings']['power']['name'],'30775')
        self.assertEqual(normalize_config(normalized),normalized)
        adapter=build_pv_source(normalized)
        self.assertEqual(adapter.devices[0][1].power_cfg.name,'30775')
        # Zero-based Modbus address 0 is a valid legacy fallback, not an empty name.
        config['battery']['settings']['soc']['address'] = 0
        self.assertEqual(normalize_config(config)['battery']['settings']['soc']['name'], '0')

    def test_calculated_config_survives_form_roundtrip(self):
        from pv2hash.app import _apply_source_config_form, services
        import json
        original=pv_config()
        form={'pv_device_ids':'test', 'pv_enabled':'on',
              'pvdev_test_name':'Test-Hybrid', 'pvdev_test_driver':'pv_modbus',
              'pvdev_test_enabled':'on', 'pvdev_test_power_mode':'calculated',
              'pvdev_test_calculation':json.dumps(calculation())}
        next_config=_apply_source_config_form(deepcopy(original),form)
        build_pv_source(next_config)
        self.assertEqual(next_config['pv']['devices'][0]['settings']['calculation'],calculation())
        model=next(m for m in services.get_source_gui_models(config=next_config) if m['id']=='pv-device:test')
        names=[f.get('name') for f in model['config_fields']]
        self.assertIn('pvdev_test_calculation',names)
        self.assertIn('pvdev_test_power_mode',names)


class MultiRegisterReadTests(unittest.TestCase):
    def test_real_modbus_input_reads_atomic_result_and_details(self):
        with MultiModbusServer({30775:2000,31393:3000,31395:0}) as server:
            cfg=pv_config()
            cfg['pv']['devices'][0]['settings'].update({'host':'127.0.0.1','port':server.port})
            group=build_pv_source(cfg)
            self.assertEqual(asyncio.run(group.read()),5000)
            self.assertEqual(group.snapshot.quality,'live')
            member=group.snapshot.devices[0]
            self.assertEqual([r['value'] for r in member['register_values']],[2000,3000,0])
            self.assertEqual([r['name'] for r in member['register_values']],['AC','Laden','Entladen'])
            self.assertEqual(server.requests,[(4,30775,2),(4,31393,2),(4,31395,2)])

    def test_incomplete_measurement_refuses_to_publish_partial_result(self):
        group=build_pv_source(pv_config())
        member=group.devices[0][1]
        def read_effect(sock, cfg):
            if cfg.address==31395: raise OSError('not available')
            return 100
        with patch('socket.create_connection') as connection:
            with patch.object(member,'_read_numeric_value',side_effect=read_effect):
                self.assertIsNone(asyncio.run(group.read()))
        self.assertIsNone(group.power_w)
        self.assertEqual(group.quality,'offline')
        self.assertEqual(group.snapshot.devices[0]['register_values'],[])
        self.assertIn('Entladen',group.snapshot.devices[0]['last_error'])


if __name__=='__main__':unittest.main()
