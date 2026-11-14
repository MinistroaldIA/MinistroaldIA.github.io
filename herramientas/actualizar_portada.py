#!/usr/bin/env python3
"""Actualiza en la portada la tarjeta del informe de una entidad con su último informe publicado (<id>/estado_informe.json).

Uso: python herramientas/actualizar_portada.py <id>
Si la portada no tiene tarjeta para esa entidad (<a class="card" href="<id>/">), avisa y no cambia nada.
"""
import json
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
eid = sys.argv[1] if len(sys.argv) > 1 else "minsa"
fe = os.path.join(RAIZ, eid, "estado_informe.json")
p = os.path.join(RAIZ, "index.html")
s = open(p, encoding="utf-8").read()
marca = f'<a class="card" href="{eid}/">'
if not os.path.isfile(fe) or marca not in s:
    print(f"«{eid}» no tiene informe publicado o tarjeta en la portada: no se cambia nada.")
    sys.exit(0)
E = json.load(open(fe, encoding="utf-8"))
i = s.index(marca)
j = s.index("</a>", i)
c = s[i:j]
c = re.sub(r'<span class="tag">Corte [0-9/]+</span>', f'<span class="tag">Corte {E["corte_txt"]}</span>', c)
vals = [f'{E["pim"] / 1e6:,.1f}', f'{100 * E["devengado"] / E["pim"]:.1f}%', f'{E["saldo"] / 1e6:,.1f}']
it = iter(vals)
c = re.sub(r'<div><b>[^<]*</b>', lambda m: f'<div><b>{next(it)}</b>', c, count=3)
s = s[:i] + c + s[j:]
open(p, "w", encoding="utf-8").write(s)
print(f"portada ({eid}):", E["corte_txt"], vals)
