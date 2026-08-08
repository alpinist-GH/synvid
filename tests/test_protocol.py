import subprocess
import sys
import unittest
import os
import tempfile

from worker.protocol import Envelope, ProtocolError, negotiate_version, parse_envelope, validate_request


class ProtocolTests(unittest.TestCase):
    def test_round_trip(self):
        message = Envelope(1, "request-1", "get_status", {"job_id": "job-1"})
        self.assertEqual(parse_envelope(message.to_json_line()), message)

    def test_rejects_unknown_version(self):
        with self.assertRaises(ProtocolError):
            parse_envelope('{"version":2,"request_id":"r","kind":"x","payload":{}}')

    def test_negotiates_intersection(self):
        self.assertEqual(negotiate_version(1, 4), 1)
        with self.assertRaises(ProtocolError):
            negotiate_version(2, 4)

    def test_accepts_stage_one_generate_request(self):
        request = parse_envelope('{"version":1,"request_id":"r","kind":"generate","payload":{}}')
        validate_request(request)

    def test_rejects_unbounded_payload(self):
        payload = "{" * 9 + '"value"' + "}" * 9
        with self.assertRaises(ProtocolError):
            parse_envelope('{"version":1,"request_id":"r","kind":"get_status","payload":' + payload + "}")

    def test_worker_handshake_uses_stdout_protocol_only(self):
        with tempfile.TemporaryDirectory() as temp:
            process = subprocess.run(
                [sys.executable, "-m", "worker"],
                input='{"version":1,"request_id":"hello-1","kind":"hello","payload":{"protocol_min":1,"protocol_max":1}}\n',
                text=True,
                capture_output=True,
                check=True,
                env={**os.environ, "SYNVID_APP_SUPPORT": temp},
            )
        reply = parse_envelope(process.stdout)
        self.assertEqual(reply.kind, "hello_ack")
        self.assertEqual(reply.payload["protocol_version"], 1)
        self.assertEqual(process.stderr, "")

    def test_worker_status_never_invents_a_generation_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            process = subprocess.run(
                [sys.executable, "-m", "worker"],
                input='{"version":1,"request_id":"status-1","kind":"get_status","payload":{}}\n',
                text=True,
                capture_output=True,
                check=True,
                env={**os.environ, "SYNVID_APP_SUPPORT": temp},
            )
        reply = parse_envelope(process.stdout)
        self.assertEqual(reply.kind, "status")
        self.assertIsNone(reply.payload["active_job"])
        self.assertIsNone(reply.payload["measured_recipes"])
