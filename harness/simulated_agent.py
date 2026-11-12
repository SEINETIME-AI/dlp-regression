"""An agent played over gRPC, for suites that need what an agent sends and receives without
installing one: enroll with a package's bootstrap token, heartbeat, push events. The stubs are
built from the backend's protos at run time into a temporary folder, so they always match the
backend under test and nothing generated is committed."""
import datetime
import importlib
import sys
import tempfile
import uuid
from pathlib import Path

import grpc
from grpc_tools import protoc

PROTOS = ("agent.proto", "event.proto")


def load_stubs(proto_dir: Path) -> dict:
    """Compiles the protos once per process and returns their pb2 / pb2_grpc modules by name."""
    if not all((proto_dir / name).exists() for name in PROTOS):
        raise FileNotFoundError(f"backend protos not found in {proto_dir} (UAT_BACKEND_PROTO_DIR)")
    out = Path(tempfile.mkdtemp(prefix="uat-grpc-stubs-"))
    grpc_include = Path(protoc.__file__).parent / "_proto"
    status = protoc.main(["protoc", f"-I{proto_dir}", f"-I{grpc_include}", f"--python_out={out}",
                          f"--grpc_python_out={out}", *(str(proto_dir / name) for name in PROTOS)])
    if status != 0:
        raise RuntimeError(f"protoc failed on {proto_dir} ({status})")
    sys.path.insert(0, str(out))
    names = [f"{Path(p).stem}_pb2{suffix}" for p in PROTOS for suffix in ("", "_grpc")]
    return {name: importlib.import_module(name) for name in names}


class SimulatedAgent:
    def __init__(self, target: str, proto_dir: Path, hostname: str):
        self.stubs = load_stubs(proto_dir)
        self.channel = grpc.insecure_channel(target)
        self.hostname = hostname
        self.machine_id = ""
        self.api_key = ""

    def close(self):
        self.channel.close()

    def _metadata(self):
        return [("x-api-key", self.api_key)]

    def enroll(self, package_id: str, bootstrap_token: str):
        pb = self.stubs["agent_pb2"]
        stub = self.stubs["agent_pb2_grpc"].AgentServiceStub(self.channel)
        answer = stub.Enroll(pb.EnrollmentRequest(
            package_id=package_id, bootstrap_token=bootstrap_token,
            device_fingerprint=f"uat-{self.hostname.lower()}-{uuid.uuid4()}", hostname=self.hostname,
            os_version="Windows 11 Pro (UAT simulated)", agent_version="0.0.0-uat",
            ip_addresses=["127.0.0.1"]), timeout=30)
        self.machine_id, self.api_key = answer.machine_id, answer.api_key
        return answer

    def heartbeat(self):
        pb = self.stubs["agent_pb2"]
        stub = self.stubs["agent_pb2_grpc"].AgentServiceStub(self.channel)
        return stub.Heartbeat(pb.HeartbeatRequest(
            machine_id=self.machine_id, agent_version="0.0.0-uat",
            os_version="Windows 11 Pro (UAT simulated)", ip_addresses=["127.0.0.1"]),
            metadata=self._metadata(), timeout=30)

    def push_machine_events(self, event_types: list[str], action: str):
        """Sends events that carry no file (agent alerts) in one BatchSync stream."""
        pb = self.stubs["event_pb2"]
        stub = self.stubs["event_pb2_grpc"].EventServiceStub(self.channel)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        requests = [pb.EventRequest(id=str(uuid.uuid4()), machine_id=self.machine_id, event_type=event_type,
                                    action=action, occurred_at=now) for event_type in event_types]
        return stub.BatchSync(iter(requests), metadata=self._metadata(), timeout=30)
