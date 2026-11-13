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
import os
import sys
from email.utils import parsedate_to_datetime

import pandas as pd

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
URL = "https://fs.datosabiertos.mef.gob.pe/datastorefiles/{anio}-Gasto-Diario.csv"
C = {"MES": 1, "NIVEL": 2, "SECTOR": 4, "PLIEGO": 6, "SEC_EJEC": 8, "EJEC_NOM": 10, "SEC_FUNC": 17, "TIPO": 20, "PROD": 22,
     "META": 32, "FUENTE": 37, "RUBRO": 39, "GEN": 46, "GEN_NOM": 47, "M0": 56}
MONTOS = ["PIA", "PIM", "CERTIFICADO", "COMPROMETIDO_ANUAL", "COMPROMETIDO", "DEVENGADO", "GIRADO"]
LLAVES = ["SEC_EJEC", "SEC_FUNC", "TIPO_ACT_PROY", "PRODUCTO_PROYECTO", "META", "RUBRO", "GENERICA"]
FERIADOS_DIC = {8, 25}   # días no laborables fijos que quedan en diciembre
_MIN = {"De", "Del", "La", "Las", "Los", "Y", "En", "El", "Para", "A", "Por"}
_TILDE = {"Administracion": "Administración", "Direccion": "Dirección", "Credito": "Crédito", "Estrategicos": "Estratégicos", "Pediatricas": "Pediátricas",
          "Creacion": "Creación", "Atencion": "Atención", "Rehabilitacion": "Rehabilitación", "Oftalmologia": "Oftalmología", "Neurologicas": "Neurológicas",
          "Movil": "Móvil", "Huaycan": "Huaycán", "Lima Este - Vitarte": "Lima Este - Vitarte", "Bartolome": "Bartolomé", "Valdizan": "Valdizán"}


def nombre(t):
    """Nombre oficial en mayúsculas → forma legible con tildes, sin alterar siglas."""
    w = str(t).strip().title().split()
    t = " ".join(x.lower() if (i and x in _MIN) else x for i, x in enumerate(w)).replace("Minsa", "MINSA").replace(" Ii ", " II ").replace(" Iii ", " III ")
    for a, b in _TILDE.items():
        t = t.replace(a, b)
    return t


def descargar(cfg):
    """Lee el CSV por partes y agrega en memoria. Devuelve (DataFrame, nombres de UE, fecha de publicación)."""
    import requests
    url = URL.format(anio=cfg["anio"])
    r = requests.get(url, stream=True, timeout=120, headers={"User-Agent": "Mozilla/5.0 (cuadro de control RO)"})
    r.raise_for_status()
    publicado = r.headers.get("Last-Modified")
    clave = f'"{cfg["pliego"]}"'
    agg, nombres, n = {}, {}, 0
    lineas = r.iter_lines(chunk_size=1 << 20, decode_unicode=False)
    next(lineas)                                    # encabezado
    for b in lineas:
        n += 1
        if clave.encode() not in b:
            continue
        f = b.decode("utf-8", "replace").rstrip("\r").strip('"').split('","')
        if f[C["NIVEL"]] != cfg["nivel"] or f[C["SECTOR"]] != cfg["sector"] or f[C["PLIEGO"]] != cfg["pliego"] or f[C["FUENTE"]] != "1":
            continue
        k = (int(f[C["MES"]]), f[C["SEC_EJEC"]], f[C["SEC_FUNC"]], f[C["TIPO"]], f[C["PROD"]], f[C["META"]], f[C["RUBRO"]], f[C["GEN"]])
        a = agg.get(k)
        if a is None:
            a = agg[k] = [0.0] * len(MONTOS)
        for i in range(len(MONTOS)):
            try:
                a[i] += float(f[C["M0"] + i] or 0)
            except ValueError:
                pass
        nombres[f[C["SEC_EJEC"]]] = f[C["EJEC_NOM"]]
    print(f"líneas leídas: {n:,} · combinaciones RO: {len(agg):,}")
    df = pd.DataFrame([list(k) + v for k, v in agg.items()], columns=["MES_EJE"] + LLAVES + MONTOS)
    return df, nombres, publicado


def desde_json(ruta, excluir=()):
    """Los JSON antiguos no filtraban el sector: se quitan las UE de otro sector con el mismo código de pliego."""
    J = json.load(open(ruta, encoding="utf-8"))
    df = pd.DataFrame(J["filas"], columns=J["claves"] + J["montos"])
    df = df[(df.FUENTE == "1") & ~df.SEC_EJEC.isin(list(excluir))].drop(columns="FUENTE")
    df["MES_EJE"] = df.MES_EJE.astype(int)
    for c in MONTOS:
        df[c] = df[c].astype(float)
    return df, J["nombres"]["ue"], J["meta"]["publicado"]


