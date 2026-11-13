#!/usr/bin/env python3
"""Actualiza la tarjeta del MINSA en la portada con el último informe publicado (minsa/estado_informe.json)."""
import json
import os
import re

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
E = json.load(open(os.path.join(RAIZ, "minsa", "estado_informe.json"), encoding="utf-8"))
p = os.path.join(RAIZ, "index.html")
s = open(p, encoding="utf-8").read()
i = s.index('<a class="card" href="minsa/">')
j = s.index("</a>", i)
c = s[i:j]
c = re.sub(r'<span class="tag">Corte [0-9/]+</span>', f'<span class="tag">Corte {E["corte_txt"]}</span>', c)
vals = [f'{E["pim"] / 1e6:,.1f}', f'{100 * E["devengado"] / E["pim"]:.1f}%', f'{E["saldo"] / 1e6:,.1f}']
it = iter(vals)
c = re.sub(r'<div><b>[^<]*</b>', lambda m: f'<div><b>{next(it)}</b>', c, count=3)
s = s[:i] + c + s[j:]
open(p, "w", encoding="utf-8").write(s)
print("portada:", E["corte_txt"], vals)
