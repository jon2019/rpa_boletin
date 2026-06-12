@echo off
REM Ejecutar el pipeline completo del bolet?n RPA
REM Usa la estructura actual del paquete bajo src/boletin

cd /d %~dp0

REM Activar entorno virtual
call .venv\Scripts\activate

REM Asegurar imports desde src/
set PYTHONPATH=%cd%\src

REM Ejecutar el pipeline completo
echo Ejecutando pipeline completo con bloqueo...
python -m boletin.main --run-now

echo.
echo Pipeline finalizado. Presiona cualquier tecla para continuar...
pause > nul