def dias_habiles(desde, hasta):
    d, n = desde, 0
    while d <= hasta:
        if d.weekday() < 5 and not (d.month == 12 and d.day in FERIADOS_DIC):
            n += 1
        d += dt.timedelta(days=1)
    return n


def tiempo(corte, anio, mes_base=10):
    """Meses que faltan al 31/12, días hábiles y avance esperado (lineal y por mes cerrado) a una fecha de corte."""
    M, dia = corte.month, corte.day
    dmes = calendar.monthrange(anio, M)[1]
    meses_rest = (0.0 if dia == dmes else (dmes - dia) / dmes) + (12 - M)
    fin_b = dt.date(anio, mes_base, calendar.monthrange(anio, mes_base)[1])
    ht = dias_habiles(fin_b + dt.timedelta(days=1), dt.date(anio, 12, 31))
    hp = dias_habiles(fin_b + dt.timedelta(days=1), corte)
    mc = (M - mes_base - 1) + (1 if dia == dmes else 0)
    return {"meses_rest": meses_rest, "habiles_rest": dias_habiles(corte + dt.timedelta(days=1), dt.date(anio, 12, 31)),
            "fin_base": fin_b, "habiles_total": ht, "habiles_pasados": hp, "esp_lin": hp / ht if ht else 0.0,
            "esp_mes": max(0, mc) / (12 - mes_base)}


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
        w = float(da.w_planilla) if da is not None else 0.0
        tipo = (str(da.tipo) if da is not None else ("2" if len(k) == 7 and k.startswith("2") else "3"))
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
        return g, par, corte_da, "Datos Abiertos", None
    if not fecha or fecha <= corte_da:
        print(f"Consulta Amigable al {fecha}: no es más reciente que Datos Abiertos ({corte_da})")
        return g, par, corte_da, "Datos Abiertos", None
    falt = sorted(set(g.SEC_EJEC) - set(filas))
    if falt:
        print(f"Consulta Amigable sin las UE {falt}; se usa Datos Abiertos")
        return g, par, corte_da, "Datos Abiertos", None
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
        ok = (sp.PIM.reindex(g.SEC_EJEC).values - g.PIM.values)
        cuadra = pd.Series(abs(ok) < 1.0, index=g.index)
        g.loc[cuadra, "riesgo"] = g.loc[cuadra, "SEC_EJEC"].map(sp.riesgo)
        g.loc[cuadra, "adicional"] = g.loc[cuadra, "SEC_EJEC"].map(sp.adicional)
        if (~cuadra).any():
            print(f"proyectos no cuadran con el total en {list(g.loc[~cuadra, 'SEC_EJEC'])}: se mantiene el riesgo por unidad")
    par = dict(par, meses_restantes=round(t1["meses_rest"], 4), dias_habiles=t1["habiles_rest"], habiles_desde_base=t1["habiles_pasados"],
               avance_esperado=round(t1["esp_lin"], 4), corte_detalle=corte_da.isoformat(),
               publicado_lima=f"Consulta Amigable, actualizada al {fecha.strftime('%d/%m/%Y')}")
    print(f"Consulta Amigable al {fecha}: devengado RO S/ {g.DEVENGADO.sum() / 1e6:,.1f} M (Datos Abiertos al {corte_da}: S/ {dev_da.sum() / 1e6:,.1f} M)")
    return g, par, fecha, "Consulta Amigable", PR


