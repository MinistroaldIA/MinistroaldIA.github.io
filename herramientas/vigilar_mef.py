#!/usr/bin/env python3
"""Revisa, sin descargar datos, si el MEF publicó algo nuevo desde la última actualización del control.

Mira dos señales:
  1. La fecha de «Última actualización» de la Consulta Amigable (una sola página).
  2. La fecha de publicación (Last-Modified) del archivo diario de gasto en Datos Abiertos (solo la cabecera).

Las señales son las mismas para todas las entidades; lo visto se guarda por entidad en <id>/ro/fuentes_vistas.json.
Revisa cada entidad de herramientas/pliegos.json (salvo "control": false) y escribe en GITHUB_OUTPUT: novedad (true/false),
pliegos (lista JSON de las entidades que deben actualizarse), motivo, ca_fecha, da_publicado y da_clave (para la caché).
Si nada cambió pero la última actualización de una entidad tiene más de 24 horas, también la actualiza (red de seguridad).
Una fuente que no se pudo leer se informa como advertencia y no cuenta como «sin novedades»; si no se pudo leer ninguna,
la tarea termina con error (la página lo muestra como «No se pudo verificar si hay datos más recientes»).

Uso:  python herramientas/vigilar_mef.py [id ...]
      python herramientas/vigilar_mef.py --guardar CA_FECHA DA_PUBLICADO ID
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
PLIEGOS = json.load(open(os.path.join(AQUI, "pliegos.json"), encoding="utf-8"))
ACTIVOS = [k for k, v in PLIEGOS.items() if v.get("control", True)]
ANIO = max(PLIEGOS[k]["anio"] for k in ACTIVOS)
vistas_de = lambda eid: os.path.join(RAIZ, eid, "ro", "fuentes_vistas.json")
datos_de = lambda eid: os.path.join(RAIZ, eid, "ro", "datos.json")
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
    return fecha_http(r.headers.get("Last-Modified"))


FECHA_HTTP = re.compile(r"[A-Za-z]{3}, \d{2} [A-Za-z]{3} \d{4} \d{2}:\d{2}:\d{2} GMT")


def fecha_http(v):
    """La fecha de publicación llega de un servidor externo y termina en archivos y comandos de la tarea automática: solo se acepta
    con la forma estándar («Wed, 18 Nov 2026 13:39:08 GMT»). Cualquier otro texto se rechaza (revisión de seguridad del 19/11/2026, I1)."""
    if v is None:
        return None
    v = v.strip()
    if not FECHA_HTTP.fullmatch(v):
        raise ValueError("la fecha de publicación del archivo no tiene el formato esperado")
    return v


def clave(publicado):
    return re.sub(r"[^0-9A-Za-z]+", "-", publicado or "sin-fecha").strip("-")


def horas_desde_ultima(eid):
    try:
        p = json.load(open(datos_de(eid), encoding="utf-8"))["parametros"]["consulta_ok"]
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
        ruta = vistas_de(a[3])
        previo = json.load(open(ruta, encoding="utf-8")) if os.path.isfile(ruta) else {}
        nuevo = {"ca_fecha": a[1] or previo.get("ca_fecha"), "da_publicado": a[2] or previo.get("da_publicado"),
                 "guardado": dt.datetime.now(LIMA).strftime("%Y-%m-%dT%H:%M")}
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        json.dump(nuevo, open(ruta, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"guardado ({a[3]}): {nuevo}")
        return
    ids = a or ACTIVOS
    if any(i not in PLIEGOS for i in ids):              # la entidad pedida llega como dato: solo se aceptan claves conocidas
        print(f"::error::Entidad desconocida: {[i for i in ids if i not in PLIEGOS]}. Válidas: {list(PLIEGOS)}")
        sys.exit(1)
    errores = {}
    try:
        ca = fecha_consulta_amigable()
        if not ca:
            errores["Consulta Amigable"] = "la página no muestra la fecha de actualización"
    except Exception as e:
        errores["Consulta Amigable"] = str(e)[:200]
        ca = None
    try:
        da = publicado_datos_abiertos()
        if not da:
            errores["Datos Abiertos"] = "el archivo no informa su fecha de publicación"
    except Exception as e:
        errores["Datos Abiertos"] = str(e)[:200]
        da = None
    for f, e in errores.items():
        print(f"::warning::No se pudo verificar {f}: {e}")
    pend, motivos = [], []
    for eid in ids:
        ruta = vistas_de(eid)
        vistas = json.load(open(ruta, encoding="utf-8")) if os.path.isfile(ruta) else {}
        nov, motivo = decidir(vistas, ca, da, horas_desde_ultima(eid))
        if not os.path.isfile(datos_de(eid)):
            nov, motivo = True, "entidad nueva: todavía no tiene control publicado"
        print(f"{eid}: {'actualizar' if nov else 'al día'} · {motivo}")
        if nov:
            pend.append(eid)
            motivos.append(f"{eid}: {motivo}")
    salida(novedad=str(bool(pend)).lower(), pliegos=json.dumps(pend), motivo=" | ".join(motivos) or "sin novedades del MEF",
           ca_fecha=ca or "", da_publicado=da or "", da_clave=clave(da))
    # Si no se pudo leer NINGUNA fuente, la consulta no verificó nada: la tarea termina con error para que la página diga
    # «No se pudo verificar si hay datos más recientes» y no «sin novedades».
    if len(errores) == 2:
        print("::error::No se pudo verificar ninguna fuente del MEF; no se afirma que no haya datos nuevos.")
        sys.exit(1)


if __name__ == "__main__":
    main()
