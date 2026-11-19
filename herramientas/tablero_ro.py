#!/usr/bin/env python3
"""Cuadro de control diario de Recursos Ordinarios por unidad ejecutora.

Uso:  python herramientas/tablero_ro.py <id>               (descarga el archivo diario del MEF)
      python herramientas/tablero_ro.py <id> --json X.json  (prueba con un JSON ya agregado por pliego_navegador.js)

Lee el archivo «<año>-Gasto-Diario.csv» de Datos Abiertos del MEF sin guardarlo (≈10 GB), toma solo las filas del
pliego en la fuente Recursos Ordinarios y deja en <id>/ro/:
  - historial.csv: una fila por fecha de corte y unidad ejecutora (se reemplaza si la fecha ya existe);
  - datos.json:    lo que lee el tablero (último corte, variación respecto del corte anterior y series diarias).

Riesgo de reversión = saldo de RO que quedaría sin devengar al 31/12 si cada unidad (meta × producto × genérica)
mantiene el promedio mensual de devengado de sus tres últimos meses completos, con tope en su saldo y piso en cero.
Es el mismo método del informe; en las pruebas con 2024 y 2025 sobrestimó el saldo final, por eso es una referencia.
"""
import calendar
import csv
import datetime as dt
import io
import json
import math
import os
import re
import shutil
import sys
from email.utils import parsedate_to_datetime

import pandas as pd

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
URL = "https://fs.datosabiertos.mef.gob.pe/datastorefiles/{anio}-Gasto-Diario.csv"
# Columnas del archivo de Datos Abiertos, buscadas por su NOMBRE en el encabezado (no por posición): si el MEF reordena
# columnas se siguen leyendo bien; si falta alguna, se rechaza el archivo y queda publicado el último corte válido.
COLUMNAS = {"MES": "MES_EJE", "NIVEL": "NIVEL_GOBIERNO", "SECTOR": "SECTOR", "PLIEGO": "PLIEGO", "SEC_EJEC": "SEC_EJEC",
            "EJEC_NOM": "EJECUTORA_NOMBRE", "SEC_FUNC": "SEC_FUNC", "TIPO": "TIPO_ACT_PROY", "PROD": "PRODUCTO_PROYECTO",
            "PROD_NOM": "PRODUCTO_PROYECTO_NOMBRE", "META": "META", "FUENTE": "FUENTE_FINANCIAMIENTO", "RUBRO": "RUBRO", "GEN": "GENERICA"}
PROD_NOM = {}                    # nombres de productos y proyectos según Datos Abiertos (respaldo de los de la Consulta Amigable)
MONTOS = ["PIA", "PIM", "CERTIFICADO", "COMPROMETIDO_ANUAL", "COMPROMETIDO", "DEVENGADO", "GIRADO"]
LLAVES = ["SEC_EJEC", "SEC_FUNC", "TIPO_ACT_PROY", "PRODUCTO_PROYECTO", "META", "RUBRO", "GENERICA"]
METODO = "2026-11-19.2"     # subir la versión (y describirla en <id>/ro/revisiones.json) cada vez que cambie el cálculo
TOL_VAR = 1.0               # soles: una variación del devengado entre cortes dentro de ±1 sol cuenta como «sin variación»
ESTADOS = {"aumento": "Aumento del devengado", "sin_variacion": "Sin variación del devengado",
           "ajuste_negativo": "Ajuste neto negativo", "sin_comparacion": "Sin comparación"}


class DatosInvalidos(ValueError):
    """El insumo del MEF no tiene la forma esperada: no se publica nada y queda el último corte válido."""


FUENTES = {}               # resultado de cada fuente en esta corrida (se publica en estado.json)


class CorteRetrocede(RuntimeError):
    """La fuente disponible trae un corte anterior al ya publicado: se conserva el publicado."""
# Feriados nacionales: los mismos del día por día municipal (nucleo/control/dia.py). Antes aquí solo estaban los de diciembre:
# en 2026 da lo mismo (desde noviembre solo quedan el 8, 9 y 25 de diciembre), pero en 2027 se habrían contado como hábiles
# el 1 de enero, Semana Santa, el 28 y 29 de julio, etc. Jueves y Viernes Santo se calculan desde la fecha de Pascua.
FIJOS = [(1, 1), (5, 1), (6, 7), (6, 29), (7, 23), (7, 28), (7, 29), (8, 6), (8, 30), (10, 8), (11, 1), (12, 8), (12, 9), (12, 25)]
CALENDARIO = ("Días hábiles: lunes a viernes, sin los feriados nacionales. Se cuentan desde el día siguiente al corte "
              "hasta el 31 de diciembre. No incluye días no laborables que el Gobierno decrete después.")
_MIN = {"De", "Del", "La", "Las", "Los", "Y", "En", "El", "Para", "A", "Por"}
_TILDE = {"Administracion": "Administración", "Direccion": "Dirección", "Credito": "Crédito", "Estrategicos": "Estratégicos", "Pediatricas": "Pediátricas",
          "Creacion": "Creación", "Atencion": "Atención", "Rehabilitacion": "Rehabilitación", "Oftalmologia": "Oftalmología", "Neurologicas": "Neurológicas",
          "Movil": "Móvil", "Huaycan": "Huaycán", "Lima Este - Vitarte": "Lima Este - Vitarte", "Bartolome": "Bartolomé", "Valdizan": "Valdizán", "Ampliacion": "Ampliación", "Preparacion": "Preparación", "Recuperacion": "Recuperación",
          "Implementacion": "Implementación", "Adquisicion": "Adquisición", "Instalacion": "Instalación", "Construccion": "Construcción",
          "Prevencion": "Prevención", "Vacunacion": "Vacunación", "Gestion": "Gestión", "Infeccion": "Infección", "Nutricion": "Nutrición",
          "Poblacion": "Población", "Evaluacion": "Evaluación", "Proteccion": "Protección", "Rehabilitacion": "Rehabilitación",
          "Educacion": "Educación", "Region": "Región", "Produccion": "Producción", "Comunicacion": "Comunicación",
          "Informacion": "Información", "Agricola": "Agrícola", "Energia": "Energía", "Mineria": "Minería", "Economia": "Economía", "Publico": "Público", "Publica": "Pública",
          "Ejecucion": "Ejecución", "Supervision": "Supervisión", "Formacion": "Formación", "Tecnico": "Técnico", "Tecnica": "Técnica", "Transito": "Tránsito"}
# siglas que el paso a minúsculas no debe alterar (se comparan sin distinguir mayúsculas)
_SIGLAS = {"MINSA", "MINEDU", "GORE", "DIRESA", "DISA", "DIRIS", "UGEL", "DRE", "DRA", "DREP", "PRONIS", "PCRIS", "CENARES", "INEN", "INSN", "ESSALUD",
           "SENASA", "MTC", "PROVIAS", "IGSS", "PNP", "SIS", "UE", "PIP", "ONP", "SUNAT", "SUNARP", "INEI", "INDECI", "CENEPRED", "SERFOR", "ANA", "MIDIS", "MIMP", "MEF"}


def nombre(t):
    """Nombre oficial en mayúsculas → forma legible con tildes, sin alterar siglas."""
    w = str(t).strip().title().split()
    w = [x.upper() if x.strip("()-.,").upper() in _SIGLAS else x for x in w]
    t = " ".join(x.lower() if (i and x in _MIN) else x for i, x in enumerate(w))
    t = re.sub(r"\b(Iii|Ii|Iv|Vii|Viii)\b", lambda m: m.group(0).upper(), t)     # números romanos (II-2, III-1)
    for a, b in _TILDE.items():
        t = re.sub(r"\b" + re.escape(a) + r"\b", b, t)                            # palabra completa: «Region» no toca «Regional»
    return t


def monto(texto, donde):
    """Importe del archivo del MEF. Vacío = 0 (el MEF deja en blanco los montos nulos); cualquier otro texto que no sea un
    número finito invalida el lote completo, en vez de sumarse como cero en silencio."""
    t = texto.strip()
    if t == "":
        return 0.0
    try:
        v = float(t)
    except ValueError:
        raise DatosInvalidos(f"importe no numérico «{texto[:30]}» en {donde}") from None
    if not math.isfinite(v):
        raise DatosInvalidos(f"importe no finito «{texto[:30]}» en {donde}")
    return v


