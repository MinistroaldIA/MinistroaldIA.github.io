#!/usr/bin/env python3
"""Verificaciones del control diario de RO antes de publicar.

Uso: python herramientas/verificar_control.py <id>
Lee <id>/ro/datos.json (valores originales, sin redondeo de pantalla) y <id>/ro/proyectos.json, y termina con código 1
si algo no cuadra. En la tarea automática corre antes de publicar: si falla, no se publica nada y queda el último corte válido.

Tres grupos (auditoría del 13/11/2026, hallazgo M08):
  A. Contrato de datos: campos presentes, números finitos, claves únicas, cronología y cobertura de unidades.
  B. Cálculo: conciliaciones presupuestales y del ritmo semanal.
  C. Actividad entre cortes: el estado y la racha de cada unidad se RECALCULAN aquí con una implementación propia
     (no se copia la del generador) y se comparan con lo publicado. Así un ajuste negativo mostrado como «sin variación»
     o una racha inventada hacen fallar la verificación.
Ninguna regla se registra como conforme de forma incondicional. pruebas_control.py demuestra que cada una falla ante un
error sembrado.
"""
import json
import math
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOL = 1.0          # soles: tolerancia por el redondeo a céntimos del archivo
ESTADOS = {"aumento", "sin_variacion", "ajuste_negativo", "sin_comparacion"}
NUM_UE = ("pim", "cert", "dev", "saldo", "no_cert", "riesgo", "dev_base", "saldo_ini", "avance", "esperado", "nec_sem", "real_sem")


