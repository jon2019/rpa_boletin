# Indice de Documentacion - RPA Boletin Minero-Energetico

## Documentos Disponibles

### Documentos Markdown

| Archivo | Descripcion | Ubicacion |
|---------|-------------|-----------|
| `README.md` | Resumen operativo del sistema, arquitectura, compensacion, flujos y configuracion | `doc/README.md` |
| `modelo_datos.md` | Modelo relacional completo, diccionario de datos detallado e integridad de aplicacion | `doc/modelo_datos.md` |
| `modelo_relacional.md` | Vista resumida del modelo relacional real y relaciones clave | `doc/modelo_relacional.md` |
| `reglas_negocio.md` | Catalogo actualizado de reglas de negocio vigentes, alineado con el flujo actual | `doc/reglas_negocio.md` |

### Documentos Adicionales

| Archivo | Descripcion | Ubicacion |
|---------|-------------|-----------|
| `diccionario_boletin.xlsx` | Diccionario de datos en formato Excel | `doc/diccionario_boletin.xlsx` |
| `documentacion_completa.docx` | Documento consolidado para uso ejecutivo | `doc/documentacion_completa.docx` |

## Guia de Lectura

### Para entender el sistema rapido

1. `README.md`
2. `reglas_negocio.md`
3. `modelo_relacional.md`

### Para cambios de schema o datos

1. `modelo_datos.md`
2. `modelo_relacional.md`

### Para validar comportamiento de negocio

1. `reglas_negocio.md`
2. `README.md`

## Estado de Version

- **Version actual**: 3.0
- **Fecha**: Abril 2026
- **Cambios principales**:
  - documentacion alineada con el schema real de `db.py`
  - inclusion de `articulos_pendientes` y `score_empresas_conocidas`
  - formalizacion del flujo `solo-IA`
  - cobertura geografica dinamica con soporte para `Internacional`

---

La documentacion se mantiene versionada junto al codigo. Si cambia la logica operativa o el modelo de datos, estos archivos deben actualizarse en el mismo cambio.