def esquema(encabezado):
    """Posición de cada columna requerida según el encabezado real. Falla si falta alguna o si hay nombres repetidos."""
    cab = next(csv.reader([encabezado.lstrip("﻿")]))
    cab = [c.strip().upper() for c in cab]
    if len(cab) < 10:
        raise DatosInvalidos(f"encabezado con {len(cab)} columna(s): el separador o el formato no es el esperado")
    rep = sorted({c for c in cab if cab.count(c) > 1})
    if rep:
        raise DatosInvalidos(f"columnas repetidas en el encabezado: {rep}")
    req = dict(COLUMNAS, **{f"M_{m}": f"MONTO_{m}" for m in MONTOS})
    falt = sorted(v for v in req.values() if v not in cab)
    if falt:
        raise DatosInvalidos(f"faltan columnas en el encabezado: {falt}")
    return {k: cab.index(v) for k, v in req.items()}, len(cab)


def leer_filas(lineas, cfg):
    """Agrega las filas RO del pliego. `lineas` = iterador de líneas en bytes, la primera es el encabezado.
    Valida esquema, número de campos e importes; ante cualquier anomalía lanza DatosInvalidos (no publica)."""
    lineas = iter(lineas)
    try:
        cab = next(lineas)
    except StopIteration:
        raise DatosInvalidos("archivo vacío") from None
    pos, ncol = esquema(cab.decode("utf-8", "replace"))
    clave = f'"{cfg["pliego"]}"'.encode()
    agg, nombres, n = {}, {}, 0
    for b in lineas:
        n += 1
        if clave not in b:                          # filtro rápido: solo se analizan las filas que mencionan el pliego
            continue
        f = next(csv.reader([b.decode("utf-8", "replace").rstrip("\r")]))
        if len(f) != ncol:
            raise DatosInvalidos(f"la línea {n + 1:,} tiene {len(f)} campos y el encabezado {ncol} (fila truncada o mal formada)")
        if f[pos["NIVEL"]] != cfg["nivel"] or f[pos["SECTOR"]] != cfg["sector"] or f[pos["PLIEGO"]] != cfg["pliego"] or f[pos["FUENTE"]] != "1":
            continue
        try:
            mes = int(f[pos["MES"]])
        except ValueError:
            raise DatosInvalidos(f"mes no numérico «{f[pos['MES']]}» en la línea {n + 1:,}") from None
        if not 0 <= mes <= 12:
            raise DatosInvalidos(f"mes fuera de rango ({mes}) en la línea {n + 1:,}")
        k = (mes, f[pos["SEC_EJEC"]], f[pos["SEC_FUNC"]], f[pos["TIPO"]], f[pos["PROD"]], f[pos["META"]], f[pos["RUBRO"]], f[pos["GEN"]])
        a = agg.get(k)
        if a is None:
            a = agg[k] = [0.0] * len(MONTOS)
        for i, m in enumerate(MONTOS):
            a[i] += monto(f[pos[f"M_{m}"]], f"la línea {n + 1:,}, columna MONTO_{m}")
        nombres[f[pos["SEC_EJEC"]]] = f[pos["EJEC_NOM"]]
        PROD_NOM[f[pos["PROD"]]] = f[pos["PROD_NOM"]]
    if not agg:
        raise DatosInvalidos(f"el archivo ({n:,} líneas) no trae filas de Recursos Ordinarios del pliego {cfg['pliego']}")
    print(f"líneas leídas: {n:,} · combinaciones RO: {len(agg):,}")
    df = pd.DataFrame([list(k) + v for k, v in agg.items()], columns=["MES_EJE"] + LLAVES + MONTOS)
    return df, nombres


def descargar(cfg):
    """Lee el CSV por partes y agrega en memoria. Devuelve (DataFrame, nombres de UE, fecha de publicación)."""
    import requests
    url = URL.format(anio=cfg["anio"])
    r = requests.get(url, stream=True, timeout=120, headers={"User-Agent": "Mozilla/5.0 (cuadro de control RO)"})
    r.raise_for_status()
    publicado = r.headers.get("Last-Modified")
    df, nombres = leer_filas(r.iter_lines(chunk_size=1 << 20, decode_unicode=False), cfg)
    # descarga incompleta: los bytes recibidos deben coincidir con los anunciados por el servidor
    cl, enc = r.headers.get("Content-Length"), r.headers.get("Content-Encoding")
    if cl and not enc and hasattr(r.raw, "tell") and r.raw.tell() != int(cl):
        raise DatosInvalidos(f"descarga incompleta: {r.raw.tell():,} de {int(cl):,} bytes")
    return df, nombres, publicado


def validar_montos(df, origen):
    """Los importes deben ser números finitos (vale también para el JSON del núcleo)."""
    for c in MONTOS:
        v = pd.to_numeric(df[c], errors="coerce")
        malos = df.loc[v.isna() | ~v.apply(lambda x: math.isfinite(x) if pd.notna(x) else False), c]
        if len(malos):
            raise DatosInvalidos(f"{origen}: {len(malos)} importe(s) inválido(s) en {c}, p. ej. «{malos.iloc[0]}»")
        df[c] = v.astype(float)
    return df


def desde_json(ruta, excluir=()):
    """Los JSON antiguos no filtraban el sector: se quitan las UE de otro sector con el mismo código de pliego."""
    J = json.load(open(ruta, encoding="utf-8"))
    df = pd.DataFrame(J["filas"], columns=J["claves"] + J["montos"])
    df = df[(df.FUENTE == "1") & ~df.SEC_EJEC.isin(list(excluir))].drop(columns="FUENTE")
    PROD_NOM.update(J["nombres"].get("prod", {}))
    df["MES_EJE"] = df.MES_EJE.astype(int)
    validar_montos(df, os.path.basename(ruta))
    if df.empty:
        raise DatosInvalidos(f"{os.path.basename(ruta)} no trae filas de Recursos Ordinarios")
    return df, J["nombres"]["ue"], J["meta"]["publicado"]


def _pascua(y):
    """Domingo de Pascua (calendario gregoriano, método de Meeus/Jones/Butcher)."""
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (2 * e + 2 * i - h - k + 32) % 7
    m = (a + 11 * h + 22 * l_) // 451
    mes = (h + l_ - 7 * m + 114) // 31
    return dt.date(y, mes, (h + l_ - 7 * m + 114) % 31 + 1)


def feriados(y):
    p = _pascua(y)
    return {dt.date(y, m, d) for m, d in FIJOS} | {p - dt.timedelta(days=3), p - dt.timedelta(days=2)}


def dias_habiles(desde, hasta):
    d, n, F = desde, 0, {}
    while d <= hasta:
        if d.year not in F:
            F[d.year] = feriados(d.year)
        if d.weekday() < 5 and d not in F[d.year]:
            n += 1
        d += dt.timedelta(days=1)
    return n


def calendario_corte(corte, anio, mes_base=10):
    """Calendario del ejercicio `anio` visto desde una fecha de corte (hallazgo N01: cambio de año).
    - Un corte posterior al 31/12 del ejercicio (el MEF sigue publicando el archivo del año en enero) se trata como CIERRE:
      los cálculos usan el 31/12, ya no quedan meses ni días hábiles y el saldo no devengado es definitivo.
    - Meses completos para el ritmo: los tres anteriores al corte, solo dentro del ejercicio (nunca meses 0 o negativos).
      Si aún no hay ninguno (enero de un ejercicio nuevo), el ritmo es lo devengado en el mes en curso llevado a mes completo,
      y se rotula como provisional.
    - Mes base del avance: el configurado si ya pasó; si no (p. ej., enero con mes base 10), el mes anterior al corte,
      o el 31/12 del año previo cuando el corte está en enero."""
    fin = dt.date(anio, 12, 31)
    cierre = corte > fin
    c = min(corte, fin)
    if c.year < anio:
        raise DatosInvalidos(f"el corte {corte} es anterior al ejercicio {anio}")
    M, dia = c.month, c.day
    dmes = calendar.monthrange(anio, M)[1]
    completo = dia == dmes
    frac = 0.0 if completo else (dmes - dia) / dmes
    completos = [m for m in ([M - 2, M - 1, M] if completo else [M - 3, M - 2, M - 1]) if m >= 1]
    mb = mes_base if mes_base < M or (mes_base == M and completo) else M - 1
    fin_b = dt.date(anio - 1, 12, 31) if mb == 0 else dt.date(anio, mb, calendar.monthrange(anio, mb)[1])
    return {"c": c, "cierre": cierre, "M": M, "dia": dia, "dmes": dmes, "frac": frac, "completos": completos,
            "parcial": not completos, "mes_base": mb, "fin_base": fin_b, "meses_rest": 0.0 if cierre else frac + (12 - M)}


