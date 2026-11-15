# Modelo downstream, seudonimización y episodios

Este documento describe las funcionalidades downstream implementadas en el
repositorio FIS Data. Separa las transformaciones reutilizables de los análisis
de cohorte y la presentación estadística, que siguen perteneciendo al
repositorio del paper.

El contrato se deriva del esquema, las vistas, los jobs y la exportación XLSX
del repositorio. No describe el contenido de ninguna base desplegada.

## Flujo recomendado

1. Materializar y normalizar PCR y Microb.
2. Si la base tiene el registro de la versión anterior, ejecutar la migración
   explícita a claves globales. Si existe una SQLite del paper, importarla
   antes de asignar nuevas identidades.
3. Ejecutar `fis-data derive-pcr` para producir y promover la derivación PCR
   actual, incluidos los resultados integrados y enlaces PCR–SIL.
4. Ejecutar `fis-data pseudonymize` para registrar los identificadores aún no
   seudonimizados presentes en esos hechos actuales y elegibles.
5. Ejecutar `fis-data derive-patient-episodes` para construir episodios
   longitudinales desde esa derivación PCR vigente.
6. Exportar `paper-analysis`.

Las hojas `SIL PCR organisms`, `Culture organisms support`,
`SIL organism targets` y `SIL organism profiles` incluyen la columna
`direct_organism_blee`: `1` si algún aislado actual del microorganismo tiene
`BLE/P`, `0` si existe `BLE/N` y ninguna positiva, y celda vacía si no hay
interpretación explícita `P` o `N`. Solo se consideran los aislados del cultivo
actual incluidos en la derivación vigente; no se incorpora evidencia
retrospectiva. La agregación es por presencia: si hay resultados positivos y
negativos, prevalece el positivo. Los perfiles codificados de carbapenemasas
conservan sus cuatro posiciones.

Las operaciones que reciben `--db-url` aceptan el URL SQLAlchemy de PostgreSQL;
si se omite, usan la configuración `FIS_DATA_DB_*`. Los ejemplos completos de
conexión y preparación están en el [README](../README.md).

## Normalización de microorganismos para publicación

La dimensión `dim_microb_microorganism` añade etiquetas descriptivas al estudio.
No sustituye al catálogo Microb original ni a la identificación del aislado:

| Elemento | Función |
|---|---|
| `ref_microb_catalog` / `ref_microb_catalog_entry` | Referencias originales versionadas; el catálogo `microorg` conserva las descripciones SIL. |
| `microb_isolate.microorganism_id` / `microorganism_code` | Identificación original del aislado, sin modificaciones. La FK apunta a una versión concreta del catálogo. |
| `dim_microb_microorganism.scientific_name` | Nombre científico normalizado, almacenado como texto plano. |
| `analysis_group_code` / `analysis_group_name` | Identificador estable y denominación del grupo usado en tablas y figuras. |

La clave primaria de la dimensión es `microorganism_code VARCHAR(32)`, textual
y sensible a mayúsculas/minúsculas. No se usa el ID de una versión del catálogo
como clave. Los grupos tienen códigos explícitos con el género completo, por
ejemplo `ENTEROBACTER_CLOACAE_COMPLEX`.

La única fuente del diccionario es
[`microorganism-normalization-v1.csv`](../src/fis_data/resources/microorganism-normalization-v1.csv),
incluida como recurso del paquete Python. Contiene exactamente 28 códigos,
26 nombres científicos y 24 grupos analíticos. `observed_name` conserva la
etiqueta suministrada para comprobar diferencias con el catálogo vigente;
no sustituye su descripción ni se copia sobre las referencias originales.
`normalization_note` explica las decisiones que precisan contexto.

### Carga y actualización de una base existente

Con el código actualizado y la conexión habitual configurada:

```bash
fis-data load-microorganism-dimension
fis-data validate-microorganism-normalization
fis-data export-downstream --format paper-analysis --output paper_analysis.xlsx
```

