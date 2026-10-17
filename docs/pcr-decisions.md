# Decisiones de diseño PCR

Registro acumulativo de decisiones funcionales sobre la materialización,
interpretación y presentación de resultados PCR. Cada nueva decisión debe
añadirse como una entrada fechada, sin reescribir las anteriores.

## 2026-10-17 — Herencia retrospectiva por organismo original

La política `pcr-sil-linking-v3` conserva todos los aislados relevantes de la
muestra original y busca una única muestra previa compatible con cualquiera
de sus códigos, dentro de los límites configurados. Hereda los marcadores
por coincidencia exacta de organismo para todos los aislados originales;
los organismos ausentes quedan `0000` y no continúan su propia búsqueda.
La conciliación global agrega solo esta herencia. Se excluyen los marcadores
de organismos presentes exclusivamente en la muestra previa. Si no hay
muestra previa seleccionada, se utiliza la evidencia directa por organismo.
Los perfiles directos permanecen separados de los reconciliados.

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

## 2026-08-05 — Control negativo positivo de 16S

Para la diana `16S`, un pozo clasificado como control negativo con llamada
`POSITIVE` se conserva como advertencia `NEGATIVE_CONTROL_POSITIVE`, pero el
QC de esa diana se mantiene válido. No se aplica esta excepción a otras
dianas: en ellas la misma situación sigue invalidando el QC.

## 2026-08-05 — Alcance analítico de runs experimentales

Los runs no se eliminan por antigüedad. `pcr_run_scope` registra explícitamente
si un run es `EXPERIMENTAL` o `ROUTINE`, junto con el motivo, autor y momento
de asignación. Cada derivación aplica automáticamente el corte configurado en
`PCR_EXPERIMENTAL_BEFORE` (o recibido como parámetro del trabajo) y lo guarda
en los parámetros de la derivación. Si no se configura, el corte por defecto
es `2023-07-01T00:00:00`, que deja como experimentales todos los runs hasta el
30 de junio de 2023 incluido. La materialización PCR aplica esa misma política
tras promover `run_started`. La variable usa una fecha/hora ISO-8601, por
ejemplo `YYYY-MM-DDTHH:MM:SS`; el límite es estricto (`run_started < corte`).
La sincronización escribe ambos alcances de forma explícita: los runs desde el
corte incluido quedan registrados como `ROUTINE`.

Los runs `EXPERIMENTAL` conservan evidencia raw y materializada, pero quedan
fuera de la derivación analítica, selección de resultados y vistas de
resultados operativos. Se puede corregir cada run de forma auditable mediante
la API de repositorio de alcance.
