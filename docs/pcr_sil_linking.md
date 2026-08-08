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
| `CULTURE_POSITIVE` | Hay al menos un aislado Microb con `microorganism_code` no nulo. |
| `CULTURE_NEGATIVE` | No hay aislado y el resultado bacteriológico está mapeado como cultivo negativo. |
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
carbapenemasa positiva (`OXA`, `KPC`, `NDM` o `VIM`). Para un microorganismo
actual ya caracterizado no se realiza búsqueda retrospectiva. La búsqueda se
reserva para microorganismos positivos sin caracterización de BLEE ni de
carbapenemasa.

## Búsqueda retrospectiva

Se activa solo cuando la muestra SIL actual es `CULTURE_POSITIVE`, contiene
microorganismo y dicho aislado está sin caracterizar. La búsqueda se limita a
muestras anteriores que cumplan todas estas condiciones:

- Pertenecen al mismo paciente.
- Son anteriores por fecha clínica (`received_at`, con `entered_at` como
  respaldo si fuera necesario).
- Son tipo de muestra `068`.
- Tienen análisis `PMDR` de clase `BACTERIOLOGICAL`.
- Contienen el mismo `microorganism_code` exacto. No se aplican equivalencias
  taxonómicas ni comparaciones por nombre libre.

Los parámetros serán versionados y configurables:

| Parámetro | Valor por defecto | Efecto |
|---|---:|---|
| `previous_sample_max_days` | 90 | Distancia máxima entre la muestra actual y la previa. |
| `previous_sample_max_negatives` | 3 | Máximo de cultivos negativos intermedios que se pueden atravesar. |

Las muestras previas `CULTURE_NEGATIVE` incrementan el contador. Las
`CULTURE_NOT_PROCESSED` no se convierten en positivas ni aportan evidencia; se
mantiene su trazabilidad y la búsqueda puede continuar. Si se supera el máximo
de negativos, se excede la ventana temporal, desaparece el microorganismo o
no hay muestra previa, la búsqueda termina sin herencia.

La primera evidencia previa compatible con un marcador de carbapenemasa
positivo será la candidata heredada. BLEE sirve para impedir la búsqueda en el
aislado actual, pero no sustituye por sí sola una evidencia de OXA/KPC/NDM/VIM
para conciliar un target PCR.

## Persistencia y auditoría propuestas

La implementación deberá mantener candidatos y decisión final separados:

- `pcr_sil_link_candidate`: evidencia directa o previa, muestra origen,
  muestra evaluada, aislado, microorganismo, marcador, días de distancia,
  negativos atravesados y motivo de aceptación/rechazo.
- `pcr_sil_result_link`: enlace elegido por resultado PCR/target, con tipo
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
