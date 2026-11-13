#!/usr/bin/env python3
"""Lee de la Consulta Amigable del MEF (Portal de Transparencia Económica) la ejecución del día de un pliego,
por unidad ejecutora y en una fuente de financiamiento.

La Consulta Amigable se actualiza a diario y suele llevar un día de ventaja al archivo de Datos Abiertos.
Navega igual que una persona: Nivel de Gobierno → Sector → Pliego → Fuente → Ejecutora.
Cada fila trae sus montos exactos en el valor del botón de selección:
    código / PIA / PIM / compromiso anual / atención de compromiso mensual / devengado / girado / certificación

Uso de prueba:  python herramientas/consulta_amigable.py 2026 E 11 011 1
"""
import datetime as dt
import html as _html
import re
import sys
from urllib.parse import urljoin

BASE = "https://apps5.mineco.gob.pe/transparencia/Navegador/Navegar.aspx?y={anio}&ap=ActProy"
MESES = {m: i for i, m in enumerate(["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                                     "setiembre", "octubre", "noviembre", "diciembre"], 1)}
MESES["septiembre"] = 9
CAMPOS = ["PIA", "PIM", "COMPROMETIDO_ANUAL", "COMPROMETIDO", "DEVENGADO", "GIRADO", "CERTIFICADO"]


def _inputs(txt, tipo):
    out = []
    for m in re.finditer(r"<input\b[^>]*>", txt, re.I):
        tag = m.group(0)
        if not re.search(rf'type\s*=\s*"{tipo}"', tag, re.I):
            continue
        name = re.search(r'name\s*=\s*"([^"]*)"', tag)
        val = re.search(r'value\s*=\s*"([^"]*)"', tag)
        out.append((_html.unescape(name.group(1)) if name else "", _html.unescape(val.group(1)) if val else ""))
    return out


def _selects(txt):
    out = []
    for m in re.finditer(r'<select\b[^>]*name\s*=\s*"([^"]*)"[^>]*>(.*?)</select>', txt, re.I | re.S):
        sel = re.search(r'<option\b[^>]*selected[^>]*value\s*=\s*"([^"]*)"', m.group(2), re.I) or \
            re.search(r'<option\b[^>]*value\s*=\s*"([^"]*)"[^>]*selected', m.group(2), re.I)
        if sel:
            out.append((_html.unescape(m.group(1)), _html.unescape(sel.group(1))))
    return out


def _accion(txt, url):
    m = re.search(r'<form\b[^>]*action\s*=\s*"([^"]*)"', txt, re.I)
    return urljoin(url, _html.unescape(m.group(1))) if m else url


def fecha_actualizacion(txt):
    """«Última actualización: 12 de noviembre de 2026.» → date(2026, 11, 12)."""
    m = re.search(r'LblLastUpdate"[^>]*>\s*(\d{1,2})\s+de\s+([a-záéíóú]+)\s+de\s+(\d{4})', txt, re.I) or \
        re.search(r"ltima actualizaci[oó]n:\s*(\d{1,2})\s+de\s+([a-záéíóú]+)\s+de\s+(\d{4})", re.sub(r"<[^>]+>", " ", _html.unescape(txt)), re.I)
    if not m:
        return None
    return dt.date(int(m.group(3)), MESES[m.group(2).lower()], int(m.group(1)))


def _filas_con_nombre(txt):
    """Filas de la tabla: (valor del botón de selección, nombre). El nombre va como «código: NOMBRE» en la misma fila."""
    out = []
    for tr in re.findall(r"<tr\b.*?</tr>", txt, re.I | re.S):
        m = re.search(r'<input\b[^>]*type\s*=\s*"radio"[^>]*value\s*=\s*"([^"]*)"', tr, re.I) or \
            re.search(r'<input\b[^>]*value\s*=\s*"([^"]*)"[^>]*type\s*=\s*"radio"', tr, re.I)
        if not m:
            continue
        v = _html.unescape(m.group(1))
        t = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", tr))).strip()
        cod = v.split("/")[0]
        n = re.search(re.escape(cod) + r"\s*:\s*(.+?)(?:\s+-?[\d,]+(?:\.\d+)?)*\s*(?:Sistema de Seguimiento.*)?$", t)
        out.append((v, n.group(1).strip() if n else t))
    return out


def _montos(v):
    p = v.split("/")
    return (p[0], {c: float(x) for c, x in zip(CAMPOS, p[1:])}) if len(p) == 1 + len(CAMPOS) else (None, None)


def por_ejecutora(anio, nivel, sector, pliego, fuente="1", timeout=60, productos=False):
    """Devuelve (filas, fecha, prod).
    filas = {sec_ejec: {campo: monto}}; prod = {sec_ejec: {código: {"nombre": ..., campo: monto}}} si productos=True."""
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (cuadro de control de saldos)"})
    r = s.get(BASE.format(anio=anio), timeout=timeout)
    r.raise_for_status()
    fecha = fecha_actualizacion(r.text)          # la fecha aparece en la portada del navegador

    def paso(est, boton, empieza=None):
        txt, url = est
        datos = dict(_inputs(txt, "hidden"))
        datos.update(dict(_selects(txt)))
        if empieza is not None:
            radios = [v for n, v in _inputs(txt, "radio") if v.split("/")[0] == empieza]
            if not radios:
                raise RuntimeError(f"Consulta Amigable: no aparece la fila «{empieza}» antes de «{boton}»")
            datos["grp1"] = radios[0]
        btn = [n for n, v in _inputs(txt, "submit") if v == boton]
        if not btn:
            raise RuntimeError(f"Consulta Amigable: no aparece el botón «{boton}»")
        datos[btn[0]] = boton
        rr = s.post(_accion(txt, url), data=datos, timeout=timeout)
        rr.raise_for_status()
        return rr.text, rr.url

    est = (r.text, r.url)
    est = paso(est, "Nivel de Gobierno")
    est = paso(est, "Sector", nivel)
    est = paso(est, "Pliego", sector)
    est = paso(est, "Fuente", pliego)
    ejec = paso(est, "Ejecutora", fuente)
    filas = {}
    for _, v in _inputs(ejec[0], "radio"):
        k, m = _montos(v)
        if k:
            filas[k] = m
    if not filas:
        raise RuntimeError("Consulta Amigable: la lista de unidades ejecutoras llegó vacía")
    prod = {}
    if productos:
        for ue in filas:                           # desde la lista de unidades se entra a cada una (el estado viaja en la página)
            txt, _ = paso(ejec, "Producto/Proyecto", ue)
            d = {}
            for v, nom in _filas_con_nombre(txt):
                k, m = _montos(v)
                if k:
                    d[k] = dict(m, nombre=nom)
            prod[ue] = d
    return filas, fecha, prod


if __name__ == "__main__":
    a = sys.argv[1:] or ["2026", "E", "11", "011", "1"]
    f, fecha, pr = por_ejecutora(int(a[0]), a[1], a[2], a[3], a[4] if len(a) > 4 else "1", productos="--productos" in sys.argv)
    print(f"actualización: {fecha} · unidades: {len(f)} · productos/proyectos: {sum(len(x) for x in pr.values())}")
    print(f"PIM S/ {sum(x['PIM'] for x in f.values()) / 1e6:,.1f} M · devengado S/ {sum(x['DEVENGADO'] for x in f.values()) / 1e6:,.1f} M")
