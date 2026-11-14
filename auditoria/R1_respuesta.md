# Respuesta a la auditoría técnica independiente — Revisión 1

- Informe auditado: «Auditoría técnica independiente — Ministro al Día», revisión 1, 13/11/2026.
- Commit auditado: `49c486e3c0b87eca1ffb76736e634c4a203ab278` (rama `main`).
- Correcciones: rama `auditoria-r1-correcciones`, en el commit que agrega este archivo. **No toca la página pública**: GitHub Pages publica `main` y las tareas programadas solo corren en `main`.
- Alcance: hallazgos prioritarios M01–M08 (capítulo 2). Además, arreglos puntuales de M09–M12 que eran de bajo costo. El resto de M09–M12 sigue pendiente (ver abajo).

## Cómo reproducir

```
python -X utf8 herramientas/pruebas_control.py              # 37 pruebas sin red (corre también en la tarea, antes de calcular)
python -X utf8 herramientas/pruebas_control.py --interfaz   # +19 pruebas en Chromium (320/360/390/412/1280 px), requiere Playwright
python -X utf8 herramientas/verificar_control.py minsa      # 41 verificaciones del corte publicado
```

Resultado: 56 de 56 pruebas y 41 de 41 verificaciones conformes, con Python 3.13 + pandas 3.0.5 y con Python 3.12 (la versión del workflow) + pandas 3.1.1.

Además, se sembraron errores en el generador y en el vigilante (ajuste tratado como «sin variación», racha que ignora huecos, vuelta del `except` silencioso, barrera desactivada, anterior = `fechas[-2]`, vigilante que calla fallos). En los seis casos fallaron entre 2 y 4 pruebas.

## Estado por hallazgo