Los tres comandos aceptan `--db-url` para especificar otra conexión PostgreSQL.
La carga crea/actualiza el esquema y las vistas mediante `create_schema` y
realiza un upsert transaccional de las 28 entradas del CSV. Se puede repetir
sin duplicaciones; restaura las etiquetas versionadas si se han alterado.
No edites manualmente la dimensión en producción. Para futuras correcciones,
versiona un nuevo recurso y actualiza `NORMALIZATION_VERSION`, sus pruebas y
esta documentación. No se necesitan dependencias nuevas ni servicios externos.

La carga compara las etiquetas observadas con las descripciones del catálogo
`microorg` vigente, devuelve `catalog_description_differences` y emite una
advertencia si difieren. También comunica si no hay catálogo disponible o si
faltan códigos del diccionario en él. Nunca modifica automáticamente el
diccionario a partir de esas diferencias. Esta implementación se ha validado
solo con datos sintéticos; no confirma descripciones ni recuentos de una base
desplegada.

### Vistas, cobertura y compatibilidad

`v_downstream_sil_pcr_organism` incorpora la dimensión mediante `LEFT JOIN`
por código original, con una única fila de dimensión por código. Conserva
`microorg_code`, `microorg_name`, todos los campos anteriores y su granularidad.
Añade `microorg_scientific_name`, `microorg_group_code` y `microorg_group_name`.
Los tres campos también se propagan a `v_downstream_sil_organism_target` y
`v_downstream_sil_organism_profile`. En las hojas `SIL PCR organisms`,
`SIL organism targets` y `SIL organism profiles`, `analytical-core-tables`
exporta los tres campos. `paper-analysis` presenta juntos `microorg_code`,
`microorg_name` (original SIL) y `microorg_group_name`; omite el nombre científico
y el código de grupo. Esta selección y orden también se reflejan en `--mode schema`.
La exportación no sustituye las etiquetas almacenadas en la base. No se crean
hojas nuevas. El informe del Excel identifica `microorganism-normalization-v1`.

Ejemplo de las etiquetas añadidas:

| microorg_code | microorg_scientific_name | microorg_group_code | microorg_group_name |
|---|---|---|---|
| `2003` | Enterobacter cloacae | ENTEROBACTER_CLOACAE_COMPLEX | Enterobacter cloacae complex |
| `2009` | Enterobacter hormaechei | ENTEROBACTER_CLOACAE_COMPLEX | Enterobacter cloacae complex |
| `19026` | Enterobacter roggenkampii | ENTEROBACTER_CLOACAE_COMPLEX | Enterobacter cloacae complex |
| `2030` | Proteus mirabilis | PROTEUS_MIRABILIS | Proteus mirabilis |

La validación informa de los códigos y filas con microorganismo de la cohorte
actual, sus especies/grupos cubiertos y los códigos de la dimensión. Tanto
`paper-analysis` como `analytical-core-tables` fallan explícitamente antes de
escribir el Excel si encuentran un código no nulo sin correspondencia. La vista
mantiene esas filas con etiquetas normalizadas nulas para poder diagnosticarlas.
Un `microorg_code NULL`, válido para cultivos sin microorganismo, no provoca un
error ni elimina la fila. No hay grupo genérico de respaldo.

Para el universo completo suministrado se esperan 28 códigos cubiertos,
26 nombres, 24 grupos y cero códigos sin correspondencia; las 2.612 filas con
microorganismo son un recuento esperado del estudio, no una restricción impuesta
a cualquier subconjunto analítico. El comando informa del recuento real cuando
se ejecuta sobre la base autorizada.

Los nombres/grupos nuevos solo son etiquetas: no alteran resistencias, BLEE,
perfiles OXA-48/VIM/KPC/NDM, selección de pacientes/muestras/episodios, búsqueda
retrospectiva ni asignación de ST. Eliminar «BLEE» del nombre de `2030` no
infiere ni modifica ningún resultado de resistencia. Las reglas clínicas siguen
comparando códigos SIL originales, nunca nombres normalizados ni grupos.
Los ST deben analizarse a nivel de especie; pertenecer al complejo
*Enterobacter cloacae* no autoriza comparabilidad de ST ni transferencia de
carbapenemasas entre sus especies.

