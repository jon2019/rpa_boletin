# Modelo relacional — vista resumida

## Vista actual

```text
 +-------------------+        +-----------------------+
 |      FUENTES      |        |        PAISES         |
 |-------------------|        |-----------------------|
 | PK id             |        | PK id                 |
 | UK url            |        | UK nombre             |
 | nombre            |        | nombre_en             |
 | url_rss           |        | codigo_iso            |
 | pais              |------->| cuota                 |
 | metodo            |        | orden                 |
 | scrape_selector   |        | activo                |
 | usuario           |        +-----------------------+
 | clave             |
 | login_url         |
 | post_login_url    |
 | activa            |
 +-------------------+
          |
          | url (lógica)
          v
 +---------------------------+         +---------------------------+
 |    EJECUCION_FUENTES      |         |   ARTICULOS_PENDIENTES    |
 |---------------------------|         |---------------------------|
 | PK id                     |         | PK id                     |
 | url_fuente                |         | url_fuente                |
 | fecha_ejecucion           |         | fecha_ef                  |
 | nombre_fuente             |         | titulo                    |
 | scraping_ok               |         | url                       |
 | ia_ok                     |         | resumen                   |
 | noticias_obtenidas        |         | fecha                     |
 | noticias_enviadas         |         | fuente                    |
 | error_detalle             |         | pais                      |
 | creado_en                 |         | ia_procesada              |
 | actualizado_en            |         | creado_en                 |
 +---------------------------+         +---------------------------+
          |
          | completa ciclo
          v
 +---------------------------+         +---------------------------+
 |    NOTICIAS_ENVIADAS      |         |        ENVIOS_LOG         |
 |---------------------------|         |---------------------------|
 | PK id                     |         | PK id                     |
 | UK url_hash               |         | fecha                     |
 | titulo                    |         | total_noticias            |
 | fuente                    |         | por_pais                  |
 | pais                      |         | ok                        |
 | url                       |         +---------------------------+
 | enviado_en                |
 +---------------------------+

 +---------------------------+
 |   PROCESOS_PROGRAMADOS    |
 |---------------------------|
 | PK id                     |
 | UK nombre                 |
 | ultima_ejecucion          |
 | ultimo_estado_ok          |
 | detalle                   |
 | actualizado_en            |
 +---------------------------+

 +---------------------------+  +---------------------------+  +---------------------------+
 |      SCORE_REGLAS         |  |      SCORE_EMPRESAS       |  | SCORE_EMPRESAS_CONOCIDAS  |
 |---------------------------|  |---------------------------|  |---------------------------|
 | PK id                     |  | PK id                     |  | PK id                     |
 | UK codigo                 |  | UK nombre                 |  | UK nombre                 |
 | descripcion               |  | activa                    |  | activa                    |
 | puntos                    |  +---------------------------+  +---------------------------+
 | activa                    |
 +---------------------------+

 +---------------------------+
 |      SCORE_KEYWORDS       |
 |---------------------------|
 | PK id                     |
 | UK keyword                |
 | activa                    |
 +---------------------------+
```

## Relaciones clave verificadas

- `fuentes.url` se usa como referencia lógica en `ejecucion_fuentes.url_fuente`.
- `fuentes.url` se usa como referencia lógica en `articulos_pendientes.url_fuente`.
- `paises.nombre` define cuotas y orden editorial.
- `noticias_enviadas.url_hash` evita reenvío histórico.
- `envios_log` registra intentos de envío sin imponer unicidad por fecha efectiva.
- `procesos_programados` desacopla la frecuencia de procesos auxiliares del pipeline principal.
- `score_reglas`, `score_empresas`, `score_empresas_conocidas` y `score_keywords` parametrizan el scoring.

## Observaciones de diseño

- `ejecucion_fuentes` es el checkpoint central del pipeline.
- `articulos_pendientes` habilita el modo `solo-IA`.
- `ia_procesada` evita doble cobro a IA si falla la limpieza final.
- `fuentes` hoy soporta RSS, scraping y login.
- La cobertura geográfica es dinámica: se toma de `paises`, y lo demás cae en `Internacional`.

## Referencia

Para detalle completo de columnas, índices y reglas de integridad, ver `modelo_datos.md`.
