# Conciliación retrospectiva PCR–SIL

## Estado y alcance

Este documento define la política clínica implementada para enlazar resultados
PCR con el SIL. El algoritmo no modifica resultados PCR ni Microb: genera una
capa derivada, versionada y reconstruible de clasificación, candidatos y
enlaces.

La población de partida serán exclusivamente los pozos PCR de la derivación
actual con `pcr_microb_well_validation.validation_status = 'MATCH_ELIGIBLE'`.
Por tanto, cada búsqueda parte de una muestra con coincidencia exacta PCR–SIL,
tipo de muestra `068` y análisis `PMDR` bacteriológico ya validados. No se
iterará sobre el conjunto completo de Microb: en la carga actual el orden de
magnitud de partida es el de las aproximadamente 16.800 muestras PCR reales
validadas.

La búsqueda se ejecutará para esas muestras independientemente del resultado
PCR. La PCR no decide si se busca o no evidencia histórica; la decisión
depende exclusivamente del contexto microbiológico de la muestra SIL actual.

## Clasificación de cultivo

Se añadirá una categorización derivada por muestra/análisis PMDR:

| Categoría | Criterio |
|---|---|
| `CULTURE_POSITIVE` | Hay al menos un aislado Microb relevante con `microorganism_code` no nulo. |
| `CULTURE_NEGATIVE` | No hay aislado relevante y el resultado bacteriológico está mapeado como cultivo negativo, o los únicos aislados son irrelevantes para SIL--FIS. |
| `CULTURE_NOT_PROCESSED` | No hay aislado y el resultado está mapeado como no procesado. |
| `CULTURE_UNCLASSIFIED` | No se puede clasificar de forma segura; se audita y no se fuerza una categoría clínica. |

La configuración inicial de códigos bacteriológicos será:

```python
POSITIVE_CULTURE_CODES = ["CO1", "AIS"]
NEGATIVE_CULTURE_CODES = ["NEG"]
NON_PROCESS_CULTURE_CODES = [".04", ".05", "1", "C06", "NPR", "PEN"]
```

La lista efectiva debe ser una configuración versionada de códigos, no una
regla basada en buscar texto en descripciones. Así se podrán ampliar códigos
históricos sin cambiar el algoritmo.

Para SIL--FIS se excluyen por nombre catalogado los patrones `Candida`,
`Enterococcus`, `Stenotrophomonas` y `Acinetobacter`, sin distinguir
mayúsculas/minúsculas. No se escriben en los candidatos, resúmenes ni tabla por
microorganismo de SIL--FIS. Si coexisten con aislados relevantes, estos últimos
son los únicos que se conservan; si son los únicos aislados, el cultivo se
clasifica como `CULTURE_NEGATIVE` y se comporta como si no hubiera aislamiento.

Una muestra actual `CULTURE_NEGATIVE` o `CULTURE_NOT_PROCESSED` conserva esa
codificación y no inicia ninguna búsqueda retrospectiva.

## Caracterización por antibiograma

La caracterización del aislado se obtiene de
`microb_susceptibility_result`, no de textos libres. Los códigos presentes en
`antibiotico.txt` son:

| Significado | `antibiotic_code` |
|---|---|
| BLEE | `BLE` |
| OXA-48 | `OXA` |
| KPC | `KPC` |
| NDM | `NDM` |
| VIM | `VIM` |

`resantib.txt` define `P` como interpretación positiva y `N` como negativa.
Por ello, un marcador cuenta como detectado solo cuando el código anterior
tiene `interpretation_code = 'P'` (o su futura configuración equivalente).

Un aislado se considera **ya caracterizado** si tiene BLEE positiva o una
carbapenemasa positiva (`OXA`, `KPC`, `NDM` o `VIM`). La evidencia directa del
cultivo es una unión binaria de los marcadores positivos de todos sus aislados
relevantes: por ejemplo, *E. coli*/KPC y *Klebsiella*/NDM producen `KPC+NDM`.
El detalle conserva una fila por aislado; la especie no compite por la diana
PCR directa.