Compatibilidad: actualiza las vistas y carga la dimensión antes de exportar.
Las columnas previas se mantienen; consumidores que exijan un número u orden
exacto de columnas deben adaptarse al formato elegido: el paper añade solo el
nombre de grupo junto al nombre original y el formato completo añade los tres
campos normalizados. La cursiva se aplica únicamente en las tablas/figuras del
manuscrito.

## Registro persistente de pseudónimos

### Identidad y estabilidad

`pseudonym_patient_registry` y `pseudonym_sample_registry` asignan un
pseudónimo global a cada identificador natural: el NHC para pacientes y el
`sample_number` para muestras. La misma cadena recibe el mismo pseudónimo
cuando aparece en distintas fuentes; `source_id` queda como procedencia de la
primera asignación y no participa en la identidad. Las claves no dependen de
IDs sustitutos de las tablas normalizadas, que pueden cambiar al reconstruir
esas tablas. Los identificadores se almacenan como texto para conservar ceros
iniciales.

Los formatos nuevos son `P-0001`, `P-0002`, … para pacientes y `S-00001`,
`S-00002`, … para muestras. Las tablas imponen unicidad global del
identificador y del pseudónimo de cada tipo.
`pseudonymization_counter` conserva el último número asignado y se sincroniza
con los sufijos ya importados antes de asignar nuevos.

La población inicial no es todo `microb_patient` y `microb_sample`: se limita
a los identificadores enlazados a filas de `pcr_sample_result` en la derivación
PCR vigente exitosa, con validación `MATCH_ELIGIBLE` y sin bloqueo
`LEGACY_SCREENING_BLOCKED`. Así, los hechos normalizados de Microb que no
participan en los resultados integrados elegibles no generan pseudónimos.

La asignación solo añade claves que faltan. Una reconstrucción PCR o Microb no
borra los registros ni reinicia los contadores. Las asignaciones y su evento
`ASSIGN` quedan en la misma transacción; el evento conserva los recuentos, no
los identificadores originales.

```bash
fis-data pseudonymize
```

### Importación del registro anterior

`import-pseudonyms` lee la SQLite anterior en modo de solo lectura. El
`--source-name` debe identificar una fuente ya registrada en `ctl_source`:

```bash
fis-data import-pseudonyms \
  --sqlite-path path/to/pseudonyms.sqlite \
  --source-name MICROBIOLOGY
```

La importación valida identificadores y pseudónimos vacíos o duplicados,
referencias de muestras a pacientes ausentes y colisiones con el registro
central. Es idempotente si el identificador ya tiene el mismo pseudónimo;
rechaza una reasignación distinta y actualiza los contadores para que la
siguiente asignación continúe después del mayor sufijo numérico importado. No
modifica la SQLite de origen.

Si hay un registro anterior que conservar, importarlo antes de ejecutar
`pseudonymize`. Las colisiones se rechazan; no se cambia silenciosamente un
pseudónimo ya asignado.

### Migración desde claves por fuente

Las bases creadas con la versión anterior pueden contener varios pseudónimos
para el mismo NHC o número de muestra, uno por `source_id`. La migración
explícita conserva la fila más antigua por identificador, elimina los
duplicados entre fuentes, reemplaza la restricción de unicidad y registra los
recuentos y el motivo. No reinicia los contadores. Los pseudónimos de las filas
duplicadas descartadas dejan de ser la asignación vigente.

```bash
fis-data globalize-pseudonyms \
  --reason "Unificar identificadores compartidos entre fuentes" \
  --confirm-globalization GLOBALIZE-PSEUDONYMS
```

El comando refresca las vistas downstream. `init-db` detecta el esquema
anterior y exige esta migración antes de crear vistas que pudieran duplicar
filas. La migración se ejecuta explícitamente sobre una base PostgreSQL ya
inicializada.

### Destrucción explícita

La destrucción requiere un motivo y un token de confirmación. Se borran las
dos tablas de correspondencias y los contadores; queda un evento `DESTROY`
con el motivo y los recuentos, sin conservar identificadores:

```bash
fis-data destroy-pseudonyms \
  --reason "Motivo autorizado" \
  --confirm-destruction DESTROY-PSEUDONYMS
```

