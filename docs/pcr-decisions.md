# Decisiones de diseño PCR

Registro acumulativo de decisiones funcionales sobre la materialización,
interpretación y presentación de resultados PCR. Cada nueva decisión debe
añadirse como una entrada fechada, sin reescribir las anteriores.

## 2026-07-30 — Modelo y resultados vigentes

Las vistas se gestionan junto al esquema de la base de datos. La salida
algorítmica se expone como `v_pcr_algorithmic_result`; la salida operativa,
incluidos los overrides manuales activos, se expone como
`v_pcr_effective_result`.

Los resultados vigentes se seleccionan de forma independiente por muestra,
tipo de run (`SCREENING` o `SPECIFIC`) y diana, para que ambos tipos puedan
coexistir.

## 2026-07-30 — Metadatos y controles

`run_started` y `run_ended` se promueven desde los atributos CFX a `pcr_run`
como fechas normalizadas. Las etiquetas de control pueden proceder de `Sample`
o, si este falta, de `Content`; un `Content` de control sobre una muestra real
queda como advertencia.

En Screening los controles son globales: basta un control positivo positivo y
todos los controles negativos deben ser negativos. En ensayos específicos el
QC sigue evaluándose por diana.

## 2026-07-30 — Cq y melting incompatibles

Un Cq válido no se descarta por un melting incompatible. Se conserva con
llamada `INDETERMINATE`, error `MELTING_INCOMPATIBLE`, el pico observado y la
diana compatible más cercana dentro de ±5 °C, cuando exista.

## 2026-07-30 — Un target `FAIL` no invalida el run completo

Los targets de origen que contienen `FAIL` se canonizan como `FAIL` y solo
anulan la interpretación de la fila muestra–diana correspondiente. La llamada
es `INDETERMINATE` y el error trazable es `TARGET FAILED`.

No se cambia la clase global del run, no se invalidan las otras dianas, no se
fuerza el QC del run y el run conserva su participación normal en episodios y
selección de resultados.

## 2026-07-30 — Evidencia explícita de QC por diana

Cada QC de run–diana conserva las interpretaciones de controles que utilizó en
`pcr_target_qc_evidence`. La vista `v_pcr_target_qc_evidence` permite navegar
desde el QC hasta pozo, Cq, melting, pico, llamada y error sin reconstruir la
relación por joins manuales.

## 2026-07-30 — Compatibilidad diagnóstica de melting

La compatibilidad de un pico con una diana usa la misma ventana configurada
para la llamada analítica (actualmente ±0.5 °C), no una tolerancia diagnóstica
de ±5 °C. `metrics_json.compatible_targets_within_window` lista todas las
dianas dentro de esa ventana.

En Screening, un control positivo con Cq válido y pico fuera de todas las
ventanas se señala como `POSITIVE_CONTROL_MELTING_INCOMPATIBLE`, no como un
control simplemente no positivo.
