# Plan e informe de implementación: downstream, seudonimización y episodios

## Objetivo y estado

Este documento comenzó como plan de trabajo y se conserva como registro de sus
guardarraíles y alcance. La implementación está hecha para el alcance
downstream, pseudonimización persistente, episodios longitudinales y
exportación pseudonimizada. El estado exacto de tablas, vistas, comandos y
límites conocidos está en la [guía del modelo downstream](downstream-model.md).

No todo el análisis estadístico propuesto se trasladó al repositorio de datos:
la selección de cohortes, los resúmenes clínicos por episodio, las estimaciones
y las figuras en Quarto permanecen en el repositorio del paper. Este plan se
originó en una valoración estática de los scripts R y del código del proyecto;
no se ejecutó R ni se consultaron datos reales.

El código R pertenece a otro repositorio. El estado actual no depende de que
ese código esté presente en este repositorio.

## 1. Inspeccionar el repositorio y fijar el alcance

- Leer `AGENTS.md` y comprobar la rama, el estado del índice y los cambios
  existentes. Trabajar en una rama `codex/<feature>` y conservar el trabajo
  previo del usuario.
- Revisar esquema, derivaciones PCR–SIL, exportaciones y pruebas antes de
  diseñar migraciones.
- No modificar, stagear, incorporar a commits ni eliminar `R/` durante esta
  implementación. Stagear mediante rutas explícitas; evitar `git add .` y
  equivalentes.
- No acceder a `data/`, volcados, exportaciones, bases reales, variables de
  entorno ni `external/`. No conectar a PostgreSQL o MySQL sin autorización
  explícita y específica.
- La SQLite de seudonimización existente requiere autorización explícita
  antes de inspeccionarla o importarla. La mención de su existencia no
  constituye autorización de acceso.
- Utilizar bases temporales y datos ficticios para las pruebas.

## 2. Documentar claves, granularidades y cardinalidades

Identificar las claves internas de paciente, muestra, análisis, aislado,
microorganismo, run, interpretación y derivación. Revisar especialmente:

- `v_downstream_sil_pcr_global` y `v_downstream_sil_pcr_organism`.
- Resúmenes directos por aislado, microorganismo y cultivo.
- Candidatos retrospectivos y enlaces PCR–SIL.
- Selección de resultados y métricas de Cq.
- Reconstrucciones y borrados que afectan a cada tabla.

Preparar una matriz de granularidades y cardinalidades esperadas. Cada
relación nueva debe indicar sus claves, procedencia y multiplicidad permitida.
Usar claves internas y el contexto de derivación necesario para impedir
cruces entre versiones. El contrato de pseudonimización usa NHC y número de
muestra como identificadores globales dentro de sus respectivos registros;
`source_id` conserva la procedencia de la primera asignación.

**Guardarraíl:** no resolver duplicados inesperados mediante `DISTINCT`,
`MAX`, primera fila o última fila sin justificar qué representan. Una
relación supuestamente única con varias correspondencias debe producir un
error o una incidencia auditable. Comprobar cardinalidades antes y después
de las uniones.

## 3. Fijar el contrato de evidencia

| Concepto | Significado |
| --- | --- |
| Resultado PCR | Resultado seleccionado para muestra, contexto y diana. |
| Evidencia SIL directa | Marcador observado en el cultivo actual. |
| Evidencia SIL retrospectiva | Marcador de una muestra previa aceptada por la política. |
| Perfil de cultivo | Unión de marcadores de sus microorganismos relevantes. |
| Perfil de microorganismo | Marcadores atribuibles a ese microorganismo. |
| Perfil de aislado | Marcadores del aislado concreto. |

En la exportación actual `SIL PCR organisms`, `oxa48/vim/kpc/ndm`
corresponden al cultivo global. Las funciones R de microorganismos los
utilizaban por defecto, con riesgo de atribuir a cada especie todos los
mecanismos del cultivo. Para caracterizar microorganismos deben utilizarse
`organism_*`, `direct_organism_*` o sus fuentes normalizadas, comprobando
su semántica exacta.