Al eliminarse los contadores, una asignación posterior comienza de nuevo la
secuencia. Por tanto, los pseudónimos anteriores podrían volver a emitirse
tras una destrucción explícita. La destrucción debe tratarse como un reinicio
del registro y coordinarse con los consumidores de exportaciones anteriores.

Para reiniciar una base que aún tiene la restricción antigua por fuente,
ejecutar primero la destrucción y después `globalize-pseudonyms` para actualizar
la unicidad del esquema vacío. Luego `init-db` y `pseudonymize` reconstruyen el
registro desde los identificadores normalizados:

```bash
fis-data destroy-pseudonyms --reason "Reconstrucción autorizada" \\
  --confirm-destruction DESTROY-PSEUDONYMS
fis-data globalize-pseudonyms --reason "Actualizar unicidad del registro vacío" \\
  --confirm-globalization GLOBALIZE-PSEUDONYMS
fis-data init-db
fis-data pseudonymize
```

## Episodios longitudinales de pacientes

Los episodios de paciente (`patient_episode*`) son distintos de `pcr_episode`,
que agrupa runs PCR complementarios. Cada ejecución de
`derive-patient-episodes` registra en `patient_episode_derivation` su versión,
parámetros, derivación PCR de origen, estado, tiempos y recuentos. Solo una
ejecución exitosa se marca como vigente; la anterior permanece como historial.

```bash
fis-data derive-patient-episodes \
  --max-gap-days 30 \
  --algorithm-version patient-gap-v1
```

La población parte de las relaciones PCR–Microb `MATCH_ELIGIBLE` de la
derivación PCR vigente, y excluye muestras bloqueadas por la política de
screening retrospectivo. También requiere paciente Microb con NHC y fecha
clínica. La fecha clínica es `received_at` y, si falta, `entered_at`; las filas
sin fecha o sin paciente se excluyen y sus recuentos quedan en
`details_json`.

El `max_gap_days` se compara entre fechas consecutivas en días naturales. Una
separación igual al máximo permanece en el mismo episodio; solo una separación
mayor abre otro. La duración total del episodio puede superar el máximo. El
orden ante empates es determinista por fecha, ID de muestra PCR e ID de muestra
Microb.

Las membresías conservan `pcr_sample_id`, `microb_sample_id`, fecha clínica y
días desde la muestra anterior. Una muestra PCR asociada a varios cultivos o
un cultivo asociado a varias muestras PCR provoca un error en la agrupación;
no se resuelve seleccionando una coincidencia arbitraria. Los episodios se
numeran por paciente dentro de cada derivación, por lo que el número no es una
identidad permanente entre ejecuciones.

Cada membresía se identifica por `(episode_derivation_id, pcr_sample_id)`;
`microb_sample_id` también es único dentro de la derivación. `init-db` migra
automáticamente las bases PostgreSQL que tenían una clave primaria errónea
limitada a `episode_derivation_id`.

## Vistas para el paper

Las vistas downstream se construyen sobre las vistas global y por organismo
de PCR–SIL existentes. La conciliación de cultivo y la atribución por
microorganismo siguen separadas: los marcadores del cultivo global no se
replican como si fueran evidencia de cada organismo.

La búsqueda retrospectiva selecciona una sola muestra previa compatible para
el conjunto de aislados originales. La herencia se aplica por código exacto
a todos los organismos actuales presentes en ella; los ausentes quedan
`0000`, sin búsqueda adicional. El perfil global reconciliado es el OR de
esa herencia, excluyendo organismos exclusivos de la muestra previa. Sin
muestra previa seleccionada, `organism_*` utiliza la evidencia directa actual.
`direct_organism_*` conserva siempre las observaciones actuales. La política
auditada es `pcr-sil-linking-v3`; véase [PCR–SIL](pcr_sil_linking.md).

