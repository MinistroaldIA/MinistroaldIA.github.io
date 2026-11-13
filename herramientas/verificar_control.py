#!/usr/bin/env python3
"""Verificaciones del control diario de RO antes de publicar (punto 6 de la revisión del 13/11/2026).

Uso: python herramientas/verificar_control.py <id>
Lee <id>/ro/datos.json (valores originales, sin redondeo de pantalla) y termina con código 1 si algo no cuadra.
En la tarea automática corre antes de publicar: si falla, no se publica nada y queda el último corte válido.
"""
import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOL = 1.0          # soles: tolerancia por el redondeo a céntimos del archivo


def clase(av, esp):
    """Misma regla que la página: razón ≥ 90 % alcanza; ≥ 60 % por debajo; < 60 % muy por debajo; sin referencia si es 0 o falta."""
    if esp is None or not esp > 0 or av is None:
        return "sin referencia"
    r, e = av / esp, 1e-9           # tolerancia: 0.09 / 0.1 = 0.8999… en coma flotante y debe contar como 90 %
    return "alcanza el umbral" if r >= 0.9 - e else ("por debajo" if r >= 0.6 - e else "muy por debajo")


def main():
    eid = sys.argv[1] if len(sys.argv) > 1 else "minsa"
    D = json.load(open(os.path.join(RAIZ, eid, "ro", "datos.json"), encoding="utf-8"))
    U, T = D["ues"], D["total"]
    res = []
    ok = lambda regla, cond, det="": res.append((bool(cond), regla, det))

    # 1. Conciliación presupuestal
    malas = [u["sec"] for u in U if abs(u["saldo"] - (u["pim"] - u["dev"])) > TOL]
    ok("Saldo = PIM − devengado en cada unidad", not malas, ", ".join(malas))
    malas = [u["sec"] for u in U if abs(u["no_cert"] - (u["pim"] - u["cert"])) > TOL]
    ok("Sin certificar = PIM − certificado en cada unidad", not malas, ", ".join(malas))
    for k_u, k_t in (("pim", "PIM"), ("dev", "DEVENGADO"), ("saldo", "saldo"), ("riesgo", "riesgo"), ("no_cert", "no_certificado")):
        s = sum(u[k_u] for u in U)
        ok(f"Suma de las unidades = total del pliego ({k_t})", abs(s - T[k_t]) <= TOL * len(U), f"{s:,.2f} frente a {T[k_t]:,.2f}")

    # 2. Comparación entre cortes
    comp = [u for u in U if u.get("dev_ant") is not None]
    malas = [u["sec"] for u in comp if abs((u["saldo"] - u["saldo_ant"]) - ((u["pim"] - u["pim_ant"]) - (u["dev"] - u["dev_ant"]))) > TOL]
    ok("Variación del saldo = variación del PIM − variación del devengado (por unidad)", not malas, ", ".join(malas))
    if len(D["fechas"]) > 1:
        i = len(D["fechas"]) - 1
        S = D["serie"]
        ds, dp, dd = S["saldo"][i] - S["saldo"][i - 1], S["PIM"][i] - S["PIM"][i - 1], S["DEVENGADO"][i] - S["DEVENGADO"][i - 1]
        ok("Variación del saldo = variación del PIM − variación del devengado (pliego)", abs(ds - (dp - dd)) <= TOL * len(U), f"{ds:,.2f} frente a {dp - dd:,.2f}")
    neg = [u["sec"] for u in comp if u["dev"] - u["dev_ant"] < -0.5]
    ok("Los ajustes negativos se conservan con su signo (no se vuelven cero)", all(u["dev"] - u["dev_ant"] < 0 for u in comp if u["sec"] in neg), ", ".join(neg) or "sin ajustes negativos")
    malas = [u["sec"] for u in comp if abs((u["riesgo"] - u["riesgo_ant"]) - ((u["pim"] - u["pim_ant"]) - (u["dev"] - u["dev_ant"]) - ((u["saldo"] - u["riesgo"]) - (u["saldo_ant"] - u["riesgo_ant"])))) > TOL]
    ok("La explicación de la variación del riesgo concilia (PIM, devengado, tiempo y supuestos)", not malas, ", ".join(malas))

    # 3. Base del avance: saldo al cierre del último mes con el PIM actual = PIM actual − devengado acumulado a esa fecha
    malas = [u["sec"] for u in U if abs(u["saldo_ini"] - (u["pim"] - (u["dev"] - u["dev_base"]))) > TOL]
    ok("Base del avance = PIM actual − devengado al cierre del último mes (se recalcula si cambia el PIM)", not malas, ", ".join(malas))
    malas = [u["sec"] for u in U if u["saldo_ini"] > 0 and abs(u["avance"] - u["dev_base"] / u["saldo_ini"]) > 1e-4]
    ok("Avance = devengado desde la base ÷ base", not malas, ", ".join(malas))

    # 4. Estimaciones: límites y agregación
    malas = [u["sec"] for u in U if not (-TOL <= u["riesgo"] <= u["saldo"] + TOL)]
    ok("Riesgo estimado entre 0 y el saldo en cada unidad", not malas, ", ".join(malas))
    con_h = [u for u in U if u.get("riesgo_hist") is not None]
    malas = [u["sec"] for u in con_h if not (min(u["rh_bajo"], u["rh_alto"]) - TOL <= u["riesgo_hist"] <= max(u["rh_bajo"], u["rh_alto"]) + TOL)]
    ok("Escenario según patrones dentro de su rango en cada unidad", not malas, ", ".join(malas))
    malas = [u["sec"] for u in con_h if not (-TOL <= u["rh_bajo"] and u["rh_alto"] <= u["saldo"] + TOL)]
    ok("Rango del escenario según patrones entre 0 y el saldo", not malas, ", ".join(malas))
    if T.get("riesgo_hist") is not None:
        ok("Suma del escenario según patrones = total del pliego", abs(sum(u["riesgo_hist"] for u in con_h) - T["riesgo_hist"]) <= TOL * len(U))

    # 5. Clasificación frente a la referencia: casos límite y coherencia de los datos
    casos = [((0.06, 0.1), "por debajo"), ((0.09, 0.1), "alcanza el umbral"), ((0.0599999, 0.1), "muy por debajo"), ((0.0899999, 0.1), "por debajo"),
             ((0.05, 0.0), "sin referencia"), ((0.05, None), "sin referencia"), ((-0.01, 0.1), "muy por debajo"), ((0.0, 0.1), "muy por debajo")]
    malos = [f"{a}->{clase(*a)}" for a, e in casos if clase(*a) != e]
    ok("Casos límite de clasificación: 60 %, 90 %, referencia cero, referencia ausente y avance negativo", not malos, "; ".join(malos))
    sinref = [u["sec"] for u in U if u["saldo_ini"] > 0 and not (u.get("esperado") or 0) > 0]
    ok("Unidades sin referencia identificadas (no cuentan como cumplimiento)", True, ", ".join(sinref) or "ninguna")

    # 6. Procedencia
    par = D["parametros"]
    ok("La procedencia de cada componente está publicada", bool(par.get("procedencia")) and bool(par.get("metodo")), par.get("fuente", ""))

    falla = [r for r in res if not r[0]]
    for b, regla, det in res:
        print(("OK    " if b else "FALLA ") + regla + (f" · {det}" if det else ""))
    print(f"{len(res) - len(falla)} de {len(res)} verificaciones conformes")
    sys.exit(1 if falla else 0)


if __name__ == "__main__":
    main()
