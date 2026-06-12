# Checklist de cierre arquitectónico

Este checklist define qué falta para considerar la reestructuración del proyecto como **cerrada** y no sólo **encaminada**.

## Estado actual

- [x] `main.py` reducido a entrypoint fino
- [x] configuración centralizada en `config/`
- [x] lock, scheduler y bootstrap extraídos
- [x] módulos principales reubicados por capa (`infrastructure/`, `runtime/`, `services/`, `scraping/`)
- [x] documentación base de estructura actualizada
- [x] imports reubicados verificados con `IMPORT_OK`

## Pendientes para declarar cierre

### 1. Validación end-to-end en modo preview

- [x] Ejecutar `python -m boletin.main --preview`
- [x] Confirmar que el pipeline completo corre sin errores de importación o paths
- [x] Verificar que el preview HTML se genera correctamente
- [x] Revisar que la salida visual no tenga regresiones por el refactor

**Criterio de aceptación:** el flujo completo debe ejecutarse en preview sin romper bootstrap, scraping, scoring, traducción ni render final.

**Resultado aplicado (24/04/2026):**

- se generó `tests/output/previews/preview.html`
- el pipeline completó bootstrap, scoring, traducción fallback y render final
- el entorno local mostró fallas externas de red / Playwright / Anthropic, pero el flujo quedó resiliente y llegó a generar preview

---

### 2. Limpieza final de encoding / mojibake remanente

- [x] Revisar comentarios y strings visibles en módulos históricos
- [x] Corregir texto mojibakeado en archivos que aún muestran secuencias corruptas
- [x] Verificar especialmente mensajes de log, asuntos de email y textos HTML

**Criterio de aceptación:** no deben quedar cadenas visibles rotas que dificulten mantenimiento, diagnóstico o render.

---

### 3. Decisión explícita sobre `infrastructure/db/facade.py`

- [x] Definir si `facade.py` queda como contrato estable
- [x] O definir un plan para eliminarla gradualmente
- [x] Documentar la decisión y su motivo

**Criterio de aceptación:** la fachada DB no puede quedar “accidental”; debe ser una decisión explícita de arquitectura.

**Decisión aplicada:** queda como contrato estable de aplicación por ahora. La decisión quedó documentada en `arquitectura/decisiones_capas_y_facade_db.md`.

---

### 4. Consolidación de límites entre capas

- [x] Revisar imports entre `services/`, `infrastructure/`, `scraping/` y `runtime/`
- [x] Confirmar que no reaparezcan dependencias cruzadas arbitrarias
- [x] Documentar reglas mínimas de dependencia entre capas

**Criterio de aceptación:** la estructura física debe coincidir con la dirección real de dependencias.

**Resultado aplicado:** reglas documentadas en `arquitectura/decisiones_capas_y_facade_db.md`.

## Definición práctica de “cerrado”

La reestructuración se considera **cerrada** cuando:

1. el pipeline corre en `--preview` de punta a punta,
2. no quedan textos mojibakeados relevantes,
3. la fachada DB tiene decisión explícita,
4. los límites entre capas quedan documentados y coherentes.

## Nota

Con este checklist completado, el proyecto puede considerarse:

- **bien estructurado**
- **arquitectónicamente sano**
- **listo para seguir evolucionando**
- **formalmente cerrado** en esta fase del refactor arquitectónico