## Búsqueda retrospectiva

La política `pcr-sil-linking-v5` inicia la búsqueda solo en cultivos positivos
sin **ningún** aislado relevante con BLE/OXA/KPC/NDM/VIM e interpretación `P`.
Una sola caracterización directa bloquea toda búsqueda, también en cultivos
polimicrobianos. Se mantienen los códigos SIL originales sin equivalencias.
Los antecedentes deben ser del mismo paciente, anteriores por `received_at`
(o `entered_at`), de tipo `068` y análisis `PMDR` bacteriológico.

Los parámetros serán versionados y configurables:

| Parámetro | Valor por defecto | Efecto |
|---|---:|---|
| `previous_sample_max_days` | 90 | Distancia máxima entre la muestra actual y la previa. |
| `previous_sample_max_negatives` | 3 | Número de negativos acumulados que detiene la búsqueda (se atraviesan como máximo dos). |

Se recorren por fecha descendente (desempate por identificador descendente).
La primera condición de parada decide el resultado de toda la muestra:

- Fecha a más de 90 días: `WINDOW_EXCEEDED`, sin herencia; exactamente 90 se admite.
- Tercer negativo acumulado: `THREE_NEGATIVES`, sin herencia. El límite es
  `negative_count >= max_negatives`, no se atraviesa el tercer negativo.
- Positivo sin ningún código actual: `OTHER_ORGANISM`, sin herencia.
- Coincidencia exacta con carbapenemasa `P`: `CARBAPENEMASE`, con herencia.
- Coincidencia exacta con BLE `P` solamente: `BLEE`, sin herencia.

Una coincidencia sin caracterización positiva continúa, incluso si otra
especie del antecedente tiene carbapenemasas. Los no procesados no cuentan
como negativos ni detienen. BLE y carbapenemasa simultáneas permiten heredar
solo carbapenemasas positivas de los códigos coincidentes.

Se conserva **una única muestra histórica por cultivo**: la primera que
cumple una parada. En cultivos polimicrobianos se heredan los marcadores
positivos de todos los códigos actuales presentes en esa muestra; nunca los
de especies presentes únicamente en el antecedente. BLE de un código actual
detiene la búsqueda para todos, aunque otros sigan sin caracterización.
No se combinan historias independientes. Una política por especie queda
pendiente de decisión clínica explícita.

El resultado global reconciliado y la conciliación PCR–SIL se calculan como
OR de los marcadores heredados por los organismos originales. Los mecanismos
de otros organismos presentes solo en la muestra previa quedan excluidos.
Por ejemplo, para Klebsiella y E. coli actuales, una muestra previa con
Klebsiella OXA y Enterobacter NDM produce Klebsiella `1000`, E. coli `0000`
y global `1000` (orden OXA-48/VIM/KPC/NDM).

Si no procede la búsqueda o no se encuentra muestra compatible, se utiliza
la evidencia directa actual por organismo y su OR para el global. Cuando sí
se selecciona muestra previa, la herencia determina el perfil reconciliado;
los campos `direct_organism_*` conservan las observaciones actuales sin
sobrescribirlas. BLEE directa bloquea toda búsqueda y BLEE histórica coincidente la detiene;
ninguna aporta evidencia OXA/KPC/NDM/VIM.

Los perfiles son indicadores binarios de detección: un cero también puede
significar ausencia de evidencia positiva, no necesariamente una prueba
negativa explícita. El detalle de auditoría distingue organismos ausentes
de organismos coincidentes sin marcadores positivos.

Actualizar las vistas no regenera los candidatos de una derivación existente.
Si se mantiene una derivación `v2`, pueden faltar candidatos de otros códigos
compatibles de su muestra previa seleccionada. Para separar candidatos
incompletos, vistas desactualizadas y diferencias de granularidad, se puede
ejecutar manualmente
[`diagnose_organism_attribution.sql`](../scripts/postgres/diagnose_organism_attribution.sql)
antes de rederivar. Es una transacción de solo lectura que devuelve únicamente
metadatos y recuentos agregados. Compara con las fuentes actuales, por lo que
un cambio de fuentes desde la derivación también puede producir discrepancias.

