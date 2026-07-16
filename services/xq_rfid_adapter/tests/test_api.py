import contextlib
import hashlib
import hmac
import http.client
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from xq_rfid_adapter.api import (
    MAX_BODY_BYTES,
    MAX_CONCURRENT_REQUESTS,
    MAX_SERVICE_WORKERS,
    ReplayGuard,
    authenticate_request,
    canonical_request,
    create_server,
    sign_request,
    verify_signature,
)
from xq_rfid_adapter.config import ConfigError, load_config, load_secret


VECTOR_ONE = {
    "secret": b"0123456789abcdef0123456789abcdef",
    "method": "post",
    "request_target": "/v1/operations?b=&a=1&a=2",
    "timestamp": "1700000000",
    "nonce": "00112233445566778899aabbccddeeff",
    "body": b'{"request_id":"r1"}',
    "canonical": b"POST\n/v1/operations?b=&a=1&a=2\n1700000000\n00112233445566778899aabbccddeeff\n324d441c37e0ad3a896107ea67cc36144fc9e607f09c14d2cb2df67b3b825b63",
    "signature": "87775a38b375ebc3731ea13936bceecbe7f7965f6e63b18c2a6047552a396b22",
}
VECTOR_TWO = {
    "secret": b"abcdefghijklmnopqrstuvwxyzABCDEF",
    "method": "GET",
    "request_target": "/v1/devices/reader%2D1?x=%2f&x=%2F",
    "timestamp": "1700000300",
    "nonce": "FFEEDDCCBBAA99887766554433221100",
    "body": b"",
    "canonical": b"GET\n/v1/devices/reader%2D1?x=%2f&x=%2F\n1700000300\nFFEEDDCCBBAA99887766554433221100\ne3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "signature": "d6327537a3ee3442a732a1cde5700ed65a13af1b1def9fb2b80f4122f51aae07",
}


class TestCanonicalization(unittest.TestCase):
    def test_fixed_golden_vectors(self):
        for vector in (VECTOR_ONE, VECTOR_TWO):
            with self.subTest(vector=vector["request_target"]):
                actual = canonical_request(
                    vector["method"], vector["request_target"],
                    vector["timestamp"], vector["nonce"], vector["body"]
                )
                self.assertEqual(actual, vector["canonical"])
                self.assertEqual(
                    sign_request(
                        vector["secret"], vector["method"],
                        vector["request_target"], vector["timestamp"],
                        vector["nonce"], vector["body"]
                    ),
                    vector["signature"],
                )

    def test_double_slash_target_cannot_authenticate_normalized_path(self):
        self.assertNotEqual(
            sign_request(
                VECTOR_ONE["secret"], "GET", "//v1/devices/reader-1",
                VECTOR_ONE["timestamp"], VECTOR_ONE["nonce"], b"",
            ),
            sign_request(
                VECTOR_ONE["secret"], "GET", "/v1/devices/reader-1",
                VECTOR_ONE["timestamp"], VECTOR_ONE["nonce"], b"",
            ),
        )

    def test_exact_target_and_body_change_signature(self):
        base = VECTOR_ONE
        targets = (
            "/v1/operations?a=1&a=2&b=",
            "/v1/operations?b=&a=2&a=1",
            "/v1/operations?b=&a=1&a=2&blank=",
            "/v1/operations?b=&a=%31&a=2",
        )
        signatures = {
            sign_request(base["secret"], "POST", target, base["timestamp"], base["nonce"], base["body"])
            for target in targets
        }
        signatures.add(base["signature"])
        self.assertEqual(len(signatures), len(targets) + 1)
        self.assertNotEqual(
            base["signature"],
            sign_request(base["secret"], "POST", base["request_target"], base["timestamp"], base["nonce"], b"{}"),
        )

    def test_verify_uses_compare_digest_and_rejects_bad_signature(self):
        with mock.patch("xq_rfid_adapter.api.hmac.compare_digest", wraps=hmac.compare_digest) as compare:
            self.assertTrue(verify_signature(
                VECTOR_ONE["secret"], VECTOR_ONE["signature"], VECTOR_ONE["method"],
                VECTOR_ONE["request_target"], VECTOR_ONE["timestamp"],
                VECTOR_ONE["nonce"], VECTOR_ONE["body"]
            ))
            self.assertFalse(verify_signature(
                VECTOR_ONE["secret"], "0" * 64, VECTOR_ONE["method"],
                VECTOR_ONE["request_target"], VECTOR_ONE["timestamp"],
                VECTOR_ONE["nonce"], VECTOR_ONE["body"]
            ))
        self.assertEqual(compare.call_count, 2)