**Guardarraíl:** no distribuir el perfil global, actual o histórico, entre
todos los microorganismos. Una atribución retrospectiva por microorganismo
debe conservar la correspondencia aceptada y la evidencia concreta del
marcador. Mantener diferenciadas la conciliación global por cultivo y la
atribución a un microorganismo.

## 4. Implementar el registro persistente de seudonimización

Crear una capa operativa independiente de las derivaciones reconstruibles.
Mantendrá correspondencias para pacientes y muestras, con identificador
original, seudónimo, fecha de alta y procedencia de la asignación.

Los identificadores originales son globales dentro de cada tipo: el mismo NHC
o número de muestra conserva el mismo seudónimo aunque aparezca en distintas
fuentes. `source_id` registra la procedencia de la primera asignación. Pacientes
y muestras mantienen registros y secuencias independientes.

Requisitos obligatorios:

- El mismo identificador conserva siempre el mismo seudónimo.
- Los identificadores nuevos reciben números nuevos.
- Las ausencias en un reproceso no eliminan correspondencias.
- Los números no se reutilizan.
- Los identificadores se tratan como texto, conservando ceros iniciales.
- Las asignaciones son transaccionales y seguras ante concurrencia.
- Las restricciones de unicidad existen también en la base de datos.
- Los seudónimos no dependen del orden de las filas del dataset.

Validar la relación muestra–paciente contra la fuente normalizada. Si una
muestra registrada aparece asociada a otro paciente, registrar el conflicto
y detener esa asignación; no reasignarla silenciosamente. Completar una
asociación previamente desconocida debe estar expresamente admitido y
auditado. No asignar un seudónimo común a identificadores ausentes.

## 5. Proteger el registro y preparar la migración existente

Revisar todas las rutas de borrado: reconstrucción PCR, reconstrucción Microb,
recreación del esquema y utilidades generales. Ninguna reconstrucción
ordinaria debe destruir correspondencias o reiniciar contadores.

Incorporar un comando específico de destrucción del registro, con intención
explícita y confirmación propia de una operación destructiva. Auditarlo en un
lugar que sobreviva al borrado del registro.

Preparar una importación de la SQLite existente que:

- Preserve exactamente los seudónimos anteriores.
- Valide duplicados, formatos y asociaciones muestra–paciente.
- Sea transaccional e idempotente.
- Sitúe la siguiente asignación después de todos los números existentes.
- Rechace conflictos sin sobrescribir correspondencias.
- No modifique ni elimine el origen.

**Guardarraíl:** no comenzar a asignar seudónimos de producción desde un
registro vacío si hay otro registro pendiente de migrar. Completar la
implementación con ejemplos ficticios; la importación real queda pendiente
de autorización de acceso a la SQLite.

## 6. Crear la derivación de episodios longitudinales

Separar este modelo de `pcr_episode`, que agrupa runs complementarios por
fecha y solapamiento de muestras.

Registrar versión del algoritmo, parámetros efectivos, ejecución, estado,
fechas y referencias a las derivaciones o fuentes utilizadas. Crear entidades
separadas para la ejecución de derivación, el episodio de paciente, la
pertenencia de muestras y, si procede, los resúmenes por diana.

La regla inicial será un máximo configurable de **30 días entre muestras
consecutivas del mismo paciente**. Un intervalo exactamente igual al umbral
continúa el episodio; uno superior inicia otro. Validar los parámetros y
persistir sus valores efectivos como se hace con la política retrospectiva.

Este criterio permite episodios cuya duración total supere 30 días. No
confundirlo con una ventana fija desde la primera muestra.

## 7. Definir tiempo, elegibilidad e identidad de episodios

Parametrizar y registrar la fecha clínica utilizada y sus alternativas.
Revisar las convenciones actuales: conciliación histórica y exportación no
necesariamente priorizan las mismas fechas. No copiar una prioridad
accidentalmente; documentar también el tratamiento de horas y días naturales.

Establecer una población elegible explícita, compatible con el alcance
analítico del proyecto. La falta de una diana no debe excluir una muestra
silenciosamente.

Guardarraíles obligatorios:

