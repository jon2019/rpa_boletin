from pathlib import Path
import runpy

ROOT_DIR = Path(__file__).resolve().parents[2]
runpy.run_path(str(ROOT_DIR / 'scripts' / 'exports' / 'generar_excel.py'), run_name='__main__')
