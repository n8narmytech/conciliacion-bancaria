"""
Endpoint de diagnóstico mínimo.

No importa nada del proyecto ni ninguna dependencia externa: solo la
librería estándar. Sirve para separar dos causas cuando el despliegue
falla:

  - /api/ping responde y /api/health no  → el runtime de Python está bien;
    el problema está en las dependencias o en los módulos de la lógica.
  - /api/ping tampoco responde           → el problema es la configuración
    de la función en sí (runtime, rutas, build).

Devuelve además qué archivos y carpetas viajaron en el bundle, que es
justamente lo que suele faltar cuando la función crashea al importar.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        dir_api = os.path.dirname(os.path.abspath(__file__))
        dir_raiz = os.path.dirname(dir_api)

        def listar(ruta):
            try:
                return sorted(os.listdir(ruta))[:30]
            except OSError as e:
                return f"no se pudo leer: {e}"

        # Probar los imports de a uno para ver cuál es el que rompe
        estado_imports = {}
        for modulo in ("pandas", "openpyxl", "xlrd", "rapidfuzz",
                       "unidecode", "requests", "fastapi"):
            try:
                __import__(modulo)
                estado_imports[modulo] = "ok"
            except Exception as e:
                estado_imports[modulo] = f"{type(e).__name__}: {e}"

        cuerpo = {
            "ok": True,
            "python": sys.version,
            "cwd": os.getcwd(),
            "dir_funcion": dir_api,
            "archivos_en_raiz": listar(dir_raiz),
            "archivos_en_api": listar(dir_api),
            "bancos": listar(os.path.join(dir_raiz, "bancos")),
            "nucleo": listar(os.path.join(dir_raiz, "nucleo")),
            "dependencias": estado_imports,
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(cuerpo, ensure_ascii=False, indent=2).encode("utf-8"))