## Persistencia y auditoría propuestas

La implementación deberá mantener candidatos y decisión final separados:

- `pcr_sil_isolate_marker_summary`: evidencia directa por aislado.
- `pcr_sil_retrospective_evidence`: única muestra previa elegida, o el motivo
  de no aplicar/no encontrar búsqueda.
- `pcr_sil_link_candidate`: relaciones entre aislados originales y aislados
  retrospectivos de igual código, con marcadores `ACCEPTED`. También conserva
  registros `NO_EVIDENCE` para aislados originales sin herencia positiva.
- `pcr_sil_result_link`: enlace por resultado PCR/target, con tipo
  `DIRECT`, `PRIOR_COMPATIBLE`, `NO_EVIDENCE`, `AMBIGUOUS` o `DISCORDANT`.

Ambas capas estarán asociadas a `derivation_id`, a la versión de la política y
a los identificadores PCR y Microb que las originan. Un enlace heredado nunca
sobrescribe la categoría ni los datos directos de la muestra actual.

## Rendimiento

La generación parte de la lista de muestras PCR validadas y busca candidatos
por paciente, fecha, tipo `068`, análisis `PMDR` y `microorganism_code`. Debe
resolverse de forma set-oriented en PostgreSQL, con índices sobre esas claves,
evitando tanto un recorrido fila a fila como un escaneo de los millones de
filas Microb.

## Ejecución

La conciliación se ejecuta como la última fase de `derive-pcr`, después de
seleccionar el resultado PCR. Los límites quedan registrados en los parámetros
de la derivación y pueden ajustarse sin cambiar código:

```bash
fis-data derive-pcr --algorithm-version melting-v1 --rebuild \
  --previous-sample-max-days 90 \
  --previous-sample-max-negatives 3
```

Las tablas derivadas son `pcr_sil_culture_classification`,
`pcr_sil_isolate_marker_summary`, `pcr_sil_retrospective_evidence`,
`pcr_sil_link_candidate` y `pcr_sil_result_link`. Un `--rebuild` de PCR o de
Microb las elimina junto con sus dependencias antes de recalcularlas.

Además, `pcr_sil_organism_marker_summary` conserva el perfil binario directo
por microorganismo y `pcr_sil_culture_marker_summary` realiza su unión binaria
por cultivo. Por ejemplo, aislados con `OXA`/`KPC` y `VIM` producen un único
perfil de cultivo `OXA`, `KPC`, `VIM`. Estas dos capas incluyen sólo evidencia
del cultivo actual; la evidencia histórica permanece diferenciada en los
candidatos retrospectivos.

Para cada target PCR, `pcr_sil_result_link` conserva `DIRECT`,
`PRIOR_COMPATIBLE`, `NO_EVIDENCE` o `DISCORDANT`. La concordancia sólo se
declara cuando hay evidencia SIL para el mismo marcador: una PCR positiva es
`CONCORDANT` y una PCR negativa `DISCORDANT`; los demás casos son
`NOT_ASSESSABLE`.

### Política conservadora y auditoría v5

