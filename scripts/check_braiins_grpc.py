from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Repo-Root in sys.path aufnehmen, damit "import pv2hash" funktioniert
REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT_STR = str(REPO_ROOT)
if REPO_ROOT_STR not in sys.path:
    sys.path.insert(0, REPO_ROOT_STR)

import grpc
from google.protobuf.json_format import MessageToDict

from pv2hash.vendor.braiins_api_stubs_path import ensure_braiins_stubs_on_path

ensure_braiins_stubs_on_path()

import bos.version_pb2 as version_pb2
import bos.version_pb2_grpc as version_pb2_grpc
import bos.v1.authentication_pb2 as authentication_pb2
import bos.v1.authentication_pb2_grpc as authentication_pb2_grpc
import bos.v1.configuration_pb2 as configuration_pb2
import bos.v1.configuration_pb2_grpc as configuration_pb2_grpc
import bos.v1.miner_pb2 as miner_pb2
import bos.v1.miner_pb2_grpc as miner_pb2_grpc


def msg_to_dict(message: Any) -> dict[str, Any]:
    return MessageToDict(
        message,
        preserving_proto_field_name=True,
        always_print_fields_with_no_presence=False,
    )


def pretty(title: str, data: Any) -> None:
    print()
    print(f"=== {title} ===")
    print(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Direkter Braiins gRPC Testclient")
    parser.add_argument("--host", required=True, help="Miner Host/IP")
    parser.add_argument("--port", type=int, default=50051, help="gRPC Port, meist 50051")
    parser.add_argument("--username", default="root", help="Braiins Benutzername")
    parser.add_argument("--password", required=True, help="Braiins Passwort")
    parser.add_argument("--timeout", type=float, default=10.0, help="RPC Timeout in Sekunden")
    args = parser.parse_args()

    target = f"{args.host}:{args.port}"
    print(f"Verbinde zu {target}")

    channel = grpc.insecure_channel(target)

    try:
        grpc.channel_ready_future(channel).result(timeout=args.timeout)
        print("gRPC Kanal bereit")
    except Exception as exc:
        print(f"Fehler: gRPC Kanal nicht bereit: {exc}")
        return 2

    try:
        api_stub = version_pb2_grpc.ApiVersionServiceStub(channel)
        api_version = api_stub.GetApiVersion(
            version_pb2.ApiVersionRequest(),
            timeout=args.timeout,
        )
        pretty("API Version", msg_to_dict(api_version))
    except Exception as exc:
        print(f"Fehler bei GetApiVersion: {exc}")
        return 3

    try:
        auth_stub = authentication_pb2_grpc.AuthenticationServiceStub(channel)
        login_response = auth_stub.Login(
            authentication_pb2.LoginRequest(
                username=args.username,
                password=args.password,
            ),
            timeout=args.timeout,
        )
        login_dict = msg_to_dict(login_response)
        pretty("Login", login_dict)

        token = getattr(login_response, "token", "") or login_dict.get("token", "")
        if not token:
            print("Fehler: Login erfolgreich, aber kein Token gefunden")
            return 4
    except Exception as exc:
        print(f"Fehler bei Login: {exc}")
        return 4

    metadata = [("authorization", token)]

    try:
        config_stub = configuration_pb2_grpc.ConfigurationServiceStub(channel)
        constraints = config_stub.GetConstraints(
            configuration_pb2.GetConstraintsRequest(),
            metadata=metadata,
            timeout=args.timeout,
        )
        constraints_dict = msg_to_dict(constraints)
        pretty("Constraints", constraints_dict)

        tuner_constraints = constraints_dict.get("tuner_constraints", {})
        power_target = tuner_constraints.get("power_target", {})
        if power_target:
            pretty("Power Target Constraints", power_target)
        else:
            print()
            print("=== Power Target Constraints ===")
            print("Nicht gefunden oder leer")
    except Exception as exc:
        print(f"Fehler bei GetConstraints: {exc}")

    try:
        miner_stub = miner_pb2_grpc.MinerServiceStub(channel)

        details = miner_stub.GetMinerDetails(
            miner_pb2.GetMinerDetailsRequest(),
            metadata=metadata,
            timeout=args.timeout,
        )
        pretty("Miner Details", msg_to_dict(details))

        status_stream = miner_stub.GetMinerStatus(
            miner_pb2.GetMinerStatusRequest(),
            metadata=metadata,
            timeout=args.timeout,
        )
        first_status = next(status_stream)
        pretty("Miner Status (erste Stream-Nachricht)", msg_to_dict(first_status))
        status_stream.cancel()

        stats = miner_stub.GetMinerStats(
            miner_pb2.GetMinerStatsRequest(),
            metadata=metadata,
            timeout=args.timeout,
        )
        pretty("Miner Stats", msg_to_dict(stats))

        errors = miner_stub.GetErrors(
            miner_pb2.GetErrorsRequest(),
            metadata=metadata,
            timeout=args.timeout,
        )
        pretty("Miner Errors", msg_to_dict(errors))
    except StopIteration:
        print("Fehler: GetMinerStatus lieferte keine Nachricht")
        return 5
    except Exception as exc:
        print(f"Fehler bei MinerService Calls: {exc}")
        return 5
    finally:
        channel.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())