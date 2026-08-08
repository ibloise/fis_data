# Normalización Microb

El flujo Microb es:

```text
ingest-text → parse-microb → materialize-microb → normalize-microb
```

`materialize-microb` conserva la representación fiel del SIL en
`stg_microb_technique` y `stg_microb_antibiogram`. `normalize-microb` carga la
capa relacional `microb_*` respetando las claves naturales y usando los
catálogos actuales de `ref_microb_catalog`.

La capa normalizada incluye pacientes, muestras, análisis, resultados,
aislamientos, observaciones, determinaciones y susceptibilidades. Los códigos
originales se conservan junto con sus referencias al catálogo cuando existe
una coincidencia. Los códigos desconocidos se registran en
`microb_normalization_issue`; no se crean conceptos clínicos artificiales.

Un aislamiento solo se crea cuando `cod_microorganismo` tiene valor. Las filas
sin microorganismo pueden seguir creando análisis, resultados o determinaciones,
pero no se propagan como aislados ni como susceptibilidades.

La operación es repetible: las claves naturales y los `UPSERT` evitan duplicar
entidades al ejecutar de nuevo el comando. Las tablas staging siguen siendo la
fuente de trazabilidad mediante `microb_entity_lineage`, que conserva
`source_id`, `run_id`, `file_id`, `line_id`, tabla e identificador staging.

## Rendimiento

La carga procesa técnicas en lotes de 5.000 registros. En cada lote resuelve y
persiste primero pacientes, después muestras, análisis, resultados, aislados,
observaciones/determinaciones, susceptibilidades y finalmente lineage e
incidencias. Cada entidad se deduplica por su clave natural y se inserta con un
`UPSERT ... RETURNING` masivo; los antibiogramas se obtienen con una consulta
por lote, no una por técnica.

## Catálogos

El cargador reconoce los catálogos existentes en `var/microb_catalogs`:

```text
procedencias, servicon, muestras, localizacion, analisis,
analisis_sero, resantib, microorg, observacion, observamic,
tecnicas, rescualt, rescuant, antibiotico, cmis, procedencias_extra,
servicios_extra y hospitales
```

`rescualt` resuelve exclusivamente `cod_result_cualitativo`; `rescuant` resuelve
`cod_result_cuantitativo`. La determinación conserva ambos códigos además de
sus referencias de catálogo. Si un valor cuantitativo no existe en `rescuant`,
se conserva sin incidencia en `quantitative_result_literal`.

Los análisis se clasifican al resolver su código: `analisis` marca
`BACTERIOLOGICAL` y `analisis_sero` marca `SEROLOGICAL`. Esta marca queda en
`microb_sample_analysis.analysis_kind` y determina la resolución de
`cod_resultado`: los bacteriológicos usan `resultados`; los serológicos,
`rescualt`. No hay fallback entre esos dos catálogos. Si el código de
`cod_resultado` solo existe en el catálogo opuesto, se registra una incidencia
`WARNING` de resultado incompatible y no se asigna como resultado válido.

En análisis `SEROLOGICAL`, `cod_resultado` puede estar vacío: el resultado
clínico se expresa mediante `cod_result_cualitativo` y/o
`cod_result_cuantitativo`. Por ello no se registra una incidencia por un
`cod_resultado` nulo en serología. En análisis bacteriológicos (o sin clase
semántica conocida) ese campo continúa siendo obligatorio.

Esto no elimina el valor original: `microb_analysis_result` conserva el
`result_code` recibido, con `result_code_id` nulo, para permitir su auditoría.
Por ejemplo, el análisis serológico `A90` puede informar `N` en
`cod_result_cualitativo`, que se normaliza como `NEGATIVO` desde `rescualt`.
Si en la misma línea `cod_resultado` contiene `.03` (un código existente solo
en `resultados`), se conserva `.03` como trazabilidad pero genera el aviso de
incompatibilidad y no se interpreta como resultado serológico.

Solo los análisis `BACTERIOLOGICAL` pueden crear aislamientos. Incluso en ese
caso se exige un `cod_microorganismo` no vacío; las filas bacteriológicas sin
microorganismo conservan sus resultados, pero no se propagan como aislados.

Los códigos de observación se intentan resolver en `observacion` y
`observamic`. Si no existen en ninguno de ambos catálogos, se aceptan como
texto libre: se conserva el valor en `observation_code`, queda
`observation_id` nulo y se registra una incidencia informativa (`INFO`), sin
considerarlo un error de normalización.

Se cargan con:

```bash
fis-data load-microb-catalogs --catalog-dir var/microb_catalogs
```

Después se ejecuta `normalize-microb`.

Los códigos de procedencia y servicio se resuelven de forma secuencial: cada
campo consulta primero su catálogo semántico (`procedencias` o `servicon`) y,
si no encuentra el código, consulta el otro y después los catálogos auxiliares
de Excel `procedencias_extra` y `servicios_extra`. Cuando un código existe en
varios, prevalece siempre el catálogo primario del campo.

Los catálogos auxiliares y `hospitales` también permiten resolver
`microb_sample.hospital_id`. La relación se obtiene a partir del hospital
indicado para la procedencia o servicio; si no hay correspondencia, la muestra
se conserva con hospital nulo sin generar una incidencia.

## Rebuild

Para descartar completamente una carga normalizada anterior y recalcularla
desde staging, ejecuta:

```bash
fis-data normalize-microb --rebuild
```

El rebuild ejecuta un único `TRUNCATE ... RESTART IDENTITY` sobre las tablas
`microb_*` derivadas, por lo que es mucho más rápido que eliminar las filas una
a una y reinicia sus identificadores técnicos. También vacía explícitamente
`pcr_microb_well_validation`: esa auditoría depende de los identificadores de
las muestras Microb y quedaría obsoleta. Tras un rebuild, ejecuta de nuevo
`derive-pcr` si necesitas regenerarla.

Conserva `stg_microb_*` y los catálogos. No admite `--file-id`, porque una
reconstrucción parcial podría eliminar entidades compartidas entre ficheros.

## Conciliación PCR–Microb

Cada derivación PCR crea una auditoría por pozo en
`pcr_microb_well_validation`. Solo los pozos `REAL_SAMPLE` sin motivo de
revisión se cruzan por coincidencia textual exacta entre `pcr_sample.sample_key`
y `microb_sample.sample_number`. Los controles y pozos no-muestra se registran
como `CONTROL_EXCLUDED`; no se confunden con muestras reales sin coincidencia,
que reciben `REAL_SAMPLE_NO_MATCH`.

Un match es `MATCH_ELIGIBLE` únicamente si la muestra Microb tiene tipo `068`
(RECTAL) y dispone de un análisis `PMDR` de clase `BACTERIOLOGICAL`. Los estados
`MATCH_INVALID_SPECIMEN`, `MATCH_INVALID_ANALYSIS` y
`MATCH_INVALID_SPECIMEN_AND_ANALYSIS` conservan la razón en la auditoría, pero
se excluyen de episodios y selección de resultados PCR vigentes. Los estados
ambiguos quedan auditados para una futura conciliación manual; no se crea aún
un enlace manual persistente.

La validación se calcula únicamente sobre los `sample_key` PCR presentes en la
derivación y usa índices por número de muestra y análisis Microb. Así evita
recorrer o multiplicar el conjunto completo de análisis Microb en cada
derivación PCR.
