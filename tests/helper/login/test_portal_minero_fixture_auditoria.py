"""
Auditoría local del preprocesamiento de imagen para Portal Minero.

NO intenta resolver el CAPTCHA.
Solo valida y deja evidencia de cómo entra y cómo sale la imagen
al pasar por `_upscale_png()` usando una imagen fixture ya guardada.

Ejecución desde la raíz del proyecto:
    python -m unittest boletin/test_portal_minero_fixture_auditoria.py

O desde la carpeta `boletin/`:
    python -m unittest test_portal_minero_fixture_auditoria.py
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


import io
import json
import sys
import unittest
from pathlib import Path

from PIL import Image


CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import test_login_portal_minero as target  # noqa: E402


FIXTURE_PATH = ROOT_DIR / "tests" / "output" / "login" / "debug_portal_minero_captcha.png"
AUDIT_DIR = ROOT_DIR / "tests" / "output" / "login" / "audit_portal_minero"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)


class TestPortalMineroFixtureAuditoria(unittest.TestCase):
    def test_fixture_captcha_preprocesamiento_deja_evidencia(self) -> None:
        self.assertTrue(
            FIXTURE_PATH.exists(),
            f"No existe la imagen fixture esperada: {FIXTURE_PATH}",
        )

        original_bytes = FIXTURE_PATH.read_bytes()
        original = Image.open(io.BytesIO(original_bytes))

        procesada_bytes = target._upscale_png(original_bytes, scale=3)
        procesada = Image.open(io.BytesIO(procesada_bytes))

        salida_png = AUDIT_DIR / "captcha_preprocesada_fixture.png"
        salida_json = AUDIT_DIR / "captcha_preprocesada_fixture.json"
        salida_png.write_bytes(procesada_bytes)

        metadata = {
            "fixture_entrada": str(FIXTURE_PATH),
            "salida_preprocesada": str(salida_png),
            "entrada": {
                "modo": original.mode,
                "ancho": original.size[0],
                "alto": original.size[1],
            },
            "salida": {
                "modo": procesada.mode,
                "ancho": procesada.size[0],
                "alto": procesada.size[1],
            },
            "factor_escala": 3,
            "transformacion_validada": (
                procesada.size[0] == original.size[0] * 3
                and procesada.size[1] == original.size[1] * 3
                and procesada.mode == "RGB"
            ),
        }
        salida_json.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

        self.assertEqual(procesada.mode, "RGB")
        self.assertEqual(procesada.size[0], original.size[0] * 3)
        self.assertEqual(procesada.size[1], original.size[1] * 3)
        self.assertTrue(salida_png.exists())
        self.assertTrue(salida_json.exists())


if __name__ == "__main__":
    unittest.main()
