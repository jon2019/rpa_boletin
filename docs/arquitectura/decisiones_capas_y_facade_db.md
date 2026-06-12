# Decisiones de capas y `infrastructure/db/facade.py`

## Decisión sobre `infrastructure/db/facade.py`

### Decisión

`src/boletin/infrastructure/db/facade.py` queda **como contrato estable de aplicación** por ahora.

### Motivo

Hoy cumple una función útil y explícita:

- evita que `services/`, `runtime/`, `scraping/` y `notifications/` conozcan detalles finos de repositorios SQL
- mantiene un punto de acceso coherente para operaciones DB usadas por el pipeline
- reduce churn de imports mientras la capa `infrastructure/db/` sigue especializada por repositorio

### Qué NO es

No es una “muleta accidental”.
No debe crecer con lógica de negocio.
No debe duplicar implementación.

Su rol es:

- exponer operaciones de aplicación relacionadas con persistencia
- delegar a repositorios especializados

### Condición de reevaluación

Se reevalúa sólo si ocurre alguno de estos casos:

1. la fachada empieza a concentrar demasiadas funciones sin cohesión,
2. aparecen bounded contexts más claros que justifiquen gateways separados,
3. los casos de uso pueden depender de contratos más explícitos sin romper ergonomía.

---

## Límites entre capas

## Regla 1 — `main.py` y `runtime/`

Pueden depender de:

- `config/`
- `services/`
- `infrastructure/`

No deben contener lógica de negocio.

---

## Regla 2 — `services/`

Pueden depender de:

- `config/`
- `infrastructure/`
- `scraping/`
- otros módulos de `services/`

No deben depender de:

- `main.py`
- `runtime/`

`services/` orquesta casos de uso; no resuelve bootstrap.

---

## Regla 3 — `scraping/`

Puede depender de:

- `config/`
- `infrastructure/`

No debe depender de:

- `runtime/`
- `main.py`

`scraping/` es un subsistema técnico/orquestador, no un entrypoint.

---

## Regla 4 — `infrastructure/`

Puede depender de:

- `config/`
- librerías externas

No debe depender de:

- `services/`
- `runtime/`
- `main.py`

La infraestructura implementa detalles técnicos, no coordina casos de uso.

---

## Regla 5 — `config/`

Debe ser base compartida.

Puede ser importado por todas las capas.
No debe importar `services/`, `scraping/`, `runtime/` ni `main.py`.

---

## Regla 6 — `templates/`

Es un recurso estático.

No define dependencias.
Los adaptadores que renderizan HTML deben resolverlo por path estable, no por magia contextual.

---

## Resumen de dirección de dependencias

```text
main/runtime
   ↓
services
   ↓
scraping + infrastructure
   ↓
config
```

Con la salvedad de que:

- `services/` puede usar `scraping/` e `infrastructure/`
- `scraping/` puede usar `infrastructure/`
- `config/` no debe depender de capas superiores

---

## Validación práctica aplicada

Al cierre de esta etapa se verificó que:

- los módulos reubicados ya no viven en la raíz de `src/boletin`
- `main.py` importa `runtime.bootstrap`
- `pipeline_service.py` depende de módulos reubicados por capa
- la fachada DB quedó documentada como decisión explícita y no como residuo temporal
