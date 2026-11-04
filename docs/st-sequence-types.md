# Ingestión de tipos de secuencia (ST)

La fuente ST añade el tipo de secuencia de un microorganismo por muestra. Usa
el flujo común de archivos Excel: ingestión raw, parseo auditable y
materialización con cruce a los aislados normalizados.

```bash
fis-data ingest-excel --source-name ST --entity st path/to/st.xlsx
fis-data parse-excel --entity st
fis-data materialize --entity st
```

El parser busca estas columnas en las primeras 20 filas de cada hoja:

| Encabezado | Uso |
| --- | --- |
| `MUESTRA` | Identificador exacto de muestra. |
| `COD_MICROORGANISMO` | Clave para localizar el microorganismo en los aislados. |
| `MICROORGANISMO` | Nombre informado; se guarda para auditoría, no se usa como clave. |
| `MICROORGANISMO_raw` | Valor original conservado para auditoría. |
| `ST` | Valor normalizado que puede propagarse a resultados. |
| `ST_raw` | Valor original conservado para auditoría. |

La ingestión conserva todas las celdas originales en `raw_excel_rows`. El
parseo guarda los campos normalizados y raw en el payload; la materialización
los conserva en `stg_st_record`. Los campos `*_raw` no se incluyen en vistas ni
exportaciones finales.

## Reglas de cruce y auditoría

- La muestra se busca por igualdad exacta de `sample_number` tras quitar
  espacios exteriores. Si no existe, queda `SAMPLE_NOT_FOUND`.
- El código del microorganismo se compara exactamente con
  `microb_isolate.microorganism_code` dentro de esa muestra. El nombre no
  interviene en la unión.
- Si el número de muestra corresponde a más de una fila `microb_sample`, la
  observación queda `AMBIGUOUS_SAMPLE` y no se enlaza a ningún aislado.
- Si una muestra y código encuentran un aislado, se asigna el ST con estado
  `MATCHED`.
- Si encuentran varios aislados con el mismo código, se enlaza el ST a todos.
  El registro queda `AMBIGUOUS_ISOLATES` con el recuento y los IDs enlazados.
- Si no hay aislados con ese código, queda `ISOLATE_NOT_FOUND`.
- Si el mismo archivo trae varios ST distintos para la misma muestra y código,
  quedan `CONFLICTING_ST_VALUES` y no se asigna ninguno.

Las filas procesadas y sus resultados se auditan en `ctl_materialization_item`,
`stg_st_record` y `microb_isolate_st_link`. El materializador puede repetirse
con `--reprocess-all`; no elige coincidencias por nombre, orden o primer
resultado.

La vista `v_pcr_sil_isolate_marker_summary` añade `st`, agregado por `isolate_id`.
Solo muestra el valor cuando todas las observaciones enlazadas para ese aislado
coinciden en un único ST; ante valores contradictorios, `st` queda nulo y los
registros discrepantes permanecen en las tablas de auditoría. La vista
`v_downstream_sil_pcr_organism`, exportada como `SIL PCR organisms`, agrega por
muestra y código de microorganismo los ST distintos de sus aislados. Si hay
varios aislados con ST diferentes, la columna contiene la lista ordenada de
valores distintos. Si cualquier aislado tiene valores contradictorios o la
fuente tiene una fila marcada `CONFLICTING_ST_VALUES` para esa muestra y
microorganismo, el ST agregado queda nulo para no presentar un resultado
parcial como completo. Las vistas de objetivos y perfiles por organismo
propagan la misma columna; los campos raw no se exportan.

Una ejecución de `normalize-microb` que procesa filas, incluida una
reconstrucción, invalida los enlaces a IDs de aislado y deja los ficheros ST
pendientes de materialización. Tras completar la normalización, ejecutar de
nuevo `fis-data materialize --entity st` para reconstruir las asociaciones.
