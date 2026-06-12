from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

#!/usr/bin/env python3
"""
Script de prueba para validar la lógica de fechas efectivas con consulta a DB.
Nota: Este script simula la lógica sin DB para pruebas unitarias.
"""
from datetime import datetime, date, timezone, timedelta

def calcular_fecha_efectiva_sin_db(hoy: datetime) -> date | None:
    """
    Versión simplificada sin consulta a DB para pruebas.
    En producción, esta función consulta la tabla ejecucion_fuentes.
    """
    weekday = hoy.weekday()  # 0=lunes, 1=martes, ..., 6=domingo
    fecha_actual = hoy.date()

    # Determinar fecha efectiva candidata
    if weekday == 1:  # Martes
        fecha_efectiva = fecha_actual
    elif weekday == 3:  # Jueves
        fecha_efectiva = fecha_actual
    elif weekday == 2:  # Miércoles -> compensa martes
        fecha_efectiva = (hoy - timedelta(days=1)).date()
    else:  # Viernes, Sábado, Domingo, Lunes -> compensa jueves
        # Calcular jueves anterior
        dias_atras = (weekday - 3) % 7
        if dias_atras == 0:
            dias_atras = 7
        fecha_efectiva = (hoy - timedelta(days=dias_atras)).date()

    # Simular consulta a DB: asumir que fechas pasadas ya fueron procesadas
    hoy_test = date.today()
    if fecha_efectiva < hoy_test:
        print(f"  Simulando: fecha {fecha_efectiva} ya procesada (anterior a hoy)")
        return None

    return fecha_efectiva

def test_fecha_efectiva():
    """Prueba la función calcular_fecha_efectiva con diferentes días de la semana."""

    # Test cases: (fecha_actual, descripción, esperar_ejecucion)
    # Nota: 1 abril 2026 es miércoles
    test_cases = [
        # Fechas futuras (simulando no procesadas) - usando abril 2026
        (datetime(2026, 4, 7, tzinfo=timezone.utc), "Martes futuro (7 abr)", True),  # 7 abril 2026 es martes
        (datetime(2026, 4, 8, tzinfo=timezone.utc), "Miércoles futuro (8 abr)", True),  # 8 abril 2026 es miércoles
        (datetime(2026, 4, 9, tzinfo=timezone.utc), "Jueves futuro (9 abr)", True),  # 9 abril 2026 es jueves
        (datetime(2026, 4, 10, tzinfo=timezone.utc), "Viernes futuro (10 abr)", True),  # 10 abril 2026 es viernes
        (datetime(2026, 4, 13, tzinfo=timezone.utc), "Lunes futuro (13 abr)", True),  # 13 abril 2026 es lunes

        # Fechas pasadas (simulando ya procesadas) - usando fechas anteriores a hoy (4 abril 2026)
        (datetime(2026, 3, 31, tzinfo=timezone.utc), "Lunes pasado (31 mar)", False),  # 31 marzo 2026 es lunes
        (datetime(2026, 4, 1, tzinfo=timezone.utc), "Martes pasado (1 abr)", False),  # 1 abril 2026 es martes
        (datetime(2026, 4, 2, tzinfo=timezone.utc), "Miércoles pasado (2 abr)", False),  # 2 abril 2026 es miércoles
    ]

    print("🧪 Probando lógica de fechas efectivas (con simulación DB)...")
    print("=" * 60)

    all_passed = True
    for actual_datetime, description, should_execute in test_cases:
        try:
            result = calcular_fecha_efectiva_sin_db(actual_datetime)
            executed = result is not None

            if executed == should_execute:
                status = "✅"
                if executed:
                    print(f"{status} {description}: ejecuta con fecha efectiva {result}")
                else:
                    print(f"{status} {description}: no ejecuta (ya procesado)")
            else:
                status = "❌"
                print(f"{status} {description}: esperado {'ejecutar' if should_execute else 'no ejecutar'}, obtenido {'ejecutar' if executed else 'no ejecutar'}")
                if executed:
                    print(f"    Fecha efectiva: {result}")
                all_passed = False

        except Exception as e:
            print(f"❌ {description}: ERROR - {e}")
            all_passed = False

    print("=" * 60)
    if all_passed:
        print("🎉 Todas las pruebas pasaron!")
        print("\n📝 Resumen de la lógica implementada:")
        print("   • Se consulta la tabla ejecucion_fuentes para verificar si una fecha ya fue procesada")
        print("   • Si ya fue procesada completamente (todas las fuentes con scraping_ok e ia_ok), no se ejecuta")
        print("   • Si no fue procesada, se ejecuta con la fecha efectiva correspondiente")
        print("   • Los días de compensación ejecutan como si fueran el día autorizado")
        print("   • El scheduler ahora ejecuta diariamente en lugar de solo martes/jueves")
    else:
        print("💥 Algunas pruebas fallaron!")

    return all_passed

if __name__ == "__main__":
    test_fecha_efectiva()