class TestAuthenticationAndReplay(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "replay.sqlite3")
        self.secret = b"s" * 32

    def tearDown(self):
        self.temp.cleanup()

    def headers(self, timestamp="1000", nonce="a" * 32, target="/v1/operations", body=b""):
        return {
            "X-RFID-Timestamp": timestamp,
            "X-RFID-Nonce": nonce,
            "X-RFID-Signature": sign_request(self.secret, "POST", target, timestamp, nonce, body),
        }

    def test_timestamp_forms_and_skew_boundaries(self):
        guard = ReplayGuard(self.db_path, ttl_seconds=300)
        for timestamp in ("", "+1000", "-1000", "1000.0", "01000", " 1000", "1000 "):
            with self.subTest(timestamp=timestamp):
                headers = self.headers(timestamp=timestamp, nonce=(hashlib.sha256(timestamp.encode()).hexdigest()))
                with self.assertRaisesRegex(Exception, "authentication"):
                    authenticate_request(self.secret, headers, "POST", "/v1/operations", b"", guard, now=1000)
        authenticate_request(self.secret, self.headers("700", "1" * 32), "POST", "/v1/operations", b"", guard, now=1000)
        authenticate_request(self.secret, self.headers("1300", "2" * 32), "POST", "/v1/operations", b"", guard, now=1000)
        for timestamp, nonce in (("699", "3" * 32), ("1301", "4" * 32)):
            with self.assertRaisesRegex(Exception, "authentication"):
                authenticate_request(self.secret, self.headers(timestamp, nonce), "POST", "/v1/operations", b"", guard, now=1000)
        guard.close()

    def test_nonce_forms_replay_restart_and_expiry_cleanup(self):
        guard = ReplayGuard(self.db_path, ttl_seconds=300)
        for nonce in ("", "g" * 32, "a" * 31, "a" * 257):
            with self.subTest(nonce=nonce[:8]):
                with self.assertRaisesRegex(Exception, "authentication"):
                    authenticate_request(self.secret, self.headers(nonce=nonce), "POST", "/v1/operations", b"", guard, now=1000)
        headers = self.headers(nonce="b" * 32)
        authenticate_request(self.secret, headers, "POST", "/v1/operations", b"", guard, now=1000)
        with self.assertRaisesRegex(Exception, "authentication"):
            authenticate_request(self.secret, headers, "POST", "/v1/operations", b"", guard, now=1001)
        guard.close()
        reopened = ReplayGuard(self.db_path, ttl_seconds=300)
        try:
            with self.assertRaisesRegex(Exception, "authentication"):
                authenticate_request(self.secret, headers, "POST", "/v1/operations", b"", reopened, now=1100)
            reopened.accept("c" * 32, now=1301)
            connection = sqlite3.connect(self.db_path)
            try:
                nonces = {row[0] for row in connection.execute("SELECT nonce FROM replay_nonces")}
            finally:
                connection.close()
            self.assertEqual(nonces, {"c" * 32})
        finally:
            reopened.close()

    def test_future_timestamp_replay_remains_blocked_after_restart_for_full_window(self):
        headers = self.headers(timestamp="1300", nonce="f" * 32)
        guard = ReplayGuard(self.db_path, ttl_seconds=300)
        authenticate_request(
            self.secret, headers, "POST", "/v1/operations", b"", guard, now=1000
        )
        guard.close()

        reopened = ReplayGuard(self.db_path, ttl_seconds=300)
        try:
            for now in (1301, 1600):
                with self.subTest(now=now):
                    with self.assertRaisesRegex(Exception, "authentication"):
                        authenticate_request(
                            self.secret,
                            headers,
                            "POST",
                            "/v1/operations",
                            b"",
                            reopened,
                            now=now,
                        )
        finally:
            reopened.close()

    def test_signature_checked_before_replay_storage(self):
        guard = ReplayGuard(self.db_path)
        headers = self.headers(nonce="d" * 32)
        headers["X-RFID-Signature"] = "0" * 64
        with self.assertRaisesRegex(Exception, "authentication"):
            authenticate_request(self.secret, headers, "POST", "/v1/operations", b"", guard, now=1000)
        connection = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM replay_nonces").fetchone()[0], 0)
        finally:
            connection.close()
        guard.close()

    def test_concurrent_same_nonce_has_exactly_one_winner(self):
        guards = [ReplayGuard(self.db_path), ReplayGuard(self.db_path)]
        try:
            barrier = threading.Barrier(2)
            results = []
            lock = threading.Lock()

            def attempt(guard):
                barrier.wait()
                try:
                    guard.accept("e" * 32, now=1000)
                    outcome = True
                except Exception:
                    outcome = False
                with lock:
                    results.append(outcome)

            threads = [threading.Thread(target=attempt, args=(guard,)) for guard in guards]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(sorted(results), [False, True])
        finally:
            for guard in guards:
                guard.close()


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config_path = self.root / "config.json"
        self.base = {
            "bind": {"host": "127.0.0.1", "port": 0},
            "sqlite_path": str(self.root / "adapter.sqlite3"),
            "production": False,
            "tls": None,
            "devices": {"reader-1": {"driver": "fake"}},
        }

    def tearDown(self):
        self.temp.cleanup()

    def write(self, value):
        self.config_path.write_text(json.dumps(value), encoding="utf-8")

    def test_secret_missing_empty_short_and_never_leaked(self):
        for environment in ({}, {"RFID_ADAPTER_SECRET": ""}, {"RFID_ADAPTER_SECRET": "short-secret-value"}):
            with self.subTest(environment=environment):
                with self.assertRaises(ConfigError) as raised:
                    load_secret(environment)
                self.assertNotIn("short-secret-value", str(raised.exception))

    def test_invalid_secret_file_settings_never_fall_back(self):
        secret_file = self.root / "secret"
        cases = [
            "",
            str(self.root / "missing"),
            str(secret_file),
        ]
        secret_file.write_bytes(b"")
        for secret_path in cases:
            with self.subTest(secret_path=secret_path):
                with self.assertRaises(ConfigError):
                    load_secret({
                        "RFID_ADAPTER_SECRET_FILE": secret_path,
                        "RFID_ADAPTER_SECRET": "e" * 32,
                    })
        secret_file.write_bytes(b"short")
        with self.assertRaises(ConfigError):
            load_secret({
                "RFID_ADAPTER_SECRET_FILE": str(secret_file),
                "RFID_ADAPTER_SECRET": "e" * 32,
            })

    def test_secret_file_precedence_and_single_newline_handling(self):
        secret_file = self.root / "secret"
        secret_file.write_bytes(b"f" * 32 + b"\r\n")
        env = {"RFID_ADAPTER_SECRET_FILE": str(secret_file), "RFID_ADAPTER_SECRET": "e" * 32}
        self.assertEqual(load_secret(env), b"f" * 32)
        secret_file.write_bytes(b"f" * 32 + b"\n\n")
        with self.assertRaises(ConfigError):
            load_secret(env)

    def test_duplicate_config_keys_rejected_recursively(self):
        database = json.dumps(str(self.root / "adapter.sqlite3"))
        cases = [
            '{"bind":{"host":"127.0.0.1","port":0},'
            f'"sqlite_path":{database},"production":true,"production":false,'
            '"tls":null,"devices":{}}',
            '{"bind":{"host":"127.0.0.1","port":0},'
            f'"sqlite_path":{database},"production":false,"tls":null,'
            '"devices":{"reader-1":{"driver":"real","driver":"fake"}}}',
            '{"bind":{"host":"127.0.0.1","port":0},'
            f'"sqlite_path":{database},"production":false,"tls":null,'
            '"devices":{"reader-1":{"driver":"real"},'
            '"reader-1":{"driver":"fake"}}}',
        ]
        for text in cases:
            with self.subTest(text=text):
                self.config_path.write_text(text, encoding="utf-8")
                with self.assertRaises(ConfigError):
                    load_config(self.config_path)

    def test_unknown_keys_production_fake_and_plaintext_remote_rejected(self):
        cases = []
        unknown = dict(self.base, typo=True)
        cases.append(unknown)
        production = dict(self.base, production=True)
        cases.append(production)
        remote = dict(self.base, bind={"host": "0.0.0.0", "port": 8080})
        cases.append(remote)
        for value in cases:
            with self.subTest(value=value):
                self.write(value)
                with self.assertRaises(ConfigError):
                    load_config(self.config_path)

    def test_device_whitelist_exact_and_immutable(self):
        self.write(self.base)
        config = load_config(self.config_path)
        self.assertEqual(set(config.devices), {"reader-1"})
        with self.assertRaises(TypeError):
            config.devices["other"] = config.devices["reader-1"]

    def test_missing_config_and_invalid_tls_files_are_safe(self):
        with self.assertRaises(ConfigError) as raised:
            load_config(self.root / "private-missing-config.json")
        self.assertNotIn(str(self.root), str(raised.exception))

        value = dict(self.base)
        value["devices"] = {}
        value["production"] = True
        value["tls"] = {
            "cert_file": str(self.root / "missing-cert.pem"),
            "key_file": str(self.root / "missing-key.pem"),
        }
        self.write(value)
        with self.assertRaises(ConfigError) as raised:
            load_config(self.config_path)
        self.assertNotIn(str(self.root), str(raised.exception))

    def test_example_config_is_safe_and_parses(self):
        example = Path(__file__).parents[1] / "src/xq_rfid_adapter/examples/config.example.json"
        text = example.read_text(encoding="utf-8")
        self.assertNotIn("secret", text.lower())
        self.assertNotIn("password", text.lower())
        self.assertNotIn("sdk", text.lower())
        config = load_config(example)
        self.assertTrue(config.production)
        self.assertFalse(config.devices)