def finito(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def estado_esperado(var, tol):
    """Implementación independiente de la regla publicada en la página («Cómo se calcula»)."""
    if var is None:
        return "sin_comparacion"
    return "aumento" if var > tol else ("ajuste_negativo" if var < -tol else "sin_variacion")


def _hay_dia_habil(desde, hasta):
    """¿Hay al menos un día hábil después de «desde» y hasta «hasta»? (lunes a viernes, sin feriados nacionales). Implementación propia:
    la lista de feriados es la de tablero_ro.py, comparada con la del núcleo en pruebas_control.py."""
    import datetime as _dt
    from tablero_ro import feriados as _fer
    d, fin = _dt.date.fromisoformat(desde) + _dt.timedelta(days=1), _dt.date.fromisoformat(hasta)
    while d <= fin:
        if d.isoweekday() <= 5 and d not in _fer(d.year):
            return True
        d += _dt.timedelta(days=1)
    return False


def racha_esperada(serie, fechas, tol):
    """Cortes consecutivos sin aumento neto, recorriendo la serie de atrás hacia adelante (implementación independiente)."""
    pares = list(zip(fechas, serie))
    if len(pares) < 2 or pares[-1][1] is None:
        return None
    n = 0
    k = len(pares) - 1
    while k > 0:
        (_, a), (fk, b) = pares[k - 1], pares[k]
        if a is None:
            return None if n == 0 else (n, fk, True)
        if b - a > tol:
            return (n, fk, False)
        if _hay_dia_habil(pares[k - 1][0], fk):       # un corte de sábado, domingo o feriado no cuenta como corte sin aumento
            n += 1
        k -= 1
    return (n, pares[0][0], True)


def verificar(D, PJ=None):
    """Devuelve [(conforme, regla, detalle)]. No imprime ni termina el proceso."""
    res = []
    ok = lambda regla, cond, det="": res.append((bool(cond), regla, det))
    U, T, par = D.get("ues") or [], D.get("total") or {}, D.get("parametros") or {}
    tol = par.get("tolerancia_variacion", TOL)

    # ---------- A. Contrato de datos ----------
    falt = [k for k in ("corte", "fechas", "serie", "ues", "total", "parametros", "semana", "comparacion") if k not in D]
    ok("Contrato: datos.json trae todos sus bloques", not falt, ", ".join(falt))
    malos = sorted({f"{u.get('sec')}:{k}" for u in U for k in NUM_UE if not finito(u.get(k))})
    ok("Contrato: importes y razones de cada unidad son números finitos", not malos, ", ".join(malos[:8]))
    malos = [k for k in ("PIM", "DEVENGADO", "saldo", "riesgo", "no_certificado") if not finito(T.get(k))]
    ok("Contrato: totales del pliego son números finitos", not malos, ", ".join(malos))
    secs = [u.get("sec") for u in U]
    ok("Contrato: cada unidad aparece una sola vez", len(secs) == len(set(secs)) and all(secs), f"{len(secs)} filas, {len(set(secs))} unidades")
    F = D.get("fechas") or []
    ok("Cronología: los cortes del historial van en orden estrictamente creciente", all(a < b for a, b in zip(F, F[1:])), " → ".join(F[-4:]))
    ok("Cronología: el corte publicado es el más reciente del historial", bool(F) and D.get("corte") == F[-1], f"{D.get('corte')} frente a {F[-1] if F else '—'}")
    ant = max([f for f in F if f < D.get("corte", "")], default=None)
    ok("Cronología: el corte anterior es el mayor estrictamente menor al publicado", D.get("corte_anterior") == ant, f"{D.get('corte_anterior')} frente a {ant}")
    C = D.get("comparacion") or {}
    ok("Cronología: la comparación declara las fechas de los dos cortes", C.get("desde") == ant and C.get("hasta") == D.get("corte"), f"{C.get('desde')} → {C.get('hasta')}")
    S = D.get("serie") or {}
    malos = [k for k, v in S.items() if len(v) != len(F)]
    ok("Contrato: cada serie tiene un valor por corte", not malos, ", ".join(malos))
    if PJ is not None:
        sin_ue = sorted(set(PJ.get("ues", {})) - set(secs))
        ok("Cobertura: todo proyecto pertenece a una unidad publicada", not sin_ue, ", ".join(sin_ue))
        ok("Cobertura: proyectos y unidades están al mismo corte", PJ.get("corte") == D.get("corte"), f"{PJ.get('corte')} frente a {D.get('corte')}")
        malos = []
        for u in U:
            L = PJ.get("ues", {}).get(u["sec"])
            if L:
                dp, dd = sum(x["pim"] for x in L) - u["pim"], sum(x["dev"] for x in L) - u["dev"]
                if abs(dp) > TOL or abs(dd) > TOL:
                    malos.append(f"{u['sec']} (PIM {dp:+,.2f}; devengado {dd:+,.2f})")
        ok("Concordancia: los proyectos suman el PIM y el devengado de su unidad", not malos, "; ".join(malos[:5]))

    # ---------- B. Cálculo ----------
    malas = [u["sec"] for u in U if abs(u["saldo"] - (u["pim"] - u["dev"])) > TOL]
    ok("Saldo = PIM − devengado en cada unidad", not malas, ", ".join(malas))
    malas = [u["sec"] for u in U if abs(u["no_cert"] - (u["pim"] - u["cert"])) > TOL]
    ok("Sin certificar = PIM − certificado en cada unidad", not malas, ", ".join(malas))
    for k_u, k_t in (("pim", "PIM"), ("dev", "DEVENGADO"), ("saldo", "saldo"), ("riesgo", "riesgo"), ("no_cert", "no_certificado")):
        s = sum(u[k_u] for u in U)
        ok(f"Suma de las unidades = total del pliego ({k_t})", abs(s - T[k_t]) <= TOL * max(1, len(U)), f"{s:,.2f} frente a {T[k_t]:,.2f}")
    comp = [u for u in U if u.get("dev_ant") is not None]
    malas = [u["sec"] for u in comp if abs((u["saldo"] - u["saldo_ant"]) - ((u["pim"] - u["pim_ant"]) - (u["dev"] - u["dev_ant"]))) > TOL]
    ok("Variación del saldo = variación del PIM − variación del devengado (por unidad)", not malas, ", ".join(malas))
    if len(F) > 1:
        i = len(F) - 1
        ds, dp, dd = S["saldo"][i] - S["saldo"][i - 1], S["PIM"][i] - S["PIM"][i - 1], S["DEVENGADO"][i] - S["DEVENGADO"][i - 1]
        ok("Variación del saldo = variación del PIM − variación del devengado (pliego)", abs(ds - (dp - dd)) <= TOL * len(U), f"{ds:,.2f} frente a {dp - dd:,.2f}")
    malas = [u["sec"] for u in comp if abs((u["riesgo"] - u["riesgo_ant"]) - ((u["pim"] - u["pim_ant"]) - (u["dev"] - u["dev_ant"]) - ((u["saldo"] - u["riesgo"]) - (u["saldo_ant"] - u["riesgo_ant"])))) > TOL]
    ok("La explicación de la variación del riesgo concilia (PIM, devengado, tiempo y supuestos)", not malas, ", ".join(malas))
    malas = [u["sec"] for u in U if abs(u["saldo_ini"] - (u["pim"] - (u["dev"] - u["dev_base"]))) > TOL]
    ok("Base del avance = PIM actual − devengado al cierre del último mes", not malas, ", ".join(malas))
    malas = [u["sec"] for u in U if u["saldo_ini"] > 0 and abs(u["avance"] - u["dev_base"] / u["saldo_ini"]) > 1e-4]
    ok("Avance = devengado desde la base ÷ base", not malas, ", ".join(malas))
    malas = [u["sec"] for u in U if not (-TOL <= u["riesgo"] <= max(u["saldo"], 0) + TOL)]
    ok("Saldo proyectado al 31/12 entre 0 y el saldo en cada unidad", not malas, ", ".join(malas))
    malas = [u["sec"] for u in U if not (0 <= u["esperado"] <= 1 + 1e-9)]
    ok("Avance esperado entre 0 % y 100 % en cada unidad", not malas, ", ".join(malas))
    con_h = [u for u in U if u.get("riesgo_hist") is not None]
    malas = [u["sec"] for u in con_h if not (min(u["rh_bajo"], u["rh_alto"]) - TOL <= u["riesgo_hist"] <= max(u["rh_bajo"], u["rh_alto"]) + TOL)]
    ok("Escenario según patrones dentro de su rango en cada unidad", not malas, ", ".join(malas))
    if T.get("riesgo_hist") is not None:
        ok("Suma del escenario según patrones = total del pliego", abs(sum(u["riesgo_hist"] for u in con_h) - T["riesgo_hist"]) <= TOL * len(U))
    ok("La procedencia de cada componente está publicada", bool(par.get("procedencia")) and bool(par.get("metodo")), par.get("fuente", ""))
    SM = D.get("semana") or {}
    ok("El ritmo semanal está publicado", bool(SM))
    if SM:
        h = SM["dias_habiles"]
        malas = [u["sec"] for u in U if h and abs(u["nec_sem"] - u["saldo"] / h * 5) > TOL]
        ok("Necesario por semana = saldo ÷ días hábiles × 5, en cada unidad", not malas, ", ".join(malas))
        ok("Necesario por semana del pliego = suma de las unidades", abs(sum(u["nec_sem"] for u in U) - SM["nec_sem"]) <= TOL * len(U))
        ok("Devengado por semana del pliego = suma de las unidades", abs(sum(u["real_sem"] for u in U) - SM["real_sem"]) <= TOL * len(U), SM.get("base", ""))
        ok("Días calendario y hábiles al 31/12 coherentes", 0 <= h <= SM["dias_cal"], f"{SM['dias_cal']} calendario · {h} hábiles")
        if SM.get("var") is not None:
            ok("Variación entre cortes del pliego = suma de las unidades", abs(sum(u.get("var") or 0 for u in U) - SM["var"]) <= TOL * len(U))

    # ---------- C. Actividad entre cortes ----------
    malas = [u["sec"] for u in U if u.get("estado") not in ESTADOS]
    ok("Cada unidad tiene un estado de variación válido", not malas, ", ".join(malas))
    malas = [u["sec"] for u in U if (u.get("var") is None) != (u.get("dev_ant") is None)
             or (u.get("var") is not None and abs(u["var"] - (u["dev"] - u["dev_ant"])) > 0.01)]
    ok("Variación entre cortes = devengado actual − devengado del corte anterior", not malas, ", ".join(malas))
    malas = [f"{u['sec']} ({u.get('var')}: {u.get('estado')})" for u in U if u.get("estado") != estado_esperado(u.get("var"), tol)]
    ok(f"Estado coherente con la variación (aumento > S/ {tol:g}; sin variación ±S/ {tol:g}; ajuste < −S/ {tol:g}; sin comparación)", not malas, "; ".join(malas[:6]))
    neg = [u["sec"] for u in U if u.get("var") is not None and u["var"] < -tol]
    ok("Ningún ajuste neto negativo figura como «sin variación» ni como aumento", all(u.get("estado") == "ajuste_negativo" for u in U if u["sec"] in neg),
       ("ajustes: " + ", ".join(neg)) if neg else "sin ajustes netos negativos")
    SU = D.get("serie_ue") or {}
    malas = []
    for u in U:
        esp = racha_esperada((SU.get(u["sec"]) or {}).get("DEVENGADO", []), F, tol)
        pub = u.get("sin_aumento")
        if (esp is None) != (pub is None) or (esp and (pub["n"], pub["desde"], pub["minimo"]) != esp):
            malas.append(f"{u['sec']} (publicado {None if pub is None else (pub['n'], pub['desde'], pub['minimo'])}, esperado {esp})")
    ok("Racha de cortes sin aumento neto reproducida desde el historial de cada unidad", not malas, "; ".join(malas[:4]))
    # Con aumento en el último corte la racha es 0. Lo contrario (racha 0 sin aumento) solo se admite cuando el último periodo no
    # tuvo días hábiles (fin de semana o feriado): ese corte no se cuenta.
    hab0 = (D.get("comparacion") or {}).get("habiles") == 0
    malas = [u["sec"] for u in U if u.get("sin_aumento") and ((u["estado"] == "aumento" and u["sin_aumento"]["n"] != 0)
                                                           or (u["estado"] != "aumento" and u["sin_aumento"]["n"] == 0 and not hab0))]
    ok("Una unidad con aumento en el último corte tiene 0 cortes sin aumento, y viceversa", not malas, ", ".join(malas))
    return res


def main():
    eid = sys.argv[1] if len(sys.argv) > 1 else "minsa"
    base = os.path.join(RAIZ, eid, "ro")
    D = json.load(open(os.path.join(base, "datos.json"), encoding="utf-8"))
    fp = os.path.join(base, "proyectos.json")
    PJ = json.load(open(fp, encoding="utf-8")) if os.path.isfile(fp) else None
    res = verificar(D, PJ)
    falla = [r for r in res if not r[0]]
    for b, regla, det in res:
        print(("OK    " if b else "FALLA ") + regla + (f" · {det}" if det else ""))
    print(f"{len(res) - len(falla)} de {len(res)} verificaciones conformes")
    sys.exit(1 if falla else 0)


if __name__ == "__main__":
    main()