def tiempo(corte, anio, mes_base=10):
    """Meses que faltan al 31/12, días hábiles y avance esperado (lineal y por mes cerrado) a una fecha de corte."""
    k = calendario_corte(corte, anio, mes_base)
    c, M, mb, fin_b = k["c"], k["M"], k["mes_base"], k["fin_base"]
    ht = dias_habiles(fin_b + dt.timedelta(days=1), dt.date(anio, 12, 31))
    hp = dias_habiles(fin_b + dt.timedelta(days=1), c)
    mc = (M - mb - 1) + (1 if k["dia"] == k["dmes"] else 0)
    return {"meses_rest": k["meses_rest"], "habiles_rest": dias_habiles(c + dt.timedelta(days=1), dt.date(anio, 12, 31)),
            "fin_base": fin_b, "habiles_total": ht, "habiles_pasados": hp, "esp_lin": hp / ht if ht else 0.0,
            "esp_mes": max(0, mc) / (12 - mb)}


def por_proyecto(PDA, prod, t0, t1):
    """Cuadro por unidad × producto/proyecto. prod = cifras del día de la Consulta Amigable (o None: solo Datos Abiertos)."""
    f = (t1["meses_rest"] / t0["meses_rest"]) if t0["meses_rest"] > 0 else 0.0
    base = PDA.set_index(["SEC_EJEC", "PRODUCTO_PROYECTO"])
    filas = []
    claves = [(ue, k) for ue, d in prod.items() for k in d] if prod else list(base.index)
    for ue, k in claves:
        da = base.loc[(ue, k)] if (ue, k) in base.index else None
        if prod:
            m = prod[ue][k]; pim, dev, nom = m["PIM"], m["DEVENGADO"], m.get("nombre", k)
        else:
            pim, dev, nom = float(da.PIM), float(da.DEVENGADO), k
        if abs(pim) < 0.5 and abs(dev) < 0.5:
            continue
        saldo = pim - dev
        adic_da = float(da.adicional) if da is not None else 0.0
        adic = max(0.0, min(saldo, adic_da * f))
        hb = float(da.hasta_base) if da is not None else dev
        tipo = (str(da.tipo) if da is not None else ("2" if len(k) == 7 and k.startswith("2") else "3"))
        w = float(da.w_planilla) if da is not None else (1.0 if tipo == "2" else 0.0)
        dbase = dev - hb
        si = saldo + dbase
        filas.append({"SEC_EJEC": ue, "cod": k, "nombre": nom, "tipo": tipo, "PIM": pim, "DEVENGADO": dev, "saldo": saldo,
                      "adicional": adic, "riesgo": saldo - adic, "dev_desde_base": dbase, "saldo_ini": si,
                      "avance": (dbase / si) if si > 0 else 0.0, "esperado": w * t1["esp_mes"] + (1 - w) * t1["esp_lin"]})
    return pd.DataFrame(filas)


def con_consulta_amigable(g, par, corte_da, cfg):
    """Reemplaza las cifras del día por las de la Consulta Amigable si trae un corte más reciente.
    El ritmo de los meses completos y el detalle por genérica siguen saliendo de Datos Abiertos."""
    try:
        import consulta_amigable as CA
        filas, fecha, prod = CA.por_ejecutora(cfg["anio"], cfg["nivel"], cfg["sector"], cfg["pliego"], "1", productos=True)
    except Exception as e:                                   # sin acceso o cambió la página: se sigue con Datos Abiertos
        print(f"Consulta Amigable no disponible ({e}); se usa Datos Abiertos")
        FUENTES["Consulta Amigable"] = f"no disponible ({str(e)[:160]})"
        return g, par, corte_da, "Datos Abiertos", None
    if not fecha or fecha <= corte_da:
        print(f"Consulta Amigable al {fecha}: no es más reciente que Datos Abiertos ({corte_da})")
        FUENTES["Consulta Amigable"] = f"leída: corte {fecha.strftime('%d/%m/%Y') if fecha else 'sin fecha'}, no más reciente que Datos Abiertos"
        return g, par, corte_da, "Datos Abiertos", None
    falt = sorted(set(g.SEC_EJEC) - set(filas))
    if falt:
        print(f"Consulta Amigable sin las UE {falt}; se usa Datos Abiertos")
        FUENTES["Consulta Amigable"] = f"leída: corte {fecha.strftime('%d/%m/%Y')}, pero sin las unidades {falt}; no se usa"
        return g, par, corte_da, "Datos Abiertos", None
    malos = [f"{u}/{k}" for u, m in filas.items() for k, v in m.items() if not math.isfinite(v)]
    if malos:
        print(f"Consulta Amigable con importes no finitos {malos[:5]}; se usa Datos Abiertos")
        FUENTES["Consulta Amigable"] = f"importes inválidos en {malos[:5]}; no se usa"
        return g, par, corte_da, "Datos Abiertos", None
    FUENTES["Consulta Amigable"] = f"leída: corte {fecha.strftime('%d/%m/%Y')} (se usa para las cifras del día)"
    t0, t1 = tiempo(corte_da, cfg["anio"], cfg.get("mes_base", 10)), tiempo(fecha, cfg["anio"], cfg.get("mes_base", 10))
    g = g.copy()
    dev_da = g.DEVENGADO.copy()
    hasta_base = g.DEVENGADO - g.dev_desde_base
    for c, k in (("PIM", "PIM"), ("CERTIFICADO", "CERTIFICADO"), ("COMPROMETIDO", "COMPROMETIDO_ANUAL"), ("DEVENGADO", "DEVENGADO")):
        g[c] = g.SEC_EJEC.map(lambda x: filas[x][k])
    g["saldo"] = g.PIM - g.DEVENGADO
    g["no_certificado"] = g.PIM - g.CERTIFICADO
    g["dev_mes_corte"] = g.dev_mes_corte + (g.DEVENGADO - dev_da)
    g["dev_desde_base"] = g.DEVENGADO - hasta_base
    f = (t1["meses_rest"] / t0["meses_rest"]) if t0["meses_rest"] > 0 else 0.0
    g["adicional"] = pd.concat([g.adicional * f, g.saldo.clip(lower=0)], axis=1).min(axis=1)
    g["riesgo"] = g.saldo - g.adicional
    g["esperado"] = g.w_planilla * t1["esp_mes"] + (1 - g.w_planilla) * t1["esp_lin"]
    # Riesgo por proyecto con cifras del día: cada proyecto con su propio tope. Si los proyectos suman el total de la unidad,
    # el riesgo de la unidad es la suma de sus proyectos.
    PR = por_proyecto(calcular.PDA, prod, t0, t1) if prod else None
    if PR is not None and len(PR):
        sp = PR.groupby("SEC_EJEC")[["PIM", "DEVENGADO", "riesgo", "adicional"]].sum()
        # el agregado de proyectos se adopta solo si cuadra con la unidad en PIM y en devengado (no basta el PIM)
        dp = sp.PIM.reindex(g.SEC_EJEC).values - g.PIM.values
        dd = sp.DEVENGADO.reindex(g.SEC_EJEC).values - g.DEVENGADO.values
        cuadra = pd.Series((abs(dp) < 1.0) & (abs(dd) < 1.0), index=g.index)
        g.loc[cuadra, "riesgo"] = g.loc[cuadra, "SEC_EJEC"].map(sp.riesgo)
        g.loc[cuadra, "adicional"] = g.loc[cuadra, "SEC_EJEC"].map(sp.adicional)
        if (~cuadra).any():
            print(f"proyectos no cuadran con el total en {list(g.loc[~cuadra, 'SEC_EJEC'])}: se mantiene el riesgo por unidad")
    par = dict(par, meses_restantes=round(t1["meses_rest"], 4), dias_habiles=t1["habiles_rest"], habiles_desde_base=t1["habiles_pasados"],
               avance_esperado=round(t1["esp_lin"], 4), corte_detalle=corte_da.isoformat(),
               publicado_lima=f"Consulta Amigable, actualizada al {fecha.strftime('%d/%m/%Y')}")
    print(f"Consulta Amigable al {fecha}: devengado RO S/ {g.DEVENGADO.sum() / 1e6:,.1f} M (Datos Abiertos al {corte_da}: S/ {dev_da.sum() / 1e6:,.1f} M)")
    return g, par, fecha, "Consulta Amigable", PR