| ID | Estado | Archivos | Prueba | Resultado | Limitación |
|---|---|---|---|---|---|
| M01 | Resuelto y verificado | `tablero_ro.py` (`clasificar_variacion`, `aplicar_actividad`), plantilla | `[M01]` ×2; interfaz `[M01/M06]` ×5 y `[M01/M10]` ×2 | −140 y −6 486,75 → «Ajuste neto negativo»; 0 → «Sin variación»; `None` → «Sin comparación». Tolerancia S/ 1 publicada en datos, pantalla y CSV. «Último día» → «Variación entre cortes» con las dos fechas y los días hábiles. | — |
| M02 | Resuelto y verificado | `tablero_ro.py` (`racha_sin_aumento`), plantilla, verificador | `[M02]` ×6 | «Días sin devengar» → «Cortes sin aumento neto». Si hay huecos o el historial empieza, se marca como mínimo («2+») y se dice desde qué corte. El caso 100 → 90 da 1 corte con 1 ajuste, ya no 9 días. | No se calcula una racha diaria: falta cobertura diaria válida. |
| M03 | Resuelto y verificado | `tablero_ro.py` (`esquema`, `leer_filas`, `monto`, `validar_montos`), `con_consulta_amigable` | `[M03]` ×7 | Columnas buscadas por nombre y lector `csv` (respeta comillas). Se rechaza el lote ante un importe no numérico, NaN o infinito, fila truncada, columna ausente o repetida, separador distinto o archivo sin filas. El rechazo deja el último corte válido (código 2, la corrida no publica). | El control de bytes recibidos contra `Content-Length` no se probó con el servidor real del MEF. La lectura F12B del núcleo privado no se revisó. |
| M04 | Parcial | plantilla, `tablero_ro.py` (`guardar_estado` → `estado.json`), `vigilar_mef.py` | `[M04]` ×3; interfaz `[M04]` ×5 | La página ya no deduce el estado del reloj. Muestra la corrida real de GitHub Actions (en curso / sin errores / no terminó bien) y `estado.json` (nuevo corte / cifras revisadas / consulta sin cambios / corte conservado, más cada fuente). «Próxima consulta prevista (aprox.)». Si no se pudo leer ninguna fuente, el vigilante termina con error. El aviso de recarga compara el corte y las revisiones, no `actualizado`. | La respuesta real de la API pública de GitHub solo se probó simulada. Sin autenticación, la API admite 60 consultas por hora y por IP: si no responde, la página lo dice y no afirma nada. Un fallo de publicación solo se ve por la API. |
| M05 | Resuelto y verificado | `tablero_ro.py` (`verificar_cronologia`, `anterior_a`, `ultimos_publicados`), workflow | `[M05]` ×3, incluida la aceptación obligatoria 10 → 12 → fallo → 11 | Antes de escribir cualquier archivo se compara el candidato con lo publicado y los historiales. Si es anterior: no se modifica nada, `estado.json` registra el motivo, la corrida no marca la señal como vista (reintenta) y el portal sigue en 12. El anterior es la mayor fecha estrictamente menor. | Falta una ruta explícita para revisiones históricas (hoy no hay ninguna). |
| M06 | Resuelto y verificado | plantilla | interfaz `[M06]` ×5; medición propia | Hasta 760 px el cuadro pasa a tarjetas (nombre, saldo, saldo proyectado, variación con fechas y estado), con opción «Ver cuadro completo». Hasta 900 px el gráfico pasa a una lista compacta (fechas, importe, días hábiles, % de lo necesario). El eje del escritorio va con signo. Texto mínimo de 12,5 px y sin desbordamiento global a 320, 360, 390 y 412 px, en horizontal (844 × 390) y con texto ampliado. El detalle muestra las tablas como filas. | No se probó en teléfonos físicos. |
| M07 | Parcial | plantilla | interfaz `[M07]` ×2 | Cabeceras con `<button>` y `aria-sort`. Filas y tarjetas responden a Enter y Espacio. Resultados del buscador con botón. Escape cierra el detalle y el foco vuelve al elemento de origen. Avisos con `aria-live`. | No se probó con un lector de pantalla. No se certifica WCAG. |
| M08 | Resuelto y verificado | `verificar_control.py` (reescrito), `pruebas_control.py` (nuevo), `pruebas/` | `[M08]` ×15 | Se quitaron las reglas tautológicas e incondicionales. Hay tres grupos: contrato (finitud, claves únicas, cronología, cobertura, concordancia de proyectos), cálculo y actividad. El estado y la racha se recalculan con una implementación independiente. 13 errores sembrados hacen fallar su regla. Las pruebas usan datos fijos (`pruebas/`), así no bloquean un corte sin ajustes. | — |
| M09 | Parcial | workflow, `vigilar_mef.py` | `[M04/M09]` | Permisos de solo lectura por defecto; solo `actualizar` escribe. `inputs.pliego`, el motivo y `rehacer_informe` se pasan por variables de entorno. La entidad se valida contra `pliegos.json`. | Pendiente: fijar SHA de las Actions, versiones y hashes de dependencias, `ref` del núcleo y registro de su commit. |
| M10 | Parcial | plantilla | interfaz `[M01/M10]` | El CSV neutraliza el texto que empieza con `= + - @` o un carácter de control; los importes negativos siguen como números. | Pendiente: CSP y cabeceras. |
| M11 | Parcial | `tablero_ro.py`, plantilla | revisión de código | Para adoptar el riesgo agregado de los proyectos se exige que cuadren el PIM **y** el devengado. La tabla por genérica muestra su propio corte cuando difiere del corte de la cabecera. | Sin prueba automática del caso «PIM coincide y devengado no». |
| M12 | Parcial | plantilla, `tablero_ro.py` | `[M04]` (textos) | «Si continúa el promedio de agosto, setiembre y octubre». Aviso visible: «Proyecto independiente. No es un portal oficial del MINSA». | Pendiente: parametrizar el calendario y el mes base, y versionar las revisiones por unidad. |

## Cambio de método

Versión `2026-11-14.1`, anotada en `minsa/ro/revisiones.json`. El corte publicado del 12/11/2026 se recalculó **sin consultar al MEF**, con `tablero_ro.py minsa --recalcular-actividad`.

- PIM, devengado, saldo, saldo proyectado y series quedan idénticos al commit auditado.
- Solo se sustituyen los campos de actividad: `ult`, `dias_sin` y `dias_sin_tipo` pasan a `var`, `estado`, `sin_aumento` y `comparacion`.

## Para probar con datos reales sin tocar el sitio

Ejecutar a mano el workflow «Control diario de Recursos Ordinarios» eligiendo la rama `auditoria-r1-correcciones`. El paso «Publicar» empuja a la rama donde corre la tarea, no a `main`.
