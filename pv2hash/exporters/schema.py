"""Declarative, adapter-owned GUI configuration schemas."""
from dataclasses import asdict
from pv2hash.miners.base import DriverField, DriverFieldChoice

class ExporterDefinition:
    TYPE = ""
    LABEL = ""

    @classmethod
    def get_config_schema(cls):
        return []

    @classmethod
    def render(cls, settings=None):
        settings = settings or {}
        fields = []
        for field in cls.get_config_schema():
            item = asdict(field)
            item["value"] = "" if field.type == "password" else settings.get(field.name, field.default)
            item["options"] = [asdict(choice) for choice in field.choices]
            fields.append(item)
        return {"type": cls.TYPE, "label": cls.LABEL, "fields": fields}

class InfluxDBDefinition(ExporterDefinition):
    TYPE = "influxdb2"
    LABEL = "InfluxDB 2.x"

    @classmethod
    def get_config_schema(cls):
        return [
            DriverField("url", "InfluxDB-URL", "url", required=True, default="http://127.0.0.1:8086", layout={"width":"half"}),
            DriverField("org", "Organisation", "text", required=True, layout={"width":"half"}),
            DriverField("bucket", "Bucket", "text", required=True, default="pv2hash", layout={"width":"half"}),
            DriverField("token", "API-Token", "password", required=True, layout={"width":"half"}),
            DriverField("timeout_seconds", "Timeout", "number", default=10, min=1, max=120, step=1, advanced=True, layout={"width":"third"}),
            DriverField("batch_size", "Batchgröße", "number", default=120, min=1, max=1000, step=1, advanced=True, layout={"width":"third"}),
            DriverField("precision", "Zeitauflösung", "select", default="ns", choices=(DriverFieldChoice("ns","Nanosekunden"), DriverFieldChoice("ms","Millisekunden")), advanced=True, layout={"width":"third"}),
        ]

DEFINITIONS = {InfluxDBDefinition.TYPE: InfluxDBDefinition}