def mensual_(d):
    """Partidas que se devengan una vez al mes: planilla (2.1), pensiones (2.2) y proyectos de inversión (valorizaciones).
    Para ellas la referencia de avance cuenta meses cerrados, no días hábiles."""
    return d.GENERICA.astype(str).isin(["1", "2"]) | (d.TIPO_ACT_PROY.astype(str) == "2")


def perfil_de(eid):
    """Perfil histórico de la entidad (devengado mensual RO por UE × genérica de los años cerrados). Es opcional: sin él no hay escenario según patrones."""
    return os.path.join(AQUI, f"perfil_historico_{eid}.json")


def tasas_hist(corte, perfil):
    """Escenario según patrones de años anteriores: para cada unidad y genérica, parte del saldo pendiente a la misma fecha
    que se devengó hasta el 31/12 en cada año cerrado (mismo método del informe de saldos)."""
    if not os.path.isfile(perfil):
        return None
    J = json.load(open(perfil, encoding="utf-8"))
    M, dia = corte.month, corte.day
    dmes = calendar.monthrange(corte.year, M)[1]
    frac = 0.0 if dia == dmes else (dmes - dia) / dmes
    T = {}
    for y, v in J["anios"].items():
        t = {}
        for k, a in v["combinaciones"].items():
            pim, m = a[0], a[1:]
            dev_ant = sum(m[:M - 1]) + m[M - 1] * (1 - frac)
            adic = m[M - 1] * frac + sum(m[M:])
            pend = pim - dev_ant
            if pend > J.get("umbral_pendiente", 10000):
                t[tuple(k.split("|"))] = min(max(adic / pend, 0.0), 1.0)
        T[y] = pd.Series(t, dtype=float)
    T = pd.DataFrame(T)
    T.index.names = ["SEC_EJEC", "GENERICA"]
    return pd.DataFrame({"central": T.mean(axis=1), "min": T.min(axis=1), "max": T.max(axis=1)})


def riesgo_hist_ue(UG, corte, perfil):
    """Fracción del saldo de cada unidad que quedaría sin devengar según los patrones de 2023–2025 (central, mínimo y máximo).
    Las combinaciones sin historia comparable conservan el escenario según ejecución reciente, como en el informe."""
    T = tasas_hist(corte, perfil)
    if T is None:
        return None
    G = UG.join(T, how="left")
    sal = G.saldo.clip(lower=0)
    out = {}
    for k in ("central", "min", "max"):
        adic = (sal * G[k]).where(G[k].notna(), G.adicional)
        out[k] = (G.saldo - adic).groupby(level=0).sum()
    R = pd.DataFrame(out)
    R["saldo"] = G.saldo.groupby(level=0).sum()
    # la tasa más alta da el riesgo más bajo: se ordena el rango
    R["bajo"], R["alto"] = R[["min", "max"]].min(axis=1), R[["min", "max"]].max(axis=1)
    return R


def calcular(df, publicado, anio, mes_base=10):
    """El archivo se publica en la mañana (hora de Lima) con datos hasta el día anterior."""
    lima = parsedate_to_datetime(publicado) - dt.timedelta(hours=5)
    corte = lima.date() - dt.timedelta(days=1)
    k = calendario_corte(corte, anio, mes_base)           # todos los cálculos de tiempo usan la fecha efectiva (≤ 31/12)
    c, M, dia, dmes, frac, completos, meses_rest = k["c"], k["M"], k["dia"], k["dmes"], k["frac"], k["completos"], k["meses_rest"]
    mes_base = k["mes_base"]
    tot = df.groupby(LLAVES)[MONTOS].sum()
    mens = df[df.MES_EJE > 0].pivot_table(index=LLAVES, columns="MES_EJE", values="DEVENGADO", aggfunc="sum") \
        .reindex(columns=range(1, 13)).fillna(0.0)
    U = tot.join(mens, how="left").fillna(0.0).reset_index()
    U["saldo"] = U.PIM - U.DEVENGADO
    # ritmo mensual de referencia: promedio de los meses completos; sin ninguno, el mes en curso llevado a mes completo
    U["_rit"] = U[completos].mean(axis=1) if completos else U[M] * dmes / max(dia, 1)
    adic = (U["_rit"] * meses_rest).clip(lower=0)
    U["adicional"] = pd.concat([adic, U.saldo.clip(lower=0)], axis=1).min(axis=1)
    U["riesgo"] = U.saldo - U.adicional
    g = U.groupby("SEC_EJEC").agg(PIM=("PIM", "sum"), CERTIFICADO=("CERTIFICADO", "sum"), COMPROMETIDO=("COMPROMETIDO_ANUAL", "sum"),
                                  DEVENGADO=("DEVENGADO", "sum"), saldo=("saldo", "sum"), adicional=("adicional", "sum"), riesgo=("riesgo", "sum"))
    g["no_certificado"] = g.PIM - g.CERTIFICADO
    g["dev_mes_corte"] = U.groupby("SEC_EJEC")[M].sum()
    # Control del saldo: lo devengado desde el cierre del mes base (p. ej., 31/10) sobre el saldo que había a esa fecha
    _post = [m for m in range(mes_base + 1, M + 1)]
    U["dev_desde_base"] = U[_post].sum(axis=1) if _post else 0.0
    g["dev_desde_base"] = U.groupby("SEC_EJEC").dev_desde_base.sum()
    # Avance esperado: la planilla y las pensiones (2.1 y 2.2) se devengan una vez al mes, así que su meta avanza por mes cerrado;
    # el resto, por días hábiles transcurridos. La meta de cada unidad pondera ambas por su saldo al cierre del mes base.
    fin_b = k["fin_base"]
    _ht = dias_habiles(fin_b + dt.timedelta(days=1), dt.date(anio, 12, 31))
    esp_lin = dias_habiles(fin_b + dt.timedelta(days=1), c) / _ht if _ht else 0.0
    _mc = (M - mes_base - 1) + (1 if dia == dmes else 0)          # meses completos cerrados después del mes base
    esp_mes = max(0, _mc) / (12 - mes_base)
    U["saldo_ini"] = U.saldo + U.dev_desde_base
    U["esp_w"] = U.saldo_ini.clip(lower=0) * mensual_(U).map({True: esp_mes, False: esp_lin})
    _w = U.groupby("SEC_EJEC")[["saldo_ini", "esp_w"]].sum()
    g["esperado"] = (_w.esp_w / U.assign(si=U.saldo_ini.clip(lower=0)).groupby("SEC_EJEC").si.sum()).fillna(0.0)
    _si = U.assign(si=U.saldo_ini.clip(lower=0))
    g["w_planilla"] = (_si[mensual_(_si)].groupby("SEC_EJEC").si.sum() / _si.groupby("SEC_EJEC").si.sum()).reindex(g.index).fillna(0.0)
    g["ritmo_mensual"] = U.groupby("SEC_EJEC")["_rit"].sum()
    gen = U.groupby(["SEC_EJEC", "GENERICA"])[["PIM", "DEVENGADO", "saldo", "riesgo"]].sum().reset_index()
    mensual = df[df.MES_EJE > 0].groupby("MES_EJE").DEVENGADO.sum().reindex(range(1, 13)).fillna(0.0)
    hab = dias_habiles(c + dt.timedelta(days=1), dt.date(anio, 12, 31))
    fin_base = k["fin_base"]
    hab_tot = dias_habiles(fin_base + dt.timedelta(days=1), dt.date(anio, 12, 31))
    hab_pas = dias_habiles(fin_base + dt.timedelta(days=1), c)
    _U = U.assign(si=U.saldo_ini.clip(lower=0), pl=mensual_(U),
                  rit=U["_rit"], hb=U.DEVENGADO - U.dev_desde_base)
    _U["si_pl"] = _U.si.where(_U.pl, 0.0)
    PDA = _U.groupby(["SEC_EJEC", "PRODUCTO_PROYECTO"]).agg(tipo=("TIPO_ACT_PROY", "first"), PIM=("PIM", "sum"), DEVENGADO=("DEVENGADO", "sum"),
                                                          adicional=("adicional", "sum"), ritmo=("rit", "sum"), hasta_base=("hb", "sum"),
                                                          si=("si", "sum"), si_pl=("si_pl", "sum")).reset_index()
    PDA["w_planilla"] = (PDA.si_pl / PDA.si).where(PDA.si > 0, (PDA.tipo.astype(str) == "2").astype(float))
    calcular.PDA = PDA
    calcular.UG = U.groupby(["SEC_EJEC", "GENERICA"])[["saldo", "adicional"]].sum()
    return corte, g[g.PIM > 0].reset_index(), gen, mensual, {"meses_ritmo": completos, "meses_restantes": round(meses_rest, 4),
                                                              "cierre": k["cierre"], "ritmo_parcial": [M, dia] if k["parcial"] else None,
                                                              "fecha_efectiva": c.isoformat(),
                                                              "dias_habiles": hab, "publicado_lima": lima.strftime("%d/%m/%Y %H:%M"),
                                                              "fecha_base": fin_base.isoformat(), "habiles_desde_base": hab_pas,
                                                              "habiles_total": hab_tot, "avance_esperado": round(hab_pas / hab_tot, 4) if hab_tot else 0}


