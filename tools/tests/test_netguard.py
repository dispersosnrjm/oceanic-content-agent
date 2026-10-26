import io
import os
import socket
import unittest
import urllib.request
from unittest import mock

try:
    from tests import _netguard
except ImportError:  # unittest discover -s tests
    import _netguard


class NetGuard(unittest.TestCase):
    def test_connections_and_dns_are_blocked(self):
        def raw_connect():
            with socket.socket() as s:
                s.connect(("127.0.0.1", 9))

        for attempt in (lambda: socket.create_connection(("example.com", 80)),
                        lambda: socket.getaddrinfo("example.com", 80),
                        raw_connect,
                        lambda: urllib.request.urlopen("http://example.com/")):
            with self.assertRaises(_netguard.NetworkBlocked):
                attempt()
        _netguard.ATTEMPTS.clear()  # los intentos de esta prueba eran a proposito

    def test_restore_without_injected_fetch_is_caught(self):
        """El error que casi cometimos: llamar a restore sin inyectar `fetch` descargaria de verdad."""
        import json
        import tempfile
        from pathlib import Path
        from oceanic import media
        with tempfile.TemporaryDirectory() as t:
            d = Path(t) / "biblioteca" / "marca" / "modelo" / "05_MULTIMEDIA" / "IMAGENES"
            d.mkdir(parents=True)
            rec = {"id": "a", "scope": "THIS_MODEL", "category": "EXTERIOR", "file": None, "sha256": None,
                   "download_status": "PENDING", "source_url": "https://x.test/a.jpg", "original": {}}
            (d / "images.json").write_text(json.dumps({"images": [rec]}), encoding="utf-8")
            st = media.restore_images(d.parent.parent)  # sin fetch: usaria la red de verdad
        self.assertEqual(st["failed"], 1)  # el codigo bajo prueba se traga la excepcion...
        self.assertTrue(_netguard.ATTEMPTS)  # ...pero el intento queda registrado y haria fallar la prueba
        _netguard.ATTEMPTS.clear()

    def test_a_swallowed_network_attempt_still_fails_the_test(self):
        class Swallows(unittest.TestCase):
            def runTest(self):
                try:
                    socket.create_connection(("example.com", 80))
                except Exception:  # noqa: BLE001 - como un `except Exception` del codigo bajo prueba
                    pass

        class Clean(unittest.TestCase):
            def runTest(self):
                self.assertTrue(True)

        out = io.StringIO()
        res = unittest.TextTestRunner(stream=out).run(unittest.TestSuite([Swallows(), Clean()]))
        self.assertFalse(res.wasSuccessful())
        self.assertEqual(len(res.failures), 1)
        self.assertIn("intento abrir una conexion real", res.failures[0][1])
        _netguard.ATTEMPTS.clear()

    def test_r2_credentials_are_not_in_the_environment(self):
        for var in _netguard.R2_ENV:
            self.assertNotIn(var, os.environ)

    def test_boto3_client_cannot_reach_r2(self):
        from oceanic import r2
        env = {"R2_ACCOUNT_ID": "acct", "R2_ACCESS_KEY_ID": "k", "R2_SECRET_ACCESS_KEY": "s", "R2_BUCKET": "b"}
        client = r2.R2Client(env)  # crear el cliente no abre conexiones
        with self.assertRaises(Exception):
            client.head_object("x/y.webp")
        self.assertTrue(_netguard.ATTEMPTS)  # el intento quedo registrado
        _netguard.ATTEMPTS.clear()

    def test_guard_is_installed_once(self):
        _netguard.install()
        with mock.patch.object(socket, "_oceanic_netguard", True):
            _netguard.install()
        self.assertTrue(socket._oceanic_netguard)


if __name__ == "__main__":
    unittest.main()
