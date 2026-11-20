#!/usr/bin/env python3
"""Herramienta común de los portales de pliegos (Ministro al Día y GORE al Día).

Agrega el marco común (menú y efectos: herramientas/marco.css, portal.js y marco.js) a las páginas de una entidad y a la
portada, y genera herramientas/portal.js desde herramientas/portal.json (identidad propia de cada portal).

Uso: python herramientas/insertar_marco.py <id>
Las páginas del informe las genera el núcleo y se reemplazan en cada actualización; esta herramienta vuelve a poner la
línea del marco en su <head>. Si una página ya la tiene, no la toca; si tiene la línea anterior (sin portal.js), la
reemplaza. Se puede correr las veces que haga falta.
"""
import glob
import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIEJA = '<link rel="stylesheet" href="/herramientas/marco.css"><script src="/herramientas/marco.js"></script>'
LINEA = ('<link rel="stylesheet" href="/herramientas/marco.css"><script src="/herramientas/portal.js"></script>'
         '<script src="/herramientas/marco.js"></script>')


def escribir(ruta, texto):
    with open(ruta, "w", encoding="utf-8", newline="\n") as f:
        f.write(texto)


# portal.js: identidad del portal para el menú (nombre, sello, colores, repositorio), desde portal.json
fp = os.path.join(RAIZ, "herramientas", "portal.json")
if os.path.isfile(fp):
    P = json.load(open(fp, encoding="utf-8"))
    js = "window.MC_PORTAL = " + json.dumps({k: v for k, v in P.items() if k != "commit"}, ensure_ascii=False) + ";\n"
    fj = os.path.join(RAIZ, "herramientas", "portal.js")
    if not os.path.isfile(fj) or open(fj, encoding="utf-8").read() != js:
        escribir(fj, js)
        print("portal.js actualizado")

eid = sys.argv[1] if len(sys.argv) > 1 else ""
if not eid or not os.path.isdir(os.path.join(RAIZ, eid)):
    sys.exit(f"Entidad desconocida: «{eid}»")
paginas = [os.path.join(RAIZ, "index.html")] + sorted(glob.glob(os.path.join(RAIZ, eid, "*.html"))) + \
    sorted(glob.glob(os.path.join(RAIZ, eid, "ro", "*.html")))
for p in paginas:
    if not os.path.isfile(p):
        continue
    s = open(p, encoding="utf-8").read()
    if LINEA in s:
        continue
    if VIEJA in s:
        escribir(p, s.replace(VIEJA, LINEA))
        print(f"marco actualizado: {os.path.relpath(p, RAIZ)}")
        continue
    i = s.lower().find("</head>")
    if i < 0:
        print(f"sin <head>, no se cambia: {os.path.relpath(p, RAIZ)}")
        continue
    escribir(p, s[:i] + LINEA + "\n" + s[i:])
    print(f"marco agregado: {os.path.relpath(p, RAIZ)}")