La búsqueda solo comienza si **todos** los aislados relevantes del cultivo
carecen de BLE, OXA, KPC, NDM y VIM positivas. Una sola caracterización positiva
bloquea toda imputación; se conserva exclusivamente el perfil directo actual,
su unión global y la procedencia de la muestra actual. No hay imputación selectiva
ni procedencias mixtas. Las vistas y los enlaces también rechazan antecedentes
aceptados inconsistentes cuando existe caracterización directa (incluida BLE).
`0000` expresa ausencia de evidencia positiva, no negatividad demostrada.
La versión v5 cambia la selección histórica y el límite de negativos.
`evidence_status=ACCEPTED` exige carbapenemasa efectivamente recuperable.
Las paradas sin mecanismo conservan `NO_EVIDENCE`, el identificador de la
muestra que detuvo el recorrido, distancia, negativos y `reason`. El JSON
incluye `stop_reason`, `examined_count` y `discarded_uncharacterized_count`
(hasta la parada). El recorrido SQL
puede evaluar filas anteriores para calcular ventanas, pero ninguna posterior
a la primera parada puede seleccionarse. Ausencia de antecedentes queda como
`NO_EVIDENCE` sin muestra histórica (`NO_CHARACTERIZED_HISTORY`); la
caracterización directa se audita con `DIRECT_CHARACTERIZATION` y
`search_started=false`. Las categorías actuales no aplicables quedan como
`NOT_APPLICABLE`.

Los antecedentes coincidentes sin caracterización se descartan durante el
recorrido; no generan candidatos aceptados. `candidate_status=ACCEPTED`
requiere un marcador de carbapenemasa `P`. No se añaden columnas ni estados
incompatibles a las exportaciones. Al detenerse sin mecanismo, las vistas
mantienen `result_itself=true` y `result_sample` actual: la muestra de parada
solo figura en auditoría. `global_encoded` y `encoded_carba_org` pueden seguir
siendo `0000`, que expresa ausencia de detección positiva, nunca un
antibiograma negativo completo. Los enlaces quedan `NO_EVIDENCE` y la
concordancia `NOT_ASSESSABLE` para mecanismos no documentados.

Tras actualizar el código instalado, regenerar con `derive-pcr --rebuild` como
se indica arriba y repetir `export-downstream` para cada formato afectado.
La regeneración real requiere autorización y no forma parte de los tests sintéticos.

Comandos para el operador autorizado (sustituir URL y destino):

```bash
fis-data init-db --db-url '<URL_POSTGRESQL>'
fis-data derive-pcr --db-url '<URL_POSTGRESQL>' --algorithm-version melting-v1 --rebuild --previous-sample-max-days 90 --previous-sample-max-negatives 3
fis-data export-downstream --db-url '<URL_POSTGRESQL>' --format paper-analysis --output /ruta/conciliacion.xlsx
fis-data export-downstream --db-url '<URL_POSTGRESQL>' --format analytical-core-tables --output /ruta/tablas.xlsx
```

Conservar los límites de la derivación anterior si difieren de este ejemplo.
Verificar que los parámetros de la nueva derivación registran
`pcr-sil-linking-v5`; no comparar como equivalentes derivaciones v4 y v5.
Guardar las exportaciones anteriores antes del rebuild, ejecutar
`pseudonymize`, regenerar ambos formatos y repetir los análisis de concordancia.
Contar elegibles, paradas por `reason`, evidencias `ACCEPTED` y búsquedas sin
mecanismo; comparar con las 222 imputaciones y revisar la reclasificación de
los 594 antecedentes previos sin mecanismo. Estas cifras no son objetivos.
No ejecutar estos comandos sobre datos reales sin autorización.

En las vistas downstream, `result_itself` es siempre booleano: `false` si
se utiliza una muestra histórica aceptada y `true` si se utiliza la muestra
actual, incluidos cultivos negativos, no procesados o sin resumen de marcadores.
Este indicador expresa procedencia, no positividad ni validez del resultado.

`result_sample_pseudonym` acompaña a `result_sample` en la exportación e
identifica la misma muestra de evidencia (actual o histórica) mediante el
registro de pseudónimos. Si esa muestra no tiene pseudónimo registrado, queda
vacío; nunca se sustituye por el pseudónimo de otra muestra.

Después de derivar PCR, ejecutar `fis-data pseudonymize` antes de exportar.
También registra las muestras históricas aceptadas de las muestras PCR
elegibles actuales, aunque el antecedente no tenga PCR propio. Conserva las
asignaciones existentes y añade únicamente los identificadores que faltan.
