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


def calcular(df, publicado, anio):
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
    g["ritmo_mensual"] = U.groupby("SEC_EJEC")[completos].sum().mean(axis=1)
    gen = U.groupby(["SEC_EJEC", "GENERICA"])[["PIM", "DEVENGADO", "saldo", "riesgo"]].sum().reset_index()
    mensual = df[df.MES_EJE > 0].groupby("MES_EJE").DEVENGADO.sum().reindex(range(1, 13)).fillna(0.0)
    hab = dias_habiles(corte + dt.timedelta(days=1), dt.date(anio, 12, 31))
    return corte, g[g.PIM > 0].reset_index(), gen, mensual, {"meses_ritmo": completos, "meses_restantes": round(meses_rest, 4),
                                                              "dias_habiles": hab, "publicado_lima": lima.strftime("%d/%m/%Y %H:%M")}


def main():
    eid = sys.argv[1]
    cfg = json.load(open(os.path.join(AQUI, "pliegos.json"), encoding="utf-8"))[eid]
    out = os.path.join(RAIZ, eid, "ro")
    os.makedirs(out, exist_ok=True)
    df, nombres, publicado = desde_json(sys.argv[3], cfg.get("excluir_json", [])) if "--json" in sys.argv else descargar(cfg)
    corte, g, gen, mensual, par = calcular(df, publicado, cfg["anio"])
    fc = corte.isoformat()
    g.insert(0, "fecha_corte", fc)
    g["UE"] = g.SEC_EJEC.map(lambda x: nombre(nombres.get(x, x)))

    # historial: una fila por corte y UE
    fh = os.path.join(out, "historial.csv")
    H = pd.read_csv(fh, dtype={"SEC_EJEC": str}) if os.path.isfile(fh) else pd.DataFrame()
    cols = ["fecha_corte", "SEC_EJEC", "UE", "PIM", "CERTIFICADO", "COMPROMETIDO", "DEVENGADO", "saldo", "no_certificado", "adicional", "riesgo"]
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
             "gen": {x.GENERICA: [x.PIM, x.DEVENGADO, x.saldo, x.riesgo] for x in gen[gen.SEC_EJEC == r.SEC_EJEC].itertuples() if abs(x.PIM) > 0.5}}
        if P is not None and r.SEC_EJEC in P.index:
            d["dev_ant"], d["riesgo_ant"] = float(P.loc[r.SEC_EJEC, "DEVENGADO"]), float(P.loc[r.SEC_EJEC, "riesgo"])
        ues.append({k: (round(v, 2) if isinstance(v, float) else v) for k, v in d.items()})
    serie = H.groupby("fecha_corte")[["PIM", "DEVENGADO", "saldo", "riesgo", "no_certificado"]].sum().round(2)
    J = {"pliego": cfg["nombre"], "anio": cfg["anio"], "corte": fc, "corte_anterior": prev, "parametros": par,
         "actualizado": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "total": serie.loc[fc].to_dict(), "serie": {k: serie[k].tolist() for k in serie.columns}, "fechas": list(serie.index),
         "serie_ue": {s: H[H.SEC_EJEC == s].set_index("fecha_corte")[["saldo", "riesgo", "DEVENGADO"]].reindex(serie.index).round(2)
                      .fillna(0).to_dict("list") for s in g.SEC_EJEC},
         "mensual": mensual.round(2).tolist(), "ues": ues}
    json.dump(J, open(os.path.join(out, "datos.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"corte {fc} · {len(ues)} UE · saldo RO S/ {J['total']['saldo'] / 1e6:,.1f} M · riesgo S/ {J['total']['riesgo'] / 1e6:,.1f} M")


if __name__ == "__main__":
    main()