class FakeService:
    def __init__(self):
        self.calls = []
        self.raise_text = None

    def _return(self, name, *args):
        self.calls.append((name, *args))
        if self.raise_text:
            raise RuntimeError(self.raise_text)
        if name == "test_connection":
            return {"status": "connected"}
        if name == "get_device":
            return {
                "status": "connected",
                "capabilities": {
                    "supports_epc": True,
                    "supports_tid": True,
                    "supports_user_read": True,
                    "supports_user_write": True,
                },
                "antenna_count": 1,
                "firmware_version": "1.2.3-build_4",
                "hardware_version": "HW-2",
                "module_version": None,
                "region": "CN",
            }
        if name == "submit_operation":
            return {
                "state": "queued",
                "request_id": args[0]["request_id"],
                "operation_type": args[0]["operation_type"],
                "payload_version": args[0]["payload_version"],
            }
        return {
            "state": "succeeded",
            "request_id": args[0],
            "operation_type": "write_and_verify",
            "payload_version": 1,
            "epc_identity": {"nibble_length": 24, "suffix": "ccdd"},
            "tid_identity": None,
            "identity_hash": "sha256:" + "a" * 64,
            "verification_ok": True,
            "retryable": False,
        }

    def test_connection(self, device_id):
        return self._return("test_connection", device_id)

    def get_device(self, device_id):
        return self._return("get_device", device_id)

    def submit_operation(self, request):
        return self._return("submit_operation", request)

    def get_operation(self, request_id):
        return self._return("get_operation", request_id)