| Vista | Granularidad | Uso |
| --- | --- | --- |
| `v_downstream_pcr_sil_target` | Muestra pseudonimizada × diana canónica (4 dianas) | Resultado PCR/FIS, fases, evidencia SIL directa y conciliada, estado de Specific, delta logarítmico y enlace PCR–SIL. |
| `v_downstream_sil_organism_target` | Muestra × microorganismo × diana | Evidencia atribuida al organismo, separando perfil reconciliado y directo, con ST agregado. No emite filas para el miembro sin código de microorganismo. |
| `v_downstream_sil_organism_profile` | Muestra × microorganismo | Perfil reconciliado y directo por código de catálogo, recuento de aislados y ST agregado. |
| `v_downstream_pcr_sil_sample_profile` | Muestra | Perfil FIS, perfiles SIL directo/reconciliado y recuentos de microorganismos, portadores y aislados. |
| `v_downstream_patient_episode` | Episodio dentro de la derivación vigente | Pseudónimo de paciente, fechas, número de muestras y motivo de inicio. |
| `v_downstream_patient_episode_sample` | Muestra miembro dentro del episodio vigente | Pseudónimos, fecha clínica y separación respecto a la muestra anterior. |

Las vistas de episodios muestran solo la derivación de episodios exitosa y
vigente. Los detalles de su algoritmo y parámetros permanecen consultables en
`patient_episode_derivation`.

## Exportaciones

`analytical-core-tables` y `paper-analysis` son dos nombres para el mismo
workbook completo: conserva las ocho hojas del núcleo analítico y añade las
seis hojas downstream de abajo. Las hojas originales conservan sus campos,
incluidos los identificadores legados; las hojas downstream usan pseudónimos.
Ambos nombres aplican la validación de cobertura de pseudónimos antes de
exportar.

El workbook incluye además estas seis hojas downstream:

- `PCR SIL targets`;
- `SIL organism targets`;
- `SIL organism profiles`;
- `PCR SIL sample profiles`;
- `Patient episodes`;
- `Episode samples`.

```bash
fis-data export-downstream --format paper-analysis \
  --output paper_analysis.xlsx
```

Para obtener únicamente los metadatos de las hojas y adaptar scripts sin
compartir filas reales, usar `--mode schema` y guardar el JSON con `--output`.
El comando consulta las columnas con `LIMIT 0`; escribe nombres de hoja,
columnas y SQL fuente, sin recuperar filas de datos:

```bash
fis-data export-downstream --format paper-analysis --mode schema \
  --output paper_analysis_schema.json
```

El export valida que todas las muestras presentes en la vista global de PCR–SIL
tengan pseudónimo de muestra. Si falta alguno, aborta y solicita ejecutar
`pseudonymize`; no escribe un libro parcialmente pseudonimizado. La hoja
`Report` registra la fecha de exportación y, si hay una derivación de episodios
vigente, su ID, algoritmo, parámetros y tiempos.

El workbook no contiene la tabla de correspondencias. Las hojas downstream
nuevas usan pseudónimos; las hojas originales conservan sus identificadores y
campos históricos. Las selecciones de cohorte, estadísticas, estimaciones y
figuras de Quarto siguen siendo responsabilidad del repositorio del paper.

## Límites conocidos

- La SQLite histórica solo debe importarse por el comando explícito y con el
  `source_name` correcto; el repositorio no busca ni descubre automáticamente
  el archivo de otro proyecto.
- La pseudonimización recoge identificadores de Microb normalizado y los
  registra por valor global. Un NHC o número de muestra igual en varias
  fuentes comparte código. Una muestra PCR sin correspondencia Microb no se
  añade por sí sola al registro.
- La destrucción explícita elimina el contador, por lo que permite reiniciar y
  potencialmente reutilizar los mismos códigos pseudónimos.
- La exportación consulta cada hoja en una sentencia separada y las vistas
  resuelven la derivación vigente dinámicamente. Evita promover otra derivación
  mientras se genera el libro; el export todavía no fija un único ID de
  derivación para todas las hojas.
- Los episodios no calculan resúmenes longitudinales por diana ni definen
  positivos/negativos de episodio. Esas definiciones clínicas y los análisis
  estadísticos permanecen pendientes del paper.
- Las vistas downstream se crean con el esquema PostgreSQL del proyecto; las
  pruebas SQLite cubren lógica y exportación, pero no sustituyen una
  comprobación de despliegue PostgreSQL.

