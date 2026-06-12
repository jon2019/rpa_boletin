"""
Tests unitarios mínimos para validar el preprocesamiento de imágenes
usado por `test_login_portal_minero.py`.

Objetivo:
- comprobar que `_upscale_png()` devuelve un PNG válido
- comprobar que realmente escala la imagen
- comprobar que la imagen resultante queda en RGB

Ejecución desde la raíz del proyecto:
    python -m unittest boletin/test_portal_minero_preprocesamiento.py

O desde la carpeta `boletin/`:
    python -m unittest test_portal_minero_preprocesamiento.py
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


import io
import sys
import unittest
from pathlib import Path

from PIL import Image


CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import test_login_portal_minero as target  # noqa: E402


def _crear_png_base(width: int = 10, height: int = 6, color=(20, 40, 60)) -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestPortalMineroPreprocesamiento(unittest.TestCase):
    def test_upscale_png_devuelve_png_valido(self) -> None:
        original = _crear_png_base()

        procesada = target._upscale_png(original, scale=3)

        self.assertIsInstance(procesada, bytes)
        self.assertTrue(procesada.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_upscale_png_triplica_dimensiones(self) -> None:
        original = _crear_png_base(width=12, height=7)

        procesada = target._upscale_png(original, scale=3)
        out = Image.open(io.BytesIO(procesada))

        self.assertEqual(out.size, (36, 21))

    def test_upscale_png_entrega_imagen_rgb(self) -> None:
        original = _crear_png_base(width=8, height=5)

        procesada = target._upscale_png(original, scale=3)
        out = Image.open(io.BytesIO(procesada))

        self.assertEqual(out.mode, "RGB")

    def test_upscale_png_respeta_factor_personalizado(self) -> None:
        original = _crear_png_base(width=9, height=4)

        procesada = target._upscale_png(original, scale=2)
        out = Image.open(io.BytesIO(procesada))

        self.assertEqual(out.size, (18, 8))


if __name__ == "__main__":
    unittest.main()