def calcular(df, publicado, anio, mes_base=10):
    """El archivo se publica en la mañana (hora de Lima) con datos hasta el día anterior."""
    lima = parsedate_to_datetime(publicado) - dt.timedelta(hours=5)
    corte = lima.date() - dt.timedelta(days=1)
    M, dia = corte.month, corte.day
    dmes = calendar.monthrange(anio, M)[1]
    if dia == dmes:
        frac, completos = 0.0, [M - 2, M - 1, M]
    else:
        frac, completos = (dmes - dia) / dmes, [M - 3, M - 2, M - 1]
    meses_rest = frac + (12 - M)
    tot = df.groupby(LLAVES)[MONTOS].sum()
    mens = df[df.MES_EJE > 0].pivot_table(index=LLAVES, columns="MES_EJE", values="DEVENGADO", aggfunc="sum") \
        .reindex(columns=range(1, 13)).fillna(0.0)
    U = tot.join(mens, how="left").fillna(0.0).reset_index()
    U["saldo"] = U.PIM - U.DEVENGADO
    adic = (U[completos].mean(axis=1) * meses_rest).clip(lower=0)
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
    fin_b = dt.date(anio, mes_base, calendar.monthrange(anio, mes_base)[1])
    _ht = dias_habiles(fin_b + dt.timedelta(days=1), dt.date(anio, 12, 31))
    esp_lin = dias_habiles(fin_b + dt.timedelta(days=1), corte) / _ht if _ht else 0.0
    _mc = (M - mes_base - 1) + (1 if dia == dmes else 0)          # meses completos cerrados después del mes base
    esp_mes = max(0, _mc) / (12 - mes_base)
    U["saldo_ini"] = U.saldo + U.dev_desde_base
    U["esp_w"] = U.saldo_ini.clip(lower=0) * U.GENERICA.astype(str).isin(["1", "2"]).map({True: esp_mes, False: esp_lin})
    _w = U.groupby("SEC_EJEC")[["saldo_ini", "esp_w"]].sum()
    g["esperado"] = (_w.esp_w / U.assign(si=U.saldo_ini.clip(lower=0)).groupby("SEC_EJEC").si.sum()).fillna(0.0)
    _si = U.assign(si=U.saldo_ini.clip(lower=0))
    g["w_planilla"] = (_si[_si.GENERICA.astype(str).isin(["1", "2"])].groupby("SEC_EJEC").si.sum() / _si.groupby("SEC_EJEC").si.sum()).reindex(g.index).fillna(0.0)
    g["ritmo_mensual"] = U.groupby("SEC_EJEC")[completos].sum().mean(axis=1)
    gen = U.groupby(["SEC_EJEC", "GENERICA"])[["PIM", "DEVENGADO", "saldo", "riesgo"]].sum().reset_index()
    mensual = df[df.MES_EJE > 0].groupby("MES_EJE").DEVENGADO.sum().reindex(range(1, 13)).fillna(0.0)
    hab = dias_habiles(corte + dt.timedelta(days=1), dt.date(anio, 12, 31))
    fin_base = dt.date(anio, mes_base, calendar.monthrange(anio, mes_base)[1])
    hab_tot = dias_habiles(fin_base + dt.timedelta(days=1), dt.date(anio, 12, 31))
    hab_pas = dias_habiles(fin_base + dt.timedelta(days=1), corte)
    _U = U.assign(si=U.saldo_ini.clip(lower=0), pl=U.GENERICA.astype(str).isin(["1", "2"]),
                  rit=U[completos].mean(axis=1), hb=U.DEVENGADO - U.dev_desde_base)
    _U["si_pl"] = _U.si.where(_U.pl, 0.0)
    PDA = _U.groupby(["SEC_EJEC", "PRODUCTO_PROYECTO"]).agg(tipo=("TIPO_ACT_PROY", "first"), PIM=("PIM", "sum"), DEVENGADO=("DEVENGADO", "sum"),
                                                          adicional=("adicional", "sum"), ritmo=("rit", "sum"), hasta_base=("hb", "sum"),
                                                          si=("si", "sum"), si_pl=("si_pl", "sum")).reset_index()
    PDA["w_planilla"] = (PDA.si_pl / PDA.si).where(PDA.si > 0, 0.0)
    calcular.PDA = PDA
    return corte, g[g.PIM > 0].reset_index(), gen, mensual, {"meses_ritmo": completos, "meses_restantes": round(meses_rest, 4),
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
    previos = {}                                    # Datos Abiertos no trae nombres: se conservan los de la última consulta
    if os.path.isfile(fj):
        try:
            for lst in json.load(open(fj, encoding="utf-8"))["ues"].values():
                previos.update({x["c"]: x["n"] for x in lst if x["n"] != x["c"]})
        except (ValueError, KeyError):
            pass
    PR["nombre"] = [previos.get(c, n) if n == c else nombre(n) for c, n in zip(PR.cod, PR.nombre)]
    H = pd.read_csv(fh, dtype={"SEC_EJEC": str, "cod": str}) if os.path.isfile(fh) else pd.DataFrame(columns=cols)
    H = H[H.fecha_corte != fc]
    prev = sorted(H.fecha_corte.unique())[-1] if len(H) else None
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


def main():
    eid = sys.argv[1]
    cfg = json.load(open(os.path.join(AQUI, "pliegos.json"), encoding="utf-8"))[eid]
    out = os.path.join(RAIZ, eid, "ro")
    os.makedirs(out, exist_ok=True)
    df, nombres, publicado = desde_json(sys.argv[3], cfg.get("excluir_json", [])) if "--json" in sys.argv else descargar(cfg)
    corte, g, gen, mensual, par = calcular(df, publicado, cfg["anio"], cfg.get("mes_base", 10))
    par["corte_detalle"] = corte.isoformat()
    fuente, PR = "Datos Abiertos", None
    corte_da = corte
    if "--sin-ca" not in sys.argv:
        g, par, corte, fuente, PR = con_consulta_amigable(g, par, corte, cfg)
    if PR is None:                       # sin Consulta Amigable: los proyectos salen de Datos Abiertos al mismo corte
        t = tiempo(corte, cfg["anio"], cfg.get("mes_base", 10))
        PR = por_proyecto(calcular.PDA, None, t, t)
    par["fuente"] = fuente
    fc = corte.isoformat()
    guardar_proyectos(PR, fc, os.path.join(RAIZ, eid, "ro"))
    g.insert(0, "fecha_corte", fc)
    g["UE"] = g.SEC_EJEC.map(lambda x: nombre(nombres.get(x, x)))

    # historial: una fila por corte y UE
    fh = os.path.join(out, "historial.csv")
    H = pd.read_csv(fh, dtype={"SEC_EJEC": str}) if os.path.isfile(fh) else pd.DataFrame()
    g["esp_monto"] = g.esperado * (g.saldo + g.dev_desde_base).clip(lower=0)
    cols = ["fecha_corte", "SEC_EJEC", "UE", "PIM", "CERTIFICADO", "COMPROMETIDO", "DEVENGADO", "saldo", "no_certificado", "adicional", "riesgo", "dev_desde_base", "esp_monto"]
    if len(H):
        H = H[H.fecha_corte != fc]
    H = pd.concat([H, g[cols]], ignore_index=True).sort_values(["fecha_corte", "riesgo"], ascending=[True, False])
    H.round(2).to_csv(fh, index=False, quoting=csv.QUOTE_NONNUMERIC)

    fechas = sorted(H.fecha_corte.unique())
    prev = fechas[-2] if len(fechas) > 1 else None
    P = H[H.fecha_corte == prev].set_index("SEC_EJEC") if prev else None
    sig = cfg.get("siglas_ue", {})
    ues = []
    for r in g.sort_values("riesgo", ascending=False).itertuples():
        d = {"sec": r.SEC_EJEC, "ue": nombre(nombres.get(r.SEC_EJEC) or r.SEC_EJEC), "sigla": sig.get(r.SEC_EJEC, ""),
             "pim": r.PIM, "cert": r.CERTIFICADO, "comp": r.COMPROMETIDO, "dev": r.DEVENGADO, "saldo": r.saldo,
             "no_cert": r.no_certificado, "adicional": r.adicional, "riesgo": r.riesgo, "dev_mes": r.dev_mes_corte, "ritmo": r.ritmo_mensual,
             "dev_base": r.dev_desde_base, "saldo_ini": r.saldo + r.dev_desde_base,
             "avance": (r.dev_desde_base / (r.saldo + r.dev_desde_base)) if (r.saldo + r.dev_desde_base) > 0 else 0.0,
             "esperado": r.esperado,
             "gen": {x.GENERICA: [x.PIM, x.DEVENGADO, x.saldo, x.riesgo] for x in gen[gen.SEC_EJEC == r.SEC_EJEC].itertuples() if abs(x.PIM) > 0.5}}
        if P is not None and r.SEC_EJEC in P.index:
            d["dev_ant"], d["riesgo_ant"] = float(P.loc[r.SEC_EJEC, "DEVENGADO"]), float(P.loc[r.SEC_EJEC, "riesgo"])
        ues.append({k: (round(v, 4 if k in ("avance", "esperado") else 2) if isinstance(v, float) else v) for k, v in d.items()})
    for c_ in ("dev_desde_base", "esp_monto"):
        if c_ not in H.columns:
            H[c_] = float("nan")
    serie = H.groupby("fecha_corte")[["PIM", "DEVENGADO", "saldo", "riesgo", "no_certificado", "dev_desde_base", "esp_monto"]].sum(min_count=1).round(2)
    serie["avance"] = (serie.dev_desde_base / (serie.saldo + serie.dev_desde_base)).round(4)
    serie["esperado"] = (serie.esp_monto / (serie.saldo + serie.dev_desde_base)).round(4)
    J = {"pliego": cfg["nombre"], "anio": cfg["anio"], "corte": fc, "corte_anterior": prev, "parametros": par,
         "actualizado": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "total": {k: (None if pd.isna(v) else v) for k, v in serie.loc[fc].to_dict().items()},
         "serie": {k: [None if pd.isna(x) else x for x in serie[k].tolist()] for k in serie.columns}, "fechas": list(serie.index),
         "serie_ue": {s: H[H.SEC_EJEC == s].set_index("fecha_corte")[["saldo", "riesgo", "DEVENGADO", "dev_desde_base"]].reindex(serie.index).round(2)
                      .fillna(0).to_dict("list") for s in g.SEC_EJEC},
         "mensual": mensual.round(2).tolist(), "ues": ues}
    json.dump(J, open(os.path.join(out, "datos.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"corte {fc} · {len(ues)} UE · saldo RO S/ {J['total']['saldo'] / 1e6:,.1f} M · riesgo S/ {J['total']['riesgo'] / 1e6:,.1f} M")


if __name__ == "__main__":
    main()