class TestHttpApi(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.secret = b"z" * 32
        self.service = FakeService()
        self.db_path = str(Path(self.temp.name) / "replay.sqlite3")
        self.server = create_server(
            ("127.0.0.1", 0), self.service, self.secret,
            self.db_path, frozenset({"reader-1"}), clock=lambda: 1700000000,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def auth(self, method, target, body=b"", nonce=None):
        timestamp = "1700000000"
        nonce = nonce or hashlib.sha256((method + target + str(time.monotonic_ns())).encode()).hexdigest()
        return {
            "X-RFID-Timestamp": timestamp,
            "X-RFID-Nonce": nonce,
            "X-RFID-Signature": sign_request(self.secret, method, target, timestamp, nonce, body),
        }

    def raw_request(self, request):
        import socket
        with socket.create_connection(("127.0.0.1", self.server.server_port), timeout=3) as connection:
            connection.sendall(request)
            response = http.client.HTTPResponse(connection)
            response.begin()
            result = (
                response.status,
                dict(response.getheaders()),
                json.loads(response.read()),
            )
        return result

    def request(self, method, target, body=b"", headers=None, *, skip_auto_headers=False):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        if skip_auto_headers:
            connection.putrequest(method, target)
            for name, value in (headers or {}).items():
                connection.putheader(name, value)
            connection.endheaders(body)
        else:
            connection.request(method, target, body=body, headers=headers or {})
        response = connection.getresponse()
        raw = response.read()
        result = (response.status, dict(response.getheaders()), json.loads(raw))
        connection.close()
        return result

    def assert_safe_error(self, response, status, code):
        actual_status, headers, envelope = response
        self.assertEqual(actual_status, status)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(list(envelope), ["ok", "request_id", "result", "error"])
        self.assertFalse(envelope["ok"])
        self.assertIsNone(envelope["result"])
        self.assertEqual(envelope["error"]["code"], code)
        return envelope

    def test_missing_bad_auth_replay_and_exact_target(self):
        self.assert_safe_error(self.request("GET", "/v1/devices/reader-1"), 401, "authentication_error")
        bad = self.auth("GET", "/v1/devices/reader-1")
        bad["X-RFID-Signature"] = "0" * 64
        self.assert_safe_error(self.request("GET", "/v1/devices/reader-1", headers=bad), 401, "authentication_error")
        nonce = "a" * 32
        target = "/v1/devices/reader-1?b=&a=1&a=2&blank=&encoded=%2f"
        headers = self.auth("GET", target, nonce=nonce)
        ok = self.request("GET", target, headers=headers)
        self.assertEqual(ok[0], 200)
        self.assert_safe_error(self.request("GET", target, headers=headers), 401, "authentication_error")
        altered_targets = (
            "/v1/devices/reader-1?a=1&a=2&b=&blank=&encoded=%2f",
            "/v1/devices/reader-1?b=&a=2&a=1&blank=&encoded=%2f",
            "/v1/devices/reader-1?b=&a=1&a=2&blank=&encoded=%2F",
        )
        for altered_target in altered_targets:
            with self.subTest(altered_target=altered_target):
                wrong_target = self.auth("GET", altered_target)
                self.assert_safe_error(
                    self.request("GET", target, headers=wrong_target),
                    401,
                    "authentication_error",
                )

    def test_raw_double_slash_target_is_not_normalized_before_authentication(self):
        target = "//v1/devices/reader-1"
        bad_headers = self.auth("GET", "/v1/devices/reader-1")
        bad_raw = (
            f"GET {target} HTTP/1.1\r\n"
            f"Host: 127.0.0.1\r\n"
            + "".join(f"{name}: {value}\r\n" for name, value in bad_headers.items())
            + "Connection: close\r\n\r\n"
        ).encode("ascii")
        self.assert_safe_error(
            self.raw_request(bad_raw), 401, "authentication_error"
        )

        good_headers = self.auth("GET", target)
        good_raw = (
            f"GET {target} HTTP/1.1\r\n"
            f"Host: 127.0.0.1\r\n"
            + "".join(f"{name}: {value}\r\n" for name, value in good_headers.items())
            + "Connection: close\r\n\r\n"
        ).encode("ascii")
        status, _, envelope = self.raw_request(good_raw)
        self.assertEqual(status, 200)
        self.assertEqual(envelope["result"]["status"], "connected")

    def test_unknown_device_only_after_authentication(self):
        target = "/v1/devices/not-configured"
        self.assert_safe_error(self.request("GET", target), 401, "authentication_error")
        response = self.request("GET", target, headers=self.auth("GET", target))
        self.assert_safe_error(response, 404, "configuration_error")
        self.assertEqual(self.service.calls, [])

    def test_four_routes_delegate_validated_values(self):
        cases = [
            ("POST", "/v1/devices/reader-1/test-connection", b"{}", "test_connection"),
            ("GET", "/v1/devices/reader-1", b"", "get_device"),
            ("POST", "/v1/operations", json.dumps({
                "request_id": "request-1", "operation_type": "write_and_verify",
                "device_id": "reader-1", "payload_hex": "AA" * 24,
                "payload_version": 1,
            }, separators=(",", ":")).encode(), "submit_operation"),
            ("GET", "/v1/operations/request-1", b"", "get_operation"),
        ]
        expected_keys = {
            "test_connection": {"status"},
            "get_device": {
                "status", "capabilities", "antenna_count", "firmware_version",
                "hardware_version", "module_version", "region",
            },
            "submit_operation": {
                "state", "request_id", "operation_type", "payload_version",
            },
            "get_operation": {
                "state", "request_id", "operation_type", "payload_version",
                "masked_epc", "masked_tid", "identity_hash", "verification_ok",
                "retryable",
            },
        }
        for method, target, body, expected in cases:
            headers = self.auth(method, target, body)
            if method == "POST":
                headers["Content-Type"] = "application/json"
                headers["Content-Length"] = str(len(body))
            status, _, envelope = self.request(method, target, body, headers)
            self.assertEqual(status, 200)
            self.assertTrue(envelope["ok"])
            self.assertEqual(set(envelope["result"]), expected_keys[expected])
        self.assertEqual(self.service.calls[0], ("test_connection", "reader-1"))
        self.assertEqual(self.service.calls[1], ("get_device", "reader-1"))
        self.assertEqual(self.service.calls[2][0], "submit_operation")
        self.assertEqual(set(self.service.calls[2][1]), {
            "request_id", "operation_type", "device_id", "payload_hex", "payload_version"
        })
        self.assertEqual(self.service.calls[3], ("get_operation", "request-1"))

    def test_bounded_and_strict_body_parsing(self):
        target = "/v1/operations"
        invalid_cases = [
            ({"Content-Type": "application/json"}, b"{}", 411),
            ({"Content-Type": "application/json", "Content-Length": "no"}, b"", 400),
            ({"Content-Type": "application/json", "Content-Length": "-1"}, b"", 400),
            ({"Content-Type": "application/json", "Transfer-Encoding": "chunked"}, b"", 400),
            ({"Content-Type": "text/plain", "Content-Length": "2"}, b"{}", 415),
            ({"Content-Type": "application/json", "Content-Length": "1"}, b"\xff", 400),
            ({"Content-Type": "application/json", "Content-Length": "1"}, b"[", 400),
            ({"Content-Type": "application/json", "Content-Length": "2"}, b"[]", 400),
            ({"Content-Type": "application/json", "Content-Length": "13"}, b'{"unknown":1}', 400),
        ]
        for extra, body, expected in invalid_cases:
            with self.subTest(extra=extra, body=body):
                headers = self.auth("POST", target, body)
                headers.update(extra)
                response = self.request(
                    "POST", target, body, headers,
                    skip_auto_headers="Content-Length" not in extra,
                )
                self.assert_safe_error(response, expected, "protocol_error")
        body = b"x" * (MAX_BODY_BYTES + 1)
        headers = self.auth("POST", target, body)
        headers.update({"Content-Type": "application/json", "Content-Length": str(len(body))})
        self.assert_safe_error(self.request("POST", target, body, headers), 413, "capacity_exceeded")

    def test_path_identifiers_methods_and_paths_are_strict(self):
        for target in ("/v1/devices/%2Fetc", "/v1/devices/" + "a" * 129, "/v1/operations/bad%2Fid"):
            response = self.request("GET", target, headers=self.auth("GET", target))
            self.assert_safe_error(response, 400, "protocol_error")
        target = "/v1/devices/reader-1"
        self.assert_safe_error(self.request("DELETE", target, headers=self.auth("DELETE", target)), 405, "protocol_error")
        missing = "/v1/missing"
        self.assert_safe_error(self.request("GET", missing, headers=self.auth("GET", missing)), 404, "protocol_error")
        self.assert_safe_error(self.request("OPTIONS", missing, headers=self.auth("OPTIONS", missing)), 405, "protocol_error")

    def test_duplicate_operation_keys_and_invalid_hex_rejected(self):
        target = "/v1/operations"
        valid_prefix = (
            b'{"request_id":"one","operation_type":"write_and_verify",'
            b'"device_id":"reader-1","payload_hex":"'
            + b"AA" * 24
            + b'","payload_version":1'
        )
        bodies = [
            b'{"request_id":"one","request_id":"two","operation_type":"write_and_verify","device_id":"reader-1","payload_hex":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","payload_version":1}',
            valid_prefix + b',"device_id":"reader-2"}',
            json.dumps({
                "request_id": "request-2", "operation_type": "write_and_verify",
                "device_id": "reader-1", "payload_hex": "AA" * 23 + "  ",
                "payload_version": 1,
            }, separators=(",", ":")).encode(),
            json.dumps({
                "request_id": "request-3", "operation_type": "write_and_verify",
                "device_id": "reader-1", "payload_hex": "AA" * 23 + "ＡＡ",
                "payload_version": 1,
            }, separators=(",", ":"), ensure_ascii=False).encode(),
        ]
        for body in bodies:
            headers = self.auth("POST", target, body)
            headers.update({"Content-Type": "application/json", "Content-Length": str(len(body))})
            self.assert_safe_error(
                self.request("POST", target, body, headers), 400, "protocol_error"
            )

    def test_every_service_adapter_error_is_sanitized(self):
        from xq_rfid_adapter.domain import AdapterError, AdapterErrorCode

        target = "/v1/devices/reader-1"
        unsafe = "password raw-frame /private/path"
        for code in AdapterErrorCode:
            with self.subTest(code=code):
                def raise_error(device_id, error_code=code):
                    del device_id
                    raise AdapterError(
                        error_code,
                        unsafe,
                        device_code="raw-device-code",
                    )

                self.service.get_device = raise_error
                _, _, envelope = self.request(
                    "GET", target, headers=self.auth("GET", target)
                )
                serialized = json.dumps(envelope)
                self.assertNotIn(unsafe, serialized)
                self.assertNotIn("raw-device-code", serialized)
                self.assertIsNone(envelope["error"]["device_code"])

    def test_route_specific_results_reject_adversarial_nominal_fields(self):
        target = "/v1/devices/reader-1"
        base = self.service._return("get_device", "reader-1")
        unsafe_values = [
            dict(base, firmware_version="/private/secret"),
            dict(base, firmware_version="https://device.invalid/version"),
            dict(base, firmware_version="secret=rawvalue"),
            dict(base, firmware_version="A" * 32),
            dict(base, firmware_version="E2003412"),
            dict(base, hardware_version="bad\ncontrol"),
            dict(base, hardware_version="C:\\private\\device"),
            dict(base, region="x" * 65),
            dict(base, region="ZZ"),
            dict(base, device_code="raw-frame-password"),
            dict(base, capabilities={"supports_epc": True, "secret": "leak"}),
            {"state": "not-a-valid-state"},
        ]
        for result in unsafe_values:
            with self.subTest(result=result):
                self.service.get_device = lambda device_id, value=result: value
                status, _, envelope = self.request(
                    "GET", target, headers=self.auth("GET", target)
                )
                self.assertEqual(status, 500)
                serialized = json.dumps(envelope)
                self.assertNotIn("private", serialized)
                self.assertNotIn("raw-frame", serialized)
                self.assertNotIn("leak", serialized)
                self.assertEqual(envelope["error"]["code"], "device_error")

    def test_operation_result_contract_accepts_exact_documented_states(self):
        target = "/v1/operations/request-1"
        states = (
            "queued", "claimed", "inventorying", "writing", "verifying",
            "succeeded", "failed_retryable", "failed_manual", "cancelled",
        )
        for state in states:
            with self.subTest(state=state):
                result = self.service._return("get_operation", "request-1")
                result["state"] = state
                self.service.get_operation = lambda request_id, value=result: value
                status, _, envelope = self.request(
                    "GET", target, headers=self.auth("GET", target)
                )
                self.assertEqual(status, 200)
                self.assertEqual(envelope["result"]["state"], state)
        invalid = self.service._return("get_operation", "request-1")
        invalid["state"] = "failed"
        self.service.get_operation = lambda request_id: invalid
        status, _, envelope = self.request(
            "GET", target, headers=self.auth("GET", target)
        )
        self.assertEqual(status, 500)
        self.assertEqual(envelope["error"]["code"], "device_error")

    def test_identity_descriptors_derive_suffix_only_masks(self):
        target = "/v1/operations/request-1"
        cases = (
            (8, "cd", "******CD"),
            (10, "34", "********34"),
            (24, "cdef", "********************CDEF"),
            (64, "beef", "*" * 60 + "BEEF"),
        )
        for length, suffix, expected in cases:
            with self.subTest(length=length):
                result = self.service._return("get_operation", "request-1")
                result["epc_identity"] = {
                    "nibble_length": length,
                    "suffix": suffix,
                }
                self.service.get_operation = lambda request_id, value=result: value
                status, _, envelope = self.request(
                    "GET", target, headers=self.auth("GET", target)
                )
                self.assertEqual(status, 200)
                self.assertEqual(envelope["result"]["masked_epc"], expected)
                self.assertNotIn("epc_identity", envelope["result"])

    def test_operation_result_contracts_reject_mismatched_or_unsafe_values(self):
        target = "/v1/operations"
        body = json.dumps({
            "request_id": "request-1",
            "operation_type": "write_and_verify",
            "device_id": "reader-1",
            "payload_hex": "AA" * 24,
            "payload_version": 1,
        }, separators=(",", ":")).encode()
        unsafe_submissions = (
            {"state": "unknown", "request_id": "request-1", "operation_type": "write_and_verify", "payload_version": 1},
            {"state": "queued", "request_id": "other", "operation_type": "write_and_verify", "payload_version": 1},
            {"state": "queued", "request_id": "request-1", "operation_type": "/private/secret", "payload_version": 1},
            {"state": "queued", "request_id": "request-1", "operation_type": "write_and_verify", "payload_version": True},
        )
        for result in unsafe_submissions:
            with self.subTest(result=result):
                self.service.submit_operation = lambda request, value=result: value
                headers = self.auth("POST", target, body)
                headers.update({"Content-Type": "application/json", "Content-Length": str(len(body))})
                status, _, envelope = self.request("POST", target, body, headers)
                self.assertEqual(status, 500)
                self.assertEqual(envelope["error"]["code"], "device_error")
                self.assertNotIn("private", json.dumps(envelope))

        lookup = "/v1/operations/request-1"
        valid = self.service._return("get_operation", "request-1")
        unsafe_lookups = (
            dict(valid, request_id="other"),
            dict(valid, masked_epc="AABB********CCDD"),
            dict(valid, epc_identity={"nibble_length": 6, "suffix": "ABCD"}),
            dict(valid, epc_identity={"nibble_length": 9, "suffix": "ABCD"}),
            dict(valid, epc_identity={"nibble_length": 24, "suffix": "ABC"}),
            dict(valid, epc_identity={"nibble_length": 24, "suffix": "ＡＢＣＤ"}),
            dict(valid, epc_identity={"nibble_length": 24, "suffix": "AB\nD"}),
            dict(valid, epc_identity={"nibble_length": 8, "suffix": "ABCDEF12"}),
            dict(valid, epc_identity={"nibble_length": 24, "suffix": "ABCD", "prefix": "FFFF"}),
            dict(valid, identity_hash="a" * 64),
            dict(valid, identity_hash="sha256:" + "A" * 64),
            dict(valid, identity_hash="md5:" + "a" * 64),
            dict(valid, identity_hash="not-a-hash"),
            dict(valid, verification_ok=1),
            dict(valid, device_code="raw-device-code"),
        )
        for result in unsafe_lookups:
            with self.subTest(result=result):
                self.service.get_operation = lambda request_id, value=result: value
                status, _, envelope = self.request(
                    "GET", lookup, headers=self.auth("GET", lookup)
                )
                self.assertEqual(status, 500)
                self.assertEqual(envelope["error"]["code"], "device_error")
                serialized = json.dumps(envelope)
                self.assertNotIn("private", serialized)
                self.assertNotIn("raw-device-code", serialized)

    def test_service_base_exceptions_are_fixed_safe_errors(self):
        target = "/v1/devices/reader-1"
        for raised in (SystemExit("secret/path"), KeyboardInterrupt("secret/path")):
            with self.subTest(exception=type(raised).__name__):
                def fail(device_id, error=raised):
                    del device_id
                    raise error

                self.service.get_device = fail
                status, _, envelope = self.request(
                    "GET", target, headers=self.auth("GET", target)
                )
                self.assertEqual(status, 500)
                serialized = json.dumps(envelope)
                self.assertNotIn("secret", serialized)
                self.assertNotIn("path", serialized)
                self.assertEqual(envelope["error"]["code"], "device_error")

    def test_arbitrary_extension_method_returns_safe_405(self):
        target = "/v1/devices/reader-1"
        headers = self.auth("BREW", target)
        raw = (
            f"BREW {target} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            + "".join(f"{name}: {value}\r\n" for name, value in headers.items())
            + "Connection: close\r\n\r\n"
        ).encode("ascii")
        self.assert_safe_error(self.raw_request(raw), 405, "protocol_error")

    def test_non_origin_request_targets_return_safe_json(self):
        forms = (
            b"http://example.invalid/v1/devices/reader-1",
            b"example.invalid:443",
            b"*",
            b"/v1/device\x80",
        )
        for target in forms:
            with self.subTest(target=target):
                raw = (
                    b"GET " + target + b" HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                    b"Connection: close\r\n\r\n"
                )
                status, headers, envelope = self.raw_request(raw)
                self.assertIn(status, (400, 401, 404, 405))
                self.assertEqual(headers["Content-Type"], "application/json")
                self.assertFalse(envelope["ok"])

    def test_request_target_whitespace_and_controls_return_safe_json(self):
        malformed = (
            b"GET /v1/devices/reader-1 extra HTTP/1.1\r\nHost: localhost\r\n\r\n",
            b"GET /v1/devices/reader-1\tbad HTTP/1.1\r\nHost: localhost\r\n\r\n",
            b"GET /v1/devices/reader-1\x01 HTTP/1.1\r\nHost: localhost\r\n\r\n",
        )
        for raw in malformed:
            with self.subTest(raw=raw):
                status, headers, envelope = self.raw_request(raw)
                self.assertEqual(status, 400)
                self.assertEqual(headers["Content-Type"], "application/json")
                self.assertFalse(envelope["ok"])

    def test_stalled_service_calls_fail_closed_without_unbounded_growth(self):
        release = threading.Event()
        entered = threading.Event()
        calls = 0
        lock = threading.Lock()

        def stall(device_id):
            nonlocal calls
            del device_id
            with lock:
                calls += 1
                if calls == MAX_SERVICE_WORKERS:
                    entered.set()
            release.wait(5)
            return {"status": "connected"}

        self.service.test_connection = stall
        results = []
        threads = []
        target = "/v1/devices/reader-1/test-connection"
        body = b"{}"
        try:
            for _ in range(MAX_SERVICE_WORKERS):
                headers = self.auth("POST", target, body)
                headers.update({"Content-Type": "application/json", "Content-Length": "2"})
                thread = threading.Thread(
                    target=lambda h=headers: results.append(
                        self.request("POST", target, body, h)[0]
                    )
                )
                thread.start()
                threads.append(thread)
            self.assertTrue(entered.wait(2))
            deadline = time.monotonic() + 2.5
            while len(results) < MAX_SERVICE_WORKERS and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(sorted(results), [504] * MAX_SERVICE_WORKERS)
            before = len(threading.enumerate())
            for _ in range(MAX_SERVICE_WORKERS * 3):
                headers = self.auth("POST", target, body)
                headers.update({"Content-Type": "application/json", "Content-Length": "2"})
                response = self.request("POST", target, body, headers)
                self.assertIn(response[0], (503, 504))
            after = len(threading.enumerate())
            self.assertLessEqual(after, before + MAX_CONCURRENT_REQUESTS)
            with lock:
                self.assertEqual(calls, MAX_SERVICE_WORKERS)
        finally:
            release.set()
            for thread in threads:
                thread.join(3)

    def test_disconnect_releases_concurrency_permit(self):
        import socket

        connection = socket.create_connection(
            ("127.0.0.1", self.server.server_port), timeout=3
        )
        connection.sendall(b"GET /v1/devices/reader-1 HTTP/1.1\r\n")
        connection.close()
        time.sleep(0.1)
        target = "/v1/devices/reader-1"
        status, _, _ = self.request("GET", target, headers=self.auth("GET", target))
        self.assertNotEqual(status, 503)

    def test_stalled_bodies_are_capped_and_expire(self):
        import socket

        sockets = []
        target = "/v1/operations"
        try:
            for index in range(MAX_CONCURRENT_REQUESTS):
                body = b"{}"
                headers = self.auth("POST", target, body, nonce=f"{index:032x}")
                request = (
                    f"POST {target} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                    "Content-Type: application/json\r\nContent-Length: 2\r\n"
                    + "".join(f"{name}: {value}\r\n" for name, value in headers.items())
                    + "\r\n"
                ).encode("ascii")
                connection = socket.create_connection(
                    ("127.0.0.1", self.server.server_port), timeout=3
                )
                connection.sendall(request)
                sockets.append(connection)
            overloaded = socket.create_connection(
                ("127.0.0.1", self.server.server_port), timeout=3
            )
            overloaded.sendall(
                b"GET /v1/devices/reader-1 HTTP/1.1\r\nHost: localhost\r\n\r\n"
            )
            response = http.client.HTTPResponse(overloaded)
            response.begin()
            envelope = json.loads(response.read())
            self.assertEqual(response.status, 503)
            self.assertEqual(envelope["error"]["code"], "device_error")
            overloaded.close()

            time.sleep(1.2)
            response = self.request(
                "GET", target, headers=self.auth("GET", target)
            )
            self.assertNotEqual(response[0], 503)
        finally:
            for connection in sockets:
                connection.close()

    def test_exception_text_and_sensitive_values_never_echo(self):
        secret_text = "DO-NOT-LEAK-secret-signature-payload-path"
        self.service.raise_text = secret_text
        target = "/v1/devices/reader-1"
        _, _, envelope = self.request("GET", target, headers=self.auth("GET", target))
        serialized = json.dumps(envelope)
        self.assertNotIn(secret_text, serialized)
        self.assertEqual(envelope["error"]["code"], "device_error")


class TestCli(unittest.TestCase):
    def test_help_calls_no_config_secret_sqlite_or_bind_entry_points(self):
        from xq_rfid_adapter import __main__ as cli

        forbidden = (
            mock.patch.object(cli, "load_config", side_effect=AssertionError("config")),
            mock.patch.object(cli, "load_secret", side_effect=AssertionError("secret")),
            mock.patch.object(cli, "create_server", side_effect=AssertionError("server")),
            mock.patch("xq_rfid_adapter.api.sqlite3.connect", side_effect=AssertionError("sqlite")),
        )
        with contextlib.ExitStack() as stack:
            for patcher in forbidden:
                stack.enter_context(patcher)
            with self.assertRaises(SystemExit) as raised:
                cli.main(["--help"])
        self.assertEqual(raised.exception.code, 0)

    def test_help_exposes_required_options_without_side_effects(self):
        command = [sys.executable, "-m", "xq_rfid_adapter", "--help"]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        for text in ("serve", "--config", "--check-config"):
            self.assertIn(text, result.stdout)

    def test_check_config_errors_are_safe_and_validate_tls_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "secret"
            secret.write_bytes(b"q" * 32)
            env = dict(os.environ, RFID_ADAPTER_SECRET_FILE=str(secret))
            missing = root / "private-missing-config.json"
            result = subprocess.run(
                [sys.executable, "-m", "xq_rfid_adapter", "--config", str(missing), "--check-config"],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 2)
            combined = result.stdout + result.stderr
            self.assertNotIn("Traceback", combined)
            self.assertNotIn(str(root), combined)

            config = root / "config.json"
            config.write_text(json.dumps({
                "bind": {"host": "127.0.0.1", "port": 0},
                "sqlite_path": str(root / "adapter.sqlite3"),
                "production": True,
                "tls": {
                    "cert_file": str(root / "private-missing-cert.pem"),
                    "key_file": str(root / "private-missing-key.pem"),
                },
                "devices": {},
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "xq_rfid_adapter", "--config", str(config), "--check-config"],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 2)
            combined = result.stdout + result.stderr
            self.assertNotIn("Traceback", combined)
            self.assertNotIn(str(root), combined)

            cert = root / "private-cert.pem"
            key = root / "private-key.pem"
            cert.write_text("not a certificate", encoding="ascii")
            key.write_text("not a key", encoding="ascii")
            value = json.loads(config.read_text(encoding="utf-8"))
            value["tls"] = {"cert_file": str(cert), "key_file": str(key)}
            config.write_text(json.dumps(value), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "xq_rfid_adapter", "--config", str(config), "--check-config"],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 2)
            combined = result.stdout + result.stderr
            self.assertNotIn("Traceback", combined)
            self.assertNotIn(str(root), combined)

    def test_process_exits_with_permanently_hung_service_worker(self):
        script = r'''
import hashlib
import http.client
import tempfile
import threading
from pathlib import Path
from xq_rfid_adapter.api import create_server, sign_request

class HungService:
    def test_connection(self, device_id):
        threading.Event().wait()
    def get_device(self, device_id):
        threading.Event().wait()
    def submit_operation(self, request):
        threading.Event().wait()
    def get_operation(self, request_id):
        threading.Event().wait()

with tempfile.TemporaryDirectory() as directory:
    secret = b"z" * 32
    server = create_server(
        ("127.0.0.1", 0), HungService(), secret,
        Path(directory) / "replay.sqlite3", frozenset({"reader-1"}),
        clock=lambda: 1700000000,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    target = "/v1/devices/reader-1/test-connection"
    body = b"{}"
    timestamp = "1700000000"
    nonce = hashlib.sha256(b"hung-call").hexdigest()
    signature = sign_request(secret, "POST", target, timestamp, nonce, body)
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    connection.request("POST", target, body, {
        "Content-Type": "application/json",
        "Content-Length": "2",
        "X-RFID-Timestamp": timestamp,
        "X-RFID-Nonce": nonce,
        "X-RFID-Signature": signature,
    })
    response = connection.getresponse()
    if response.status != 504:
        raise RuntimeError("unexpected status")
    response.read()
    connection.close()
    server.shutdown()
    server.server_close()
    thread.join(2)
    if thread.is_alive():
        raise RuntimeError("server did not stop")
'''
        started = time.monotonic()
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=6,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(time.monotonic() - started, 5)

    def test_serve_sqlite_startup_error_is_safe(self):
        from xq_rfid_adapter import __main__ as cli

        config = mock.Mock()
        config.bind.host = "127.0.0.1"
        config.bind.port = 0
        config.sqlite_path = Path("/private/missing/adapter.sqlite3")
        config.devices = {}
        config.tls = None
        with (
            mock.patch.object(cli, "load_config", return_value=config),
            mock.patch.object(cli, "load_secret", return_value=b"q" * 32),
            mock.patch.object(
                cli, "create_server", side_effect=sqlite3.OperationalError("/private/path")
            ),
            self.assertRaises(SystemExit) as raised,
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            cli.main(["serve", "--config", "ignored.json"])
        self.assertEqual(raised.exception.code, 2)
        self.assertNotIn("/private", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_check_config_does_not_create_database_or_bind(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "must-not-exist.sqlite3"
            secret = root / "secret"
            secret.write_bytes(b"q" * 32)
            config = root / "config.json"
            config.write_text(json.dumps({
                "bind": {"host": "127.0.0.1", "port": 65535},
                "sqlite_path": str(database), "production": False,
                "tls": None, "devices": {"reader-1": {"driver": "fake"}},
            }), encoding="utf-8")
            env = dict(os.environ, RFID_ADAPTER_SECRET_FILE=str(secret))
            result = subprocess.run(
                [sys.executable, "-m", "xq_rfid_adapter", "--config", str(config), "--check-config"],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(database.exists())


if __name__ == "__main__":
    unittest.main()
