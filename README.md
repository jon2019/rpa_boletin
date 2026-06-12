# rpa_boletin

Sistema RPA para recopilar, puntuar, traducir y distribuir noticias de miner?a y energ?a.

## Estructura

```
rpa_boletin/
??? docs/
??? logs/
??? scripts/
??? src/
?   ??? boletin/
??? tests/
??? pyproject.toml
??? README.md
??? .env
```

## Ejecuci?n

- Pipeline una vez: `python -m boletin.main --run-now`
- Preview sin env?o: `python -m boletin.main --preview`

> Asegurate de tener `src/` en el entorno editable o usar el repo ra?z con el `pyproject.toml`.
