"""PV generation: one read-only Modbus/TCP measurement independent of controller."""
from __future__ import annotations

import asyncio
import socket
import struct
import threading
import unittest
from copy import deepcopy
from unittest.mock import patch

from pv2hash.config.defaults import DEFAULT_CONFIG
from pv2hash.factory import build_pv_source
from pv2hash.sources.battery_modbus import BatteryModbusSource, ModbusValueConfig
from pv2hash.sources.pv_modbus import PvModbusSource


def cfg(host="127.0.0.1", port=502, address=340, *, dtype="uint16", factor=1.0, kind="holding"):
    x=deepcopy(DEFAULT_CONFIG)
    x["pv"]["enabled"]=True
    x["pv"]["type"]="pv_modbus"
    s=x["pv"]["settings"]
    s["host"]=host
    s["port"]=port
    s["power"]={"address":address,"register_type":kind,
                "value_type":dtype,"endian":"big_endian","factor":factor}
    return x


class OneShotModbus:
    """One fake read-only Modbus device accepting precisely one register request."""
    def __init__(self, raw:bytes):
        self.raw=raw
        self.requests=[]
        self.sock=socket.socket()
        self.sock.bind(("127.0.0.1",0))
        self.sock.listen(1)
        self.port=self.sock.getsockname()[1]
        self.thread=threading.Thread(target=self.serve,daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def serve(self):
        try:
            conn,_=self.sock.accept()
            with conn:
                request=conn.recv(12)
                self.requests.append(request)
                tid,prot,length,unit,fn,addr,qty=struct.unpack(">HHHBBHH",request)
                payload=bytes([fn,len(self.raw)])+self.raw
                conn.sendall(struct.pack(">HHHB",tid,0,1+len(payload),unit)+payload)
        finally:
            self.sock.close()

    def __exit__(self,*exc):
        self.thread.join(timeout=2)
        self.sock.close()


class PvModbusTest(unittest.TestCase):
    def test_default_disabled_no_polling(self):
        self.assertIsNone(build_pv_source(deepcopy(DEFAULT_CONFIG)))

    def test_one_register_uint16_scaled(self):
        with OneShotModbus(struct.pack(">H",1234)) as server:
            adapter=build_pv_source(cfg(port=server.port,address=400,factor=2))
            self.assertEqual(asyncio.run(adapter.read()),2468.0)
            self.assertEqual(adapter.quality,"live")
            self.assertEqual(adapter.debug_info["pv_power_w"],2468.0)
            self.assertEqual(len(server.requests),1)
            fn,addr,qty=struct.unpack(">BHH",server.requests[0][7:12])
            self.assertEqual((fn,addr,qty),(3,400,1))

    def test_32bit_single_start_address_and_input_register(self):
        with OneShotModbus(struct.pack(">f",3520.5)) as server:
            adapter=build_pv_source(cfg(port=server.port,address=300,dtype="float32",kind="input"))
            self.assertAlmostEqual(asyncio.run(adapter.read()),3520.5)
            fn,addr,qty=struct.unpack(">BHH",server.requests[0][7:12])
            self.assertEqual((fn,addr,qty),(4,300,2))

    def test_sma_nan_sentinels_rejected_before_scaling(self):
        # SMA Modbus-TI: NaN = 0x80000000 for S32, 0xFFFFFFFF for U32,
        # 0x8000 for S16, 0xFFFF for U16.
        sentinels = [
            ("int32", ">i", -(2 ** 31)),
            ("uint32", ">I", 2 ** 32 - 1),
            ("int16", ">h", -(2 ** 15)),
            ("uint16", ">H", 2 ** 16 - 1),
        ]
        for dtype, fmt, sentinel in sentinels:
            with self.subTest(dtype=dtype):
                # A negative, fractional or otherwise nonunit factor must not
                # hide an invalid raw register value.
                with OneShotModbus(struct.pack(fmt,sentinel)) as server:
                    adapter=build_pv_source(cfg(port=server.port, address=30775,
                                                dtype=dtype, factor=-0.1))
                    self.assertIsNone(asyncio.run(adapter.read()))
                    self.assertEqual(adapter.quality,"offline")
                    self.assertIsNone(adapter.power_w)
                    self.assertIn("SMA-NaN",adapter.debug_info["devices"][0]["last_error"])
                    self.assertEqual(adapter.debug_info["devices"][0]["register_values"],[])

    def test_sma_nan_does_not_reject_normal_negative_ac_power(self):
        with OneShotModbus(struct.pack(">i",-716)) as server:
            adapter=build_pv_source(cfg(port=server.port, address=30775,dtype="int32"))
            self.assertEqual(asyncio.run(adapter.read()),-716.0)
            self.assertEqual(adapter.quality,"live")

    def test_sma_nan_calculation_register_is_rejected(self):
        # Calculated modes must refuse partial inputs rather than combining
        # a NaN from one register with a valid reading from the other.
        x=cfg(address=30775,dtype="int32")
        settings=x["pv"]["settings"]
        settings["power_mode"]="calculated"
        settings["calculation"]={"registers":[
            {"id":"ac","name":"AC-Leistung","address":30775,"register_type":"input",
             "value_type":"int32","endian":"big_endian","factor":1},
            {"id":"battery","name":"Batterieleistung","address":31393,"register_type":"input",
             "value_type":"uint32","endian":"big_endian","factor":1},
        ], "tokens":[{"type":"register","id":"ac"},"-",{"type":"register","id":"battery"}]}
        with OneShotModbus(struct.pack(">i",-(2**31))) as server:
            settings["port"]=server.port
            adapter=build_pv_source(x)
            self.assertIsNone(asyncio.run(adapter.read()))
            self.assertEqual(adapter.quality,"offline")
            error=adapter.debug_info["devices"][0]["last_error"]
            self.assertIn("AC-Leistung",error)
            self.assertIn("SMA-NaN",error)
            self.assertEqual(len(server.requests),1)

    def test_device_offline_clears_stale_numeric_value(self):
        adapter=build_pv_source(cfg(port=502))
        with patch.object(adapter.devices[0][1],"_poll_sync",return_value=802.0):
            self.assertEqual(asyncio.run(adapter.read()),802.0)
        with patch.object(adapter.devices[0][1],"_poll_sync",side_effect=OSError("Connection refused")):
            self.assertIsNone(asyncio.run(adapter.read()))
        self.assertIsNone(adapter.power_w)
        self.assertEqual(adapter.quality,"offline")
        self.assertIsNone(adapter.debug_info["pv_power_w"])

    def test_reject_invalid_input_and_out_of_range(self):
        cases=[
            cfg(host=""),
            cfg(port=0),
            cfg(address=65535,dtype="float32"),
            cfg(address=-1),
            cfg(factor=float("inf")),
            cfg(factor=0.0),
            cfg(kind="coil"),
        ]
        for bad in cases:
            with self.subTest(bad=bad["pv"]["settings"]):
                with self.assertRaises(ValueError):
                    build_pv_source(bad)

    def test_pv_is_configurable_via_generic_schema(self):
        adapter=build_pv_source(cfg())
        fields=adapter.devices[0][1].get_config_fields(config=cfg()["pv"])
        flat=[v["name"] for field in fields for v in
              (field.get("fields") if field.get("type")=="fieldset" else [field])]
        self.assertEqual(flat.count("pv_power_address"),1)
        self.assertEqual(flat.count("pv_power_value_type"),1)
        self.assertEqual(flat.count("pv_power_factor"),1)
        self.assertEqual(len([f for f in flat if f.endswith("_address")]),1)

    def test_old_measurement_form_preserves_configured_pv(self):
        from pv2hash.app import _apply_source_config_form
        old=cfg(host="pv-lan", address=600, dtype="int32")
        changed=_apply_source_config_form(deepcopy(old), {"source_type":"simulator","battery_type":"none"})
        self.assertEqual(changed["pv"], old["pv"])

    def test_form_payload_configures_single_pv_measurement(self):
        from pv2hash.app import _apply_source_config_form
        fields={
            "source_type":"simulator", "battery_type":"none",
            "pv_type":"pv_modbus", "pv_enabled":"on",
            "pv_host":"127.0.0.1", "pv_port":"18869", "pv_unit_id":"2",
            "pv_poll_interval_ms":"5000", "pv_request_timeout_seconds":"0.7",
            "pv_power_address":"470",
            "pv_power_register_type":"input",
            "pv_power_value_type":"uint16",
            "pv_power_endian":"big_endian",
            "pv_power_factor":"1000",
        }
        x=_apply_source_config_form(deepcopy(DEFAULT_CONFIG),fields)
        pv=build_pv_source(x)
        member = pv.devices[0][1]
        self.assertEqual(member.unit_id,2)
        self.assertEqual(member.power_cfg.address,470)
        self.assertEqual(member.power_cfg.factor,1000.0)
        self.assertEqual(member.power_cfg.register_type,"input")

    def test_existing_battery_modbus_parser_unchanged(self):
        battery=BatteryModbusSource(host="127.0.0.1")
        raw=struct.pack(">h",-104)
        decoded=battery._decode_value(raw_bytes=raw,
                                      cfg=ModbusValueConfig(name="test",value_type="int16"),
                                      register_type="holding")
        self.assertEqual(decoded,-104)


if __name__=="__main__":
    unittest.main()