def guardar_proyectos(PR, fc, out):
    """proyectos.json: por unidad, sus productos y proyectos en RO ordenados por riesgo; historial_proyectos.csv: una fila por día."""
    os.makedirs(out, exist_ok=True)
    fh = os.path.join(out, "historial_proyectos.csv")
    cols = ["fecha_corte", "SEC_EJEC", "cod", "DEVENGADO"]          # basta el devengado: el del día sale de la resta
    PR = PR.copy()
    fj = os.path.join(out, "proyectos.json")
    previos = {}                                    # nombre: Consulta Amigable del día; si no, el de la última consulta; si no, el de Datos Abiertos
    if os.path.isfile(fj):
        try:
            for lst in json.load(open(fj, encoding="utf-8"))["ues"].values():
                previos.update({x["c"]: x["n"] for x in lst if x["n"] != x["c"]})
        except (ValueError, KeyError):
            pass
    PR["nombre"] = [nombre(n) if n != c else (previos.get(c) or (nombre(PROD_NOM[c]) if PROD_NOM.get(c) else c)) for c, n in zip(PR.cod, PR.nombre)]
    H = pd.read_csv(fh, dtype={"SEC_EJEC": str, "cod": str}) if os.path.isfile(fh) else pd.DataFrame(columns=cols)
    H = H[H.fecha_corte != fc]
    prev = anterior_a(H.fecha_corte.unique(), fc)               # el mayor corte estrictamente anterior al que se publica
    ant = H[H.fecha_corte == prev].set_index(["SEC_EJEC", "cod"]).DEVENGADO if prev else None
    H = pd.concat([H, PR.assign(fecha_corte=fc)[cols]], ignore_index=True)
    fechas = sorted(H.fecha_corte.unique())[-20:]                      # se guardan los últimos 20 cortes
    H[H.fecha_corte.isin(fechas)].round(2).to_csv(fh, index=False, quoting=csv.QUOTE_NONNUMERIC)
    J = {"corte": fc, "corte_anterior": prev, "ues": {}}
    for ue, d in PR.sort_values("riesgo", ascending=False).groupby("SEC_EJEC", sort=False):
        J["ues"][ue] = [{"c": r.cod, "n": r.nombre, "t": "P" if str(r.tipo) == "2" else "A", "pim": round(r.PIM, 2), "dev": round(r.DEVENGADO, 2),
                         "s": round(r.saldo, 2), "si": round(r.saldo_ini, 2), "av": round(r.avance, 4), "esp": round(r.esperado, 4),
                         "ri": round(r.riesgo, 2),
                         "dd": (round(r.DEVENGADO - float(ant.get((ue, r.cod), r.DEVENGADO)), 2) if ant is not None else None)}
                        for r in d.itertuples() if abs(r.PIM) >= 0.5 or abs(r.DEVENGADO) >= 0.5]
    json.dump(J, open(fj, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"proyectos: {sum(len(v) for v in J['ues'].values())} en {len(J['ues'])} unidades")


def controles(g, ues, tot, P):
    """Comprobaciones sobre los valores originales (antes de redondear). Se publican junto con los datos."""
    c = []
    ok = lambda regla, cond, det="": c.append({"regla": regla, "ok": bool(cond), "detalle": det})
    ok("Saldo por devengar = PIM − devengado", ((g.saldo - (g.PIM - g.DEVENGADO)).abs() < 0.01).all())
    ok("Sin certificar = PIM − certificado", ((g.no_certificado - (g.PIM - g.CERTIFICADO)).abs() < 0.01).all())
    ok("La suma de las unidades coincide con el total del pliego", abs(g.saldo.sum() - tot["saldo"]) < 1 and abs(g.DEVENGADO.sum() - tot["DEVENGADO"]) < 1)
    if P is not None:
        neg = [u["sec"] for u in ues if u.get("var") is not None and u["var"] < -TOL_VAR]
        ok("Ajustes netos negativos clasificados como ajuste, no como «sin variación»", all(u.get("estado") == "ajuste_negativo" for u in ues if u["sec"] in neg),
           ("Unidades con ajuste neto negativo: " + ", ".join(neg)) if neg else "sin ajustes netos negativos")
        dP = g.PIM.sum() - P.PIM.sum()
        ok("Variación del saldo = variación del PIM − variación del devengado",
           abs((g.saldo.sum() - P.saldo.sum()) - (dP - (g.DEVENGADO.sum() - P.DEVENGADO.sum()))) < 1, f"Variación del PIM entre cortes: S/ {dP:,.2f}")
    for x in c:
        print(("OK   " if x["ok"] else "REVISAR ") + x["regla"] + (f" · {x['detalle']}" if x["detalle"] else ""))
    return c


def procedencia(fuente, corte, corte_da, publicado, PR_ca):
    """Fuente real de cada componente del corte que se publica (se muestra en la cabecera, las fichas y el método)."""
    f = lambda d: d.strftime("%d/%m/%Y")
    da = f"Datos Abiertos al {f(corte_da)}" + (f" (archivo publicado {publicado})" if publicado else "")
    dia = f"Consulta Amigable al {f(corte)}" if fuente == "Consulta Amigable" else f"Datos Abiertos al {f(corte_da)}"
    return [
        {"componente": "Cifras del día por unidad: PIM, certificado, compromiso y devengado", "fuente": dia},
        {"componente": "Productos, actividades y proyectos de cada unidad", "fuente": dia if PR_ca else f"Datos Abiertos al {f(corte_da)}"},
        {"componente": "Ritmo de los tres últimos meses completos, detalle por meta y genérica, y devengado mensual", "fuente": da},
        {"componente": "Escenario según patrones de 2023–2025", "fuente": "Datos Abiertos, años cerrados 2023, 2024 y 2025 (devengado mensual por unidad y genérica)"},
        {"componente": "Días hábiles al 31/12", "fuente": "Calendario de lunes a viernes sin los feriados nacionales"},
    ]


def fdm(iso):
    return "/".join(reversed(iso.split("-"))) if iso else "—"


CAMPOS_REV = [("PIM", "PIM"), ("DEVENGADO", "Devengado"), ("saldo", "Saldo por devengar"), ("no_certificado", "Sin certificar"),
              ("riesgo", "Riesgo estimado (ejecución reciente)"), ("riesgo_hist", "Riesgo según patrones 2023–2025")]


def revisar(out, J):
    """Si se vuelve a publicar un corte ya publicado con cifras distintas, lo anota en revisiones.json con fecha, motivo y efecto.
    También guarda los valores con que se publicó cada corte por primera vez. No borra ni reescribe revisiones anteriores."""
    fr, fd = os.path.join(out, "revisiones.json"), os.path.join(out, "datos.json")
    R = json.load(open(fr, encoding="utf-8")) if os.path.isfile(fr) else {"revisiones": [], "primera_publicacion": {}, "metodos": []}
    A = json.load(open(fd, encoding="utf-8")) if os.path.isfile(fd) else None
    par, fc, T = J["parametros"], J["corte"], J["total"]
    ahora = par["consulta_ok"]
    R.setdefault("primera_publicacion", {})
    if fc not in R["primera_publicacion"]:
        R["primera_publicacion"][fc] = {"publicado": ahora, "fuente": par["fuente"], "corte_detalle": par.get("corte_detalle"),
                                        **{k: T.get(k) for k, _ in CAMPOS_REV}}
    if A and A.get("corte") == fc:
        pa, TA = A.get("parametros", {}), A.get("total", {})
        motivos = []
        if pa.get("metodo") != par.get("metodo"):
            motivos.append(f"cambio de método (versión {pa.get('metodo') or 'anterior'} → {par.get('metodo')}; ver la lista de métodos)")
        if pa.get("fuente") != par.get("fuente"):
            motivos.append(f"las cifras del día pasaron de {pa.get('fuente')} a {par.get('fuente')}")
        if pa.get("corte_detalle") != par.get("corte_detalle"):
            motivos.append(f"el ritmo y el detalle de Datos Abiertos pasaron del {fdm(pa.get('corte_detalle'))} al {fdm(par.get('corte_detalle'))}")
        if not motivos:
            motivos.append("misma fuente y mismo método: el MEF modificó cifras ya publicadas (causa por verificar)")
        for k, etq in CAMPOS_REV:
            a, d = TA.get(k), T.get(k)
            if (a is None) != (d is None) or (a is not None and abs(d - a) >= 1):
                R["revisiones"].append({"registrado": ahora, "corte": fc, "indicador": etq, "antes": a, "despues": d,
                                        "motivo": (lambda t: t[:1].upper() + t[1:])("; ".join(motivos)) + ".",
                                        "tipo": "método" if pa.get("metodo") != par.get("metodo") else ("fuente" if len(motivos) and "MEF modificó" not in motivos[0] else "dato")})
    R["metodo_vigente"] = par.get("metodo")
    json.dump(R, open(fr, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    J["revisiones_corte"] = [x for x in R["revisiones"] if x["corte"] == fc]


# ---------------------------------------------------------------------------------------------------------------------
# Actividad entre cortes (hallazgos M01 y M02 de la auditoría del 13/11/2026)
# Lo único que se observa es el devengado ACUMULADO en cada corte. Su diferencia es una variación NETA entre dos cortes
# (que pueden estar separados por más de un día hábil): un aumento no enumera las operaciones del periodo, una cifra
# negativa es un ajuste neto (anulaciones o rebajas mayores que lo nuevo) y no prueba inactividad.
# ---------------------------------------------------------------------------------------------------------------------
def clasificar_variacion(v, tol=TOL_VAR):
    """Estado excluyente de la variación neta del devengado entre dos cortes."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "sin_comparacion"
    if v > tol:
        return "aumento"
    if v < -tol:
        return "ajuste_negativo"
    return "sin_variacion"


def anterior_a(fechas, fc):
    """El mayor corte estrictamente anterior a fc (o None). No depende del orden ni de cortes posteriores ya guardados."""
    ant = [f for f in fechas if isinstance(f, str) and f < fc]
    return max(ant) if ant else None


def racha_sin_aumento(valores, fechas, tol=TOL_VAR):
    """Cortes consecutivos, contados desde el último, sin aumento neto observado del devengado acumulado.
    valores y fechas en orden cronológico (None/NaN = la unidad no figura en ese corte).
    Devuelve None si no hay al menos dos cortes, o {"n", "desde", "minimo", "cobertura", "ajustes"}:
      n         cortes sin aumento neto (0 si el último corte trae aumento); solo cuentan los cortes con al menos un día hábil
                desde el corte anterior (no los de sábado, domingo o feriado);
      desde     corte de referencia: el último que mostró aumento neto (o el primero disponible);
      minimo    True si la racha llega al inicio del historial o a un hueco: entonces n es un mínimo («n o más»);
      cobertura "completa" o "interrumpida" (la unidad falta en algún corte: no se infiere nada antes del hueco);
      ajustes   cuántos de esos cortes fueron ajustes netos negativos (se informan, no se cuentan como inactividad aparte)."""
    v = [None if (x is None or (isinstance(x, float) and math.isnan(x))) else float(x) for x in valores]
    if len(v) < 2 or v[-1] is None:
        return None
    n = aj = 0
    for i in range(len(v) - 1, 0, -1):
        if v[i - 1] is None or v[i] is None:
            return {"n": n, "desde": fechas[i], "minimo": True, "cobertura": "interrumpida", "ajustes": aj} if n or i < len(v) - 1 else None
        d = v[i] - v[i - 1]
        if d > tol:
            return {"n": n, "desde": fechas[i], "minimo": False, "cobertura": "completa", "ajustes": aj}
        # Revisión externa (19/11/2026), punto 3: un corte sin días hábiles respecto del anterior (sábado, domingo o feriado) no cuenta
        # como «corte sin aumento»: ahí no se espera devengado. Si ese día sí hubo aumento, la racha se reinicia igual (línea de arriba).
        if dias_habiles(dt.date.fromisoformat(fechas[i - 1]) + dt.timedelta(days=1), dt.date.fromisoformat(fechas[i])) > 0:
            n += 1
            aj += d < -tol
    return {"n": n, "desde": fechas[0], "minimo": True, "cobertura": "completa", "ajustes": aj}


def aplicar_actividad(ues, H, fc, tol=TOL_VAR):
    """Agrega a cada unidad su variación entre cortes, su estado y su racha de cortes sin aumento neto."""
    fechas = sorted(f for f in H.fecha_corte.unique() if f <= fc)
    DV = H[H.fecha_corte.isin(fechas)].pivot_table(index="fecha_corte", columns="SEC_EJEC", values="DEVENGADO", aggfunc="sum").reindex(fechas)
    prev = anterior_a(fechas, fc)
    hab = dias_habiles(dt.date.fromisoformat(prev) + dt.timedelta(days=1), dt.date.fromisoformat(fc)) if prev else None
    for u in ues:
        for k in ("ult", "dias_sin", "dias_sin_tipo"):           # campos del método anterior (sustituidos)
            u.pop(k, None)
        var = round(u["dev"] - u["dev_ant"], 2) if u.get("dev_ant") is not None else None
        u["var"] = var
        u["estado"] = clasificar_variacion(var, tol)
        u["sin_aumento"] = racha_sin_aumento(DV[u["sec"]].tolist() if u["sec"] in DV else [], fechas, tol)
    return {"desde": prev, "hasta": fc, "habiles": hab, "tolerancia": tol}


def verificar_cronologia(candidato, publicado, fechas_hist):
    """Barrera contra el retroceso del corte (M05). Se llama ANTES de escribir cualquier archivo.
    Igual al último publicado: revisión de cifras (permitida). Anterior: CorteRetrocede (se conserva lo publicado)."""
    ultimo = max([f for f in list(fechas_hist) + [publicado] if isinstance(f, str)], default=None)
    if ultimo and candidato < ultimo:
        raise CorteRetrocede(f"la fuente disponible trae el corte {fdm(candidato)}, anterior al ya publicado {fdm(ultimo)}")
    return ultimo


def guardar_estado(out, **kv):
    """estado.json: estado operativo de la última corrida (separado de las cifras). La página lo muestra tal cual."""
    ruta = os.path.join(out, "estado.json")
    E = json.load(open(ruta, encoding="utf-8")) if os.path.isfile(ruta) else {}
    ahora = dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).strftime("%Y-%m-%dT%H:%M")
    E.update({"ultimo_intento": ahora, "fuentes": dict(FUENTES), "metodo": METODO}, **kv)
    if kv.get("resultado") in ("nuevo_corte", "cifras_revisadas", "sin_cambios"):
        E["ultimo_exito"] = ahora
    E["textos"] = {"nuevo_corte": "Nuevo corte", "cifras_revisadas": "Cifras revisadas", "sin_cambios": "Consulta sin cambios",
                   "corte_retenido": "Se conservó el corte publicado", "recalculo": "Recalculado con el método nuevo"}
    json.dump(E, open(ruta, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return E


def ultimos_publicados(out):
    """Corte publicado en datos.json y fechas de los historiales (para la barrera cronológica)."""
    pub, fechas = None, []
    try:
        pub = json.load(open(os.path.join(out, "datos.json"), encoding="utf-8")).get("corte")
    except (OSError, ValueError):
        pass
    for f in ("historial.csv", "historial_proyectos.csv"):
        ruta = os.path.join(out, f)
        if os.path.isfile(ruta):
            fechas += list(pd.read_csv(ruta, usecols=["fecha_corte"], dtype=str).fecha_corte.unique())
    return pub, fechas



MESES_TXT = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "setiembre", "octubre", "noviembre", "diciembre"]


def meses_txt(meses):
    n = [MESES_TXT[m - 1] for m in meses if 1 <= m <= 12]
    return (", ".join(n[:-1]) + " y " + n[-1]) if len(n) > 1 else "".join(n)


def ritmo_txt(par):
    """Texto del supuesto de la proyección («si continúa el promedio de …»)."""
    if par.get("cierre"):
        return ""
    if par.get("ritmo_parcial"):
        m, d = par["ritmo_parcial"]
        return f"lo devengado en {MESES_TXT[m - 1]} hasta el día {d} (provisional: aún no hay meses completos)"
    return meses_txt(par.get("meses_ritmo", []))


def recalcular_actividad(eid):
    """Aplica el método vigente de actividad (estados y racha) al último corte ya publicado, sin consultar al MEF.
    Sirve al cambiar de método: las cifras presupuestales (PIM, devengado, saldo, riesgo) no se tocan."""
    out = os.path.join(RAIZ, eid, "ro")
    fd = os.path.join(out, "datos.json")
    J = json.load(open(fd, encoding="utf-8"))
    H = pd.read_csv(os.path.join(out, "historial.csv"), dtype={"SEC_EJEC": str})
    fc, par = J["corte"], J["parametros"]
    J["comparacion"] = aplicar_actividad(J["ues"], H, fc)
    S = J.get("semana") or {}
    if S:
        S["var"], S["var_dias"] = S.pop("ult", S.get("var")), S.pop("ult_dias", S.get("var_dias"))
        S["cortes"] = [c if len(c) > 3 else c + [anterior_a(J["fechas"], c[0])] for c in S.get("cortes", [])]
    par["metodo"], par["tolerancia_variacion"] = METODO, TOL_VAR
    par["meses_ritmo_txt"] = ritmo_txt(par)
    for c in J.get("controles", []):
        if c["regla"].startswith("Devengado entre cortes sin ajustes negativos"):
            neg = [u["sec"] for u in J["ues"] if u["estado"] == "ajuste_negativo"]
            c.update(regla="Ajustes netos negativos clasificados como ajuste, no como «sin variación»", ok=True,
                     detalle=("Unidades con ajuste neto negativo: " + ", ".join(neg)) if neg else "sin ajustes netos negativos")
    revisar(out, J)
    json.dump(J, open(fd, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    shutil.copyfile(os.path.join(AQUI, "plantilla_control_ro.html"), os.path.join(out, "index.html"))
    FUENTES.update({"Consulta Amigable": "no consultada (recálculo local)", "Datos Abiertos": "no consultado (recálculo local)"})
    guardar_estado(out, resultado="recalculo", detalle=f"Corte {fdm(fc)} recalculado con el método {METODO} (estados de variación y racha), "
                   "sin consultar al MEF. Las cifras presupuestales no cambiaron.", corte_publicado=fc, corte_candidato=fc)
    print(f"{eid}: actividad recalculada al {fdm(fc)} · " + ", ".join(f"{k}: {sum(u['estado'] == k for u in J['ues'])}" for k in ESTADOS))



def main():
    eid = sys.argv[1]
    cfg = json.load(open(os.path.join(AQUI, "pliegos.json"), encoding="utf-8"))[eid]
    out = os.path.join(RAIZ, eid, "ro")
    os.makedirs(out, exist_ok=True)
    previo = {}
    try:
        previo = json.load(open(os.path.join(out, "datos.json"), encoding="utf-8"))
    except (OSError, ValueError):
        pass
    df, nombres, publicado = desde_json(sys.argv[3], cfg.get("excluir_json", [])) if "--json" in sys.argv else descargar(cfg)
    corte, g, gen, mensual, par = calcular(df, publicado, cfg["anio"], cfg.get("mes_base", 10))
    par["corte_detalle"] = corte.isoformat()
    FUENTES["Datos Abiertos"] = f"leído: archivo publicado {publicado}; corte {corte.strftime('%d/%m/%Y')} (día anterior a la publicación)"
    fuente, PR = "Datos Abiertos", None
    corte_da = corte
    if "--sin-ca" not in sys.argv:
        g, par, corte, fuente, PR = con_consulta_amigable(g, par, corte, cfg)
    else:
        FUENTES["Consulta Amigable"] = "no consultada (--sin-ca)"
    fc = corte.isoformat()
    # Barrera cronológica (M05): antes de escribir NADA. Si la única fuente disponible trae un corte anterior al publicado
    # (p. ej., falla la Consulta Amigable y Datos Abiertos va un día atrás), se conserva lo publicado y se registra el motivo.
    pub, fechas_pub = ultimos_publicados(out)
    try:
        verificar_cronologia(fc, pub, fechas_pub)
    except CorteRetrocede as e:
        guardar_estado(out, resultado="corte_retenido", detalle=f"No se publicó: {e}. Se mantiene el corte {fdm(pub)}.",
                       corte_publicado=pub, corte_candidato=fc)
        print(f"::warning::{e}; se conserva el corte publicado")
        sys.exit(3)
    if PR is None:                       # sin Consulta Amigable: los proyectos salen de Datos Abiertos al mismo corte
        t = tiempo(corte, cfg["anio"], cfg.get("mes_base", 10))
        PR = por_proyecto(calcular.PDA, None, t, t)
    par["fuente"] = fuente
    par["consulta_ok"] = dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).strftime("%Y-%m-%dT%H:%M")
    par["calendario"] = CALENDARIO
    par["metodo"] = METODO
    par["da_publicado"] = publicado
    par["procedencia"] = procedencia(fuente, corte, corte_da, publicado, PR_ca=(fuente == "Consulta Amigable"))
    # escenario según patrones de 2023–2025, aplicado a la fecha de corte que se publica
    RH = riesgo_hist_ue(calcular.UG, calendario_corte(corte, cfg["anio"], cfg.get("mes_base", 10))["c"], perfil_de(eid))
    if RH is not None:
        for k, col in (("central", "riesgo_hist"), ("bajo", "rh_bajo"), ("alto", "rh_alto")):
            fr = (RH[k] / RH.saldo).where(RH.saldo.abs() > 0.5)
            g[col] = (g.SEC_EJEC.map(fr) * g.saldo).fillna(g.riesgo)
        par["hist_anios"] = "2023–2025"
    par["tolerancia_variacion"] = TOL_VAR
    par["meses_ritmo_txt"] = ritmo_txt(par)
    guardar_proyectos(PR, fc, os.path.join(RAIZ, eid, "ro"))
    g.insert(0, "fecha_corte", fc)
    g["UE"] = g.SEC_EJEC.map(lambda x: nombre(nombres.get(x, x)))

    # historial: una fila por corte y UE
    fh = os.path.join(out, "historial.csv")
    H = pd.read_csv(fh, dtype={"SEC_EJEC": str}) if os.path.isfile(fh) else pd.DataFrame()
    g["esp_monto"] = g.esperado * (g.saldo + g.dev_desde_base).clip(lower=0)
    cols = ["fecha_corte", "SEC_EJEC", "UE", "PIM", "CERTIFICADO", "COMPROMETIDO", "DEVENGADO", "saldo", "no_certificado", "adicional", "riesgo", "dev_desde_base", "esp_monto"]
    cols += [c for c in ("riesgo_hist", "rh_bajo", "rh_alto") if c in g.columns]
    g["fuente"] = fuente
    cols.append("fuente")
    if len(H):
        H = H[H.fecha_corte != fc]
    if prev_ := (anterior_a(H.fecha_corte.unique(), fc) if len(H) else None):
        par["habiles_entre"] = dias_habiles(dt.date.fromisoformat(prev_) + dt.timedelta(days=1), corte)
    H = pd.concat([H, g[cols]], ignore_index=True).sort_values(["fecha_corte", "riesgo"], ascending=[True, False])
    H.round(2).to_csv(fh, index=False, quoting=csv.QUOTE_NONNUMERIC)

    fechas = sorted(H.fecha_corte.unique())
    prev = anterior_a(fechas, fc)                  # el mayor corte estrictamente anterior (nunca el propio candidato)
    P = H[H.fecha_corte == prev].set_index("SEC_EJEC") if prev else None
    sig = cfg.get("siglas_ue", {})
    ues = []
    for r in g.sort_values("riesgo", ascending=False).itertuples():
        d = {"sec": r.SEC_EJEC, "ue": nombre(nombres.get(r.SEC_EJEC) or r.SEC_EJEC), "sigla": sig.get(r.SEC_EJEC, ""),
             "pim": r.PIM, "cert": r.CERTIFICADO, "comp": r.COMPROMETIDO, "dev": r.DEVENGADO, "saldo": r.saldo,
             "no_cert": r.no_certificado, "adicional": r.adicional, "riesgo": r.riesgo, "dev_mes": r.dev_mes_corte, "ritmo": r.ritmo_mensual,
             "dev_base": r.dev_desde_base, "saldo_ini": r.saldo + r.dev_desde_base,
             "avance": (r.dev_desde_base / (r.saldo + r.dev_desde_base)) if (r.saldo + r.dev_desde_base) > 0 else 0.0,
             "esperado": r.esperado, "w_pl": round(float(getattr(r, "w_planilla", 0.0)), 4),
             "riesgo_hist": getattr(r, "riesgo_hist", None), "rh_bajo": getattr(r, "rh_bajo", None), "rh_alto": getattr(r, "rh_alto", None),
             "gen": {x.GENERICA: [x.PIM, x.DEVENGADO, x.saldo, x.riesgo] for x in gen[gen.SEC_EJEC == r.SEC_EJEC].itertuples() if abs(x.PIM) > 0.5}}
        if P is not None and r.SEC_EJEC in P.index:
            d["dev_ant"], d["riesgo_ant"] = float(P.loc[r.SEC_EJEC, "DEVENGADO"]), float(P.loc[r.SEC_EJEC, "riesgo"])
            d["pim_ant"], d["saldo_ant"] = float(P.loc[r.SEC_EJEC, "PIM"]), float(P.loc[r.SEC_EJEC, "saldo"])
            d["cert_ant"] = float(P.loc[r.SEC_EJEC, "CERTIFICADO"])
        ues.append({k: (round(v, 4 if k in ("avance", "esperado") else 2) if isinstance(v, float) else v) for k, v in d.items()})
    for c_ in ("dev_desde_base", "esp_monto", "riesgo_hist", "rh_bajo", "rh_alto"):
        if c_ not in H.columns:
            H[c_] = float("nan")
    serie = H.groupby("fecha_corte")[["PIM", "DEVENGADO", "saldo", "riesgo", "no_certificado", "dev_desde_base", "esp_monto", "riesgo_hist", "rh_bajo", "rh_alto"]].sum(min_count=1).round(2)
    serie["avance"] = (serie.dev_desde_base / (serie.saldo + serie.dev_desde_base)).round(4)
    serie["esperado"] = (serie.esp_monto / (serie.saldo + serie.dev_desde_base)).round(4)
    # ---------- ritmo semanal: lo necesario para devengar todo el saldo al 31/12 frente a lo que realmente se devenga ----------
    fds = [dt.date.fromisoformat(f) for f in fechas]
    hab_hasta = lambda f: dias_habiles(f + dt.timedelta(days=1), corte)       # días hábiles desde un corte anterior hasta el actual
    hab_rest = par["dias_habiles"]
    DV = H.pivot_table(index="fecha_corte", columns="SEC_EJEC", values="DEVENGADO", aggfunc="sum").reindex(fechas)
    # ventana del ritmo real: el corte más reciente que esté a 5 días hábiles o más; si aún no hay, lo devengado desde el cierre del último mes
    k_ = next((i for i in range(len(fds) - 2, -1, -1) if hab_hasta(fds[i]) >= 5), None)
    if k_ is not None:
        dias_v, base_v = hab_hasta(fds[k_]), f"últimos {hab_hasta(fds[k_])} días hábiles"
        real_ue = lambda s_, dev_: (dev_ - DV.at[fechas[k_], s_]) / dias_v * 5 if s_ in DV and pd.notna(DV.at[fechas[k_], s_]) else None
    else:
        dias_v = par["habiles_desde_base"]
        b_ = dt.date.fromisoformat(par["fecha_base"]) + dt.timedelta(days=1)
        base_v = f"desde el {b_.day}/{b_.month:02d} ({dias_v} días hábiles)"
        real_ue = lambda s_, dev_: None
    for u in ues:
        r_ = real_ue(u["sec"], u["dev"])
        if r_ is None:
            r_ = u["dev_base"] / dias_v * 5 if dias_v else 0.0
        u.update({"nec_sem": round(u["saldo"] / hab_rest * 5, 2) if hab_rest else 0.0, "real_sem": round(r_, 2)})
    comparacion = aplicar_actividad(ues, H, fc)
    tot_ = serie.loc[fc]
    dserie = serie.DEVENGADO.tolist()
    semana = {"dias_cal": max(0, (dt.date(cfg["anio"], 12, 31) - corte).days), "dias_habiles": hab_rest,
              "nec_sem": round(float(tot_.saldo) / hab_rest * 5, 2) if hab_rest else 0.0,
              "real_sem": round(sum(u["real_sem"] for u in ues), 2), "base": base_v, "base_dias": dias_v,
              "var": round(dserie[-1] - dserie[-2], 2) if len(dserie) > 1 else None, "var_dias": par.get("habiles_entre"),
              "cortes": [[fechas[i], round(dserie[i] - dserie[i - 1], 2), dias_habiles(fds[i - 1] + dt.timedelta(days=1), fds[i]), fechas[i - 1]]
                         for i in range(1, len(fechas))]}
    J = {"id": eid, "pliego": cfg["nombre"], "sigla": cfg.get("sigla", ""), "codigo": cfg["pliego"], "nivel": cfg["nivel"], "sector": cfg["sector"],
         "informe": os.path.isfile(os.path.join(RAIZ, eid, "index.html")), "vigilancia": cfg.get("control", True), "hist_inicio": fechas[0], "anio": cfg["anio"], "corte": fc, "corte_anterior": prev, "comparacion": comparacion, "parametros": par,
         "actualizado": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "total": {k: (None if pd.isna(v) else v) for k, v in serie.loc[fc].to_dict().items()},
         "serie": {k: [None if pd.isna(x) else x for x in serie[k].tolist()] for k in serie.columns}, "fechas": list(serie.index),
         "serie_ue": {s: {k: [None if pd.isna(x) else x for x in v] for k, v in H[H.SEC_EJEC == s].set_index("fecha_corte")[["PIM", "saldo", "riesgo", "DEVENGADO", "dev_desde_base"]]
                          .reindex(serie.index).round(2).to_dict("list").items()} for s in g.SEC_EJEC},
         "controles": controles(g, ues, serie.loc[fc], P),
         "fuentes": [(H[H.fecha_corte == f_].fuente.dropna().iloc[0] if "fuente" in H.columns and H[H.fecha_corte == f_].fuente.notna().any() else None) for f_ in serie.index],
         "mensual": mensual.round(2).tolist(), "semana": semana, "ues": ues}
    n_rev = len(json.load(open(os.path.join(out, "revisiones.json"), encoding="utf-8")).get("revisiones", [])) if os.path.isfile(os.path.join(out, "revisiones.json")) else 0
    revisar(out, J)
    nuevas = len(json.load(open(os.path.join(out, "revisiones.json"), encoding="utf-8"))["revisiones"]) - n_rev
    json.dump(J, open(os.path.join(out, "datos.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    # la página es la misma para todas las entidades: se copia de la plantilla en cada corrida (lee todo de datos.json)
    shutil.copyfile(os.path.join(AQUI, "plantilla_control_ro.html"), os.path.join(out, "index.html"))
    if previo.get("corte") != fc:
        res, det = "nuevo_corte", f"Nuevo corte {fdm(fc)} (antes {fdm(previo.get('corte'))})."
    elif nuevas > 0:
        res, det = "cifras_revisadas", f"Mismo corte {fdm(fc)}; {nuevas} cifra(s) revisada(s) respecto de lo publicado."
    else:
        res, det = "sin_cambios", f"Consulta sin corte nuevo: se mantiene el corte {fdm(fc)} y sus totales."
    guardar_estado(out, resultado=res, detalle=det, corte_publicado=fc, corte_candidato=fc, fuente=fuente)
    print(f"corte {fc} · {len(ues)} UE · saldo RO S/ {J['total']['saldo'] / 1e6:,.1f} M · riesgo S/ {J['total']['riesgo'] / 1e6:,.1f} M · {det}")


if __name__ == "__main__":
    if "--recalcular-actividad" in sys.argv:
        recalcular_actividad(sys.argv[1])
        sys.exit(0)
    try:
        main()
    except DatosInvalidos as e:                  # el insumo no es confiable: no se escribe nada y queda el último corte válido
        print(f"::error::Datos del MEF rechazados: {e}")
        sys.exit(2)