- Partir de una relación con una fila por muestra, sin multiplicación por
  microorganismos, dianas o runs.
- No agrupar pacientes sin identificador como un único paciente.
- Auditar las muestras sin fecha suficiente, sin inventar fechas.
- Resolver empates mediante un orden determinista.
- Separar identidad interna y seudónimo de presentación.
- Mantener una única pertenencia por muestra y derivación.
- Asociar los identificadores de episodio a su derivación: nuevas muestras
  o nuevos parámetros pueden fusionar o dividir episodios.

Construir episodios sobre la población completa elegible de esa derivación.
El filtro temporal del paper se aplicará después. Si se admite una ventana
de construcción, debe quedar registrada porque cambia los límites.

## 8. Resumir episodios y exportar su pertenencia

Conservar inicio, fin, número de muestras, separación entre muestras y motivo
de apertura. Por diana, exportar recuentos de positivos, negativos, inválidos
y ausentes.

Definir expresamente cualquier categoría resumida. Un positivo permite
afirmar que existe algún positivo observado; declarar un episodio negativo
requiere una política de evaluabilidad. Sin positivos, pero con resultados
desconocidos, no clasificar automáticamente como negativo completo.

No interpretar el tiempo entre primer y último positivo como duración
demostrada de colonización continua. Exportar las fechas observadas y
reservar las estimaciones clínicas al análisis correspondiente.

Exportar **episodios de pacientes** y **muestras por episodio**, con identidad
de derivación y parámetros en el informe de exportación.

## 9. Construir la comparación por muestra y diana

Crear una vista downstream con una fila por muestra y diana canónica:

- Resultado FIS canónico y resultados por fase.
- Estado de validez y disponibilidad.
- Evidencia SIL directa y conciliada.
- Tipo y procedencia de evidencia retrospectiva.
- Estado de comparación y motivo de no evaluabilidad.
- Métrica Cq y referencia 16S asociadas al resultado correspondiente.

Reutilizar las reglas actuales de `fis_*`; no reconstruirlas desde
`*_specific` mediante conversiones independientes de ausencias a cero.

**Guardarraíl:** comprobar que métrica y resultado seleccionado proceden de
interpretaciones y runs compatibles. No asociar automáticamente la última
métrica disponible con cualquier resultado promovido. Conservar las claves
necesarias para verificarlo y mantener la referencia 16S en el contexto
definido por la política actual, sin imputaciones silenciosas.

Distinguir ausencia de marcador detectado, cultivo no procesado, cultivo
no clasificado y falta de evidencia evaluable. No reutilizar una única
etiqueta de concordancia para conceptos diferentes sin revisar su contrato.

Mantener el valor logarítmico y, si resulta útil, `10^valor`. Documentar
`100 × 10^valor` como una transformación de la métrica: el nombre
`relative_percent` no establece un porcentaje biológico calibrado.

## 10. Construir evidencia y perfiles por microorganismo

Crear una salida por muestra, microorganismo y diana, con evidencia directa
y atribuida separadas. Preservar muestra histórica, organismo coincidente
y referencias de aislados que aportan evidencia.

Construir después perfiles por microorganismo y recuentos. Usar códigos
catalogados como identidad; los nombres son descripciones. Auditar
correspondencias ausentes o ambiguas.

Validar obligatoriamente un cultivo con *E. coli* OXA-48 y *Klebsiella* KPC:

- El cultivo global contiene ambos mecanismos.
- Cada microorganismo conserva solo su evidencia correspondiente.
- La unión global no se distribuye a ambos organismos.
- Dos aislados de la misma especie no se cuentan como dos especies portadoras.
- Un perfil agregado por especie no demuestra que todos sus mecanismos
  coexistan en un mismo aislado.

## 11. Construir perfiles y arquitectura por muestra

Derivar perfiles FIS y SIL, mecanismos exclusivos de cada fuente y relación
entre perfiles. Mantener separados los perfiles SIL directo y conciliado.

Exportar recuentos inequívocos de microorganismos distintos, aislados y
asociaciones microorganismo–mecanismo. Las etiquetas de arquitectura deben
indicar a cuál de estas unidades se refieren.

