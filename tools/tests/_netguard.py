"""Proteccion de red para las pruebas: ninguna prueba puede abrir una conexion real (ni a R2 ni a ninguna web).

Se activa al importar este modulo (cada archivo de pruebas lo importa) y es idempotente:

* bloquea la apertura de conexiones y la resolucion de nombres (socket.connect, connect_ex, create_connection,
  getaddrinfo, gethostbyname...), de modo que urllib, boto3 o cualquier cliente fallan de inmediato;
* registra cada intento y hace FALLAR la prueba aunque el codigo bajo prueba se haya tragado la excepcion
  (p. ej. un `except Exception` que cuenta la descarga como fallida);
* quita del entorno las credenciales R2_* (si se corrieran las pruebas en una sesion con las claves cargadas, una prueba
  equivocada no podria construir un cliente real). Las pruebas que necesitan variables las fijan con mock.patch.dict.
"""

from __future__ import annotations

import os
import socket
import sys
import unittest

R2_ENV = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
ATTEMPTS: list[str] = []


class NetworkBlocked(AssertionError):
    """Una prueba intento usar la red."""


def _blocked(name):
    def guard(*args, **kwargs):
        target = args[1] if name in ("connect", "connect_ex") and len(args) > 1 else (args[0] if args else "")
        ATTEMPTS.append(f"{name}({str(target)[:60]})")
        raise NetworkBlocked(f"las pruebas no pueden usar la red: {name}")
    return guard


def install() -> None:
    if getattr(socket, "_oceanic_netguard", False):
        return
    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
        setattr(socket, name, _blocked(name))
    socket.socket.connect = _blocked("connect")
    socket.socket.connect_ex = _blocked("connect_ex")
    socket._oceanic_netguard = True
    for var in R2_ENV:
        os.environ.pop(var, None)

    original_run = unittest.TestCase.run

    def run(self, result=None):
        ATTEMPTS.clear()
        out = original_run(self, result)
        if ATTEMPTS and result is not None:
            try:
                raise NetworkBlocked(f"{self.id()} intento abrir una conexion real: {', '.join(ATTEMPTS)}")
            except NetworkBlocked:
                result.addFailure(self, sys.exc_info())
        return out

    unittest.TestCase.run = run


install()
