#!/usr/bin/env python3
"""Revisa, sin descargar datos, si el MEF publicó algo nuevo desde la última actualización del control.

Mira dos señales:
  1. La fecha de «Última actualización» de la Consulta Amigable (una sola página).
  2. La fecha de publicación (Last-Modified) del archivo diario de gasto en Datos Abiertos (solo la cabecera).

Compara con minsa/ro/fuentes_vistas.json, que guarda lo visto en la última actualización completa.
Escribe en GITHUB_OUTPUT: novedad (true/false), motivo, ca_fecha, da_publicado y da_clave (para la caché).
Si nada cambió pero la última actualización tiene más de 24 horas, también pide correr (red de seguridad).

Uso:  python herramientas/vigilar_mef.py [ruta_vistas]
      python herramientas/vigilar_mef.py --guardar CA_FECHA DA_PUBLICADO [ruta_vistas]
"""
import datetime as dt
import json
import os
import re
import sys
from email.utils import parsedate_to_datetime

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
RAIZ = os.path.dirname(AQUI)
VISTAS = os.path.join(RAIZ, "minsa", "ro", "fuentes_vistas.json")
DATOS = os.path.join(RAIZ, "minsa", "ro", "datos.json")
ANIO = 2026
DA = f"https://fs.datosabiertos.mef.gob.pe/datastorefiles/{ANIO}-Gasto-Diario.csv"
UA = {"User-Agent": "Mozilla/5.0 (cuadro de control de saldos)"}
LIMA = dt.timezone(dt.timedelta(hours=-5))


def fecha_consulta_amigable():
    import requests
    import consulta_amigable as CA
    r = requests.get(CA.BASE.format(anio=ANIO), timeout=60, headers=UA)
    r.raise_for_status()
    f = CA.fecha_actualizacion(r.text)
    return f.isoformat() if f else None


def publicado_datos_abiertos():
    import requests
    r = requests.head(DA, timeout=60, headers=UA, allow_redirects=True)
    r.raise_for_status()
    return r.headers.get("Last-Modified")


def clave(publicado):
    return re.sub(r"[^0-9A-Za-z]+", "-", publicado or "sin-fecha").strip("-")


def horas_desde_ultima():
    try:
        p = json.load(open(DATOS, encoding="utf-8"))["parametros"]["consulta_ok"]
        t = dt.datetime.fromisoformat(p).replace(tzinfo=LIMA)
        return (dt.datetime.now(LIMA) - t).total_seconds() / 3600
    except Exception:
        return None


def decidir(vistas, ca, da, horas):
    """Devuelve (novedad, motivo). Una señal que no se pudo leer no cuenta como novedad."""
    motivos = []
    if ca and (not vistas.get("ca_fecha") or ca > vistas["ca_fecha"]):
        motivos.append(f"Consulta Amigable al {ca} (antes {vistas.get('ca_fecha') or 'sin registro'})")
    if da and da != vistas.get("da_publicado"):
        try:
            nuevo = not vistas.get("da_publicado") or parsedate_to_datetime(da) > parsedate_to_datetime(vistas["da_publicado"])
        except Exception:
            nuevo = True
        if nuevo:
            motivos.append(f"Datos Abiertos publicado {da} (antes {vistas.get('da_publicado') or 'sin registro'})")
    if not motivos and horas is not None and horas >= 24:
        motivos.append(f"red de seguridad: {horas:.0f} horas sin actualizar")
    return bool(motivos), "; ".join(motivos) or "sin novedades del MEF"


def salida(**kv):
    ruta = os.environ.get("GITHUB_OUTPUT")
    for k, v in kv.items():
        print(f"{k}={v}")
    if ruta:
        with open(ruta, "a", encoding="utf-8") as f:
            for k, v in kv.items():
                f.write(f"{k}={v}\n")


def main():
    a = sys.argv[1:]
    if a and a[0] == "--guardar":
        ruta = a[3] if len(a) > 3 else VISTAS
        previo = json.load(open(ruta, encoding="utf-8")) if os.path.isfile(ruta) else {}
        nuevo = {"ca_fecha": a[1] or previo.get("ca_fecha"), "da_publicado": a[2] or previo.get("da_publicado"),
                 "guardado": dt.datetime.now(LIMA).strftime("%Y-%m-%dT%H:%M")}
        json.dump(nuevo, open(ruta, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"guardado: {nuevo}")
        return
    ruta = a[0] if a else VISTAS
    vistas = json.load(open(ruta, encoding="utf-8")) if os.path.isfile(ruta) else {}
    try:
        ca = fecha_consulta_amigable()
    except Exception as e:
        print(f"No se pudo leer la Consulta Amigable: {e}")
        ca = None
    try:
        da = publicado_datos_abiertos()
    except Exception as e:
        print(f"No se pudo leer Datos Abiertos: {e}")
        da = None
    nov, motivo = decidir(vistas, ca, da, horas_desde_ultima())
    salida(novedad=str(nov).lower(), motivo=motivo, ca_fecha=ca or "", da_publicado=da or "", da_clave=clave(da))


if __name__ == "__main__":
    main()