Validar el orden OXA-48–VIM–KPC–NDM y los estados de cada diana antes de
construir códigos binarios. Conservar explícitamente inválidos y desconocidos;
no generar perfiles negativos por defecto. Las frecuencias, porcentajes y
contrastes estadísticos seguirán calculándose en el paper.

## 12. Integrar vistas, seudónimos y exportaciones

Conservar claves internas en las capas de cálculo. Utilizar seudónimos en las
salidas que los requieran; no exportar el registro de correspondencias en los
downstream ordinarios.

Crear vistas sobre las fuentes existentes cuando sea suficiente. Añadir
persistencia cuando haya una política independiente que versionar o una
necesidad concreta de rendimiento.

Salidas previstas, con nombres orientativos:

| Salida | Granularidad |
| --- | --- |
| PCR SIL target comparison | Muestra × diana. |
| SIL organism target evidence | Muestra × microorganismo × diana. |
| SIL organism profiles | Muestra × microorganismo; posible ampliación de la salida existente. |
| PCR SIL sample profiles | Muestra. |
| Patient episodes | Episodio longitudinal dentro de una derivación. |
| Patient episode samples | Pertenencia de muestra a episodio dentro de una derivación. |

Mantener compatibles las exportaciones actuales salvo cambios expresamente
documentados. Todas las hojas deben corresponder a la misma derivación o
combinación registrada de derivaciones; evitar mezclas si cambia la vigente
durante la lectura. Registrar la versión del contrato de exportación.

Documentar granularidad, claves, estados, procedencia y semántica temporal.
La clasificación externa de unidades y servicios queda pendiente de decidir
si será un catálogo compartido. No incorporar su CSV sin autorización.

## 13. Validar y entregar

Priorizar pruebas con datos ficticios que verifiquen:

- Reproceso, concurrencia, importación y conservación de seudónimos.
- Supervivencia del registro ante todas las reconstrucciones generales.
- Conflictos de identidad y conservación de ceros iniciales.
- Intervalos inferiores, iguales y superiores al umbral de episodios.
- Fechas ausentes, empates y llegada de muestras intermedias.
- Ausencia de multiplicación por microorganismos, dianas o runs.
- Separación de evidencia directa e histórica con perfiles diferentes.
- Cultivos negativos, no procesados y no clasificados.
- Resultados parciales, inválidos y desconocidos.
- Correspondencia entre resultado seleccionado, Cq y referencia 16S.
- Consistencia entre hojas de exportación y contexto de derivación.

Revisar el diff y el índice mediante rutas explícitas, comprobando que `R/`
no se haya incorporado y que no se revierta trabajo previo del usuario.

Entregar cambios, comandos nuevos, migraciones, contratos downstream y
validaciones realizadas. Señalar por separado la importación real pendiente
del registro SQLite y las decisiones funcionales aún sin resolver.

## Límites del traslado desde R

Conservar en el paper el periodo de estudio, filtros de inclusión, primera
muestra por paciente, edad basal de la cohorte, grupos de edad, resúmenes,
denominadores, métricas de rendimiento, coocurrencias, contrastes,
regresiones, ajustes de multiplicidad y presentación en Quarto.

La edad por muestra ya existe en el downstream; la edad en la primera muestra
del paciente dentro del estudio es otra variable. Mantener ambas semánticas
explícitas.

No trasladar literalmente las reglas de `R/legacy`: incluye sustitución de
referencias 16S por medianas, selección del último run, normalización de
ausencias a `0000` y limpiezas que el proyecto actual ya aborda mediante
políticas trazables.

## Referencias del proyecto

- [README](../README.md).
- [Modelo PCR](pcr-tables.md).
- [Revisión PCR](pcr-review.md).
- [Decisiones PCR](pcr-decisions.md).
- [Conciliación PCR–SIL](pcr_sil_linking.md).
- [Esquema y vistas](../src/fis_data/schema.py).
- [Exportaciones downstream](../src/fis_data/etl/exports/downstream.py).
- [Derivaciones PCR](../src/fis_data/etl/pcr/analytics/).

