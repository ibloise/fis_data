# PCR Review CLI

`pcr-review` permite inspeccionar, resolver y corregir incidencias producidas por
la analítica PCR sin ejecutar SQL ni modificar directamente las tablas
derivadas. Todas las decisiones quedan registradas como eventos inmutables en
`pcr_review_action` y sobreviven a reconstrucciones con `derive-pcr --rebuild`.

## Conceptos

La cola combina cinco tipos de elementos:

| Tipo | Clave estable | Uso |
| --- | --- | --- |
| `WELL` | `well:<id>` | Clasificación dudosa de un pozo o muestra. |
| `RUN` | `run:<id>` | Clase o fecha de episodio no reconocida. |
| `EPISODE` | `runs:<id>,<id>` | Asociación de runs que requiere confirmación. |
| `TARGET_QC` | `run:<id>:target:<target>` | QC inválido para un target de un run. |
| `RESULT` | `sample:<key>:target:<target>` | Selección final ausente, discordante o empatada. |

Cada elemento incluye:

- `derivation_id`: derivación analítica actual;
- `fingerprint`: hash del motivo y contexto relevante;
- `subject_key`: identidad estable sobre la que se guardan decisiones;
- `status`: `OPEN` o `RESOLVED`;
- `reason` y `context`: causa y evidencia analítica disponible.

Las acciones que cambian estado requieren siempre `--reviewer`, `--reason`,
`--derivation-id` y `--fingerprint`. La CLI rechaza una acción si la derivación
o el fingerprint ya no son actuales. Debe repetirse `list` o `show` antes de
actuar sobre una incidencia que haya cambiado.

## Estados de revisión

`pcr-review` distingue entre el estado operativo de la revisión y la causa
analítica por la que un elemento aparece en la cola. El estado operativo indica
si el fingerprint actual sigue pendiente para el revisor:

| Estado | Significado | Cómo se alcanza | Efecto en la cola |
| --- | --- | --- | --- |
| `OPEN` | La incidencia actual necesita decisión humana. | Estado inicial de cualquier elemento generado por analytics; también tras `reopen` o `revoke`. | Aparece en `list --status OPEN`. |
| `RESOLVED` | El fingerprint actual ya fue revisado. | `resolve` con `ACCEPTED` o `DISMISSED`, u `override` con una corrección tipada. | Desaparece de `list --status OPEN`, pero sigue visible con `--status RESOLVED` o `--status ALL`. |

El estado se calcula plegando el historial append-only de
`pcr_review_action`; no hay una columna mutable que se actualice en sitio. Una
resolución solo cubre el fingerprint exacto revisado. Si una nueva derivación
cambia el motivo o el contexto relevante, el fingerprint cambia y el elemento
reaparece como `OPEN`, aunque exista una resolución anterior para la misma clave
estable.

`revoke` reabre la incidencia actual cuando retira un override activo. Esto
permite auditar que la corrección se retiró sin borrar la decisión previa.

## Necesidades de revisión generadas por analytics

La cola se construye desde la última derivación PCR con estado `SUCCESS`. Cada
tipo usa una regla concreta para decidir si necesita revisión:

| Tipo | Cuándo entra en revisión | Campo analítico fuente | Qué debe decidir el revisor |
| --- | --- | --- | --- |
| `WELL` | El nombre del pozo no permite clasificar de forma segura la muestra o control. | `pcr_well_classification.review_reason IS NOT NULL`. | Confirmar que no requiere cambios o corregir `role` y, si aplica, `sample_key`. |
| `RUN` | El conjunto de targets del run no encaja con una clase PCR conocida. | `pcr_run_analysis.review_reason IS NOT NULL`. | Confirmar la clasificación o corregir `run_class` y opcionalmente `episode_date_key`. |
| `EPISODE` | Analytics no puede asociar runs de forma inequívoca en un episodio. | `pcr_episode.association_status = 'REVIEW'`. | Confirmar que queda pendiente o definir explícitamente qué runs pertenecen al episodio. |
| `TARGET_QC` | El QC de un target para un run es inválido. | `pcr_target_qc.qc_status = 'INVALID'`. | Confirmar la invalidez o corregir `status` y `valid` para esa combinación run-target. |
| `RESULT` | No hay resultado válido, hay discordancia de llamadas o hay empate entre candidatos recientes. | `pcr_result_selection.needs_review = 1`. | Confirmar la selección, escoger un resultado fuente preferido y/o fijar una llamada final efectiva. |

### `WELL`: clasificación dudosa del pozo

Un pozo entra en cola cuando su nombre está vacío o no coincide con una muestra
numérica ni con un control conocido. En la práctica analytics lo clasifica como
`NON_SAMPLE_REVIEW` y guarda el motivo en `review_reason`.

Valores habituales de `reason`:

- `Missing sample name.`
- `Non-numeric sample name is not a known control.`

Un `override well` permite convertirlo en `REAL_SAMPLE`,
`POSITIVE_CONTROL`, `NEGATIVE_CONTROL` o `NON_SAMPLE_REVIEW`. Si se marca como
`REAL_SAMPLE`, debe indicarse una `sample_key` canónica.

### `RUN`: clase de run no reconocida

Un run entra en cola cuando sus targets no describen claramente screening,
panel OXA48/VIM, panel KPC/NDM ni compensación parcial. El motivo conserva el
conjunto de targets no reconocido.

Ejemplo de `reason`:

- `Unrecognized target set: [...]`

Un `override run` permite fijar una clase válida y, opcionalmente, una fecha de
episodio (`episode_date_key`) si el nombre del run no contiene una fecha
parseable.

### `EPISODE`: asociación de runs ambigua

Analytics asocia runs de un episodio usando la fecha inferida del nombre y el
solapamiento de muestras. Cuando no hay un compañero inequívoco, el episodio se
marca como `REVIEW`.

Motivo habitual:

- `No unambiguous run partner.`

Un `override episode` define de forma explícita la membresía de runs. En la
siguiente derivación se crea un episodio manual y auditable, sin modificar la
evidencia analítica original.

### `TARGET_QC`: QC inválido

Una combinación run-target entra en cola cuando falla el control positivo,
falla el control negativo o falta alguno de ellos. El `reason` es el JSON de
detalles de QC, con contadores y listas de advertencias o fallos.

Issues posibles en el payload de QC:

- `MISSING_POSITIVE_CONTROL`
- `MISSING_NEGATIVE_CONTROL`
- `POSITIVE_CONTROL_NOT_POSITIVE`
- `NEGATIVE_CONTROL_POSITIVE`
- `NEGATIVE_CONTROL_INDETERMINATE`

Un `override qc` permite fijar `VALID`, `WARNING` o
`VALID_WITH_WARNINGS` como válido, o mantener `INVALID` como inválido. La
validación impide declarar `INVALID` y `--valid` simultáneamente.

### `RESULT`: selección final revisable

Un resultado entra en cola cuando la selección por muestra-target no es
automática o no es suficientemente segura. Las condiciones actuales son:

- no existe ningún candidato válido positivo o negativo;
- existen candidatos válidos con llamadas discordantes;
- los candidatos más recientes empatan en fecha de run.

El campo `reason` contiene el nivel de concordancia calculado:

- `NO_VALID_RESULT`
- `DISCORDANT`
- `CONCORDANT` cuando aun siendo concordante requiere revisión por empate.

Un `override result` puede elegir un `preferred_result_id` como fuente
analítica preferida, fijar una `final_call` efectiva, o ambas cosas. La vista
`v_pcr_effective_result` muestra la llamada efectiva corregida sin ocultar la
selección analítica original.

## Invocación y base de datos

La ruta de la base se indica en el grupo principal:

```bash
fis-data pcr-review --db-path var/fis_data.sqlite COMMAND [OPTIONS]
```

Si se omite `--db-path`, se usa la configuración habitual de `FIS_DB_URL` o
`FIS_DB_PATH`. La herramienta crea la tabla de auditoría si todavía no existe,
pero necesita al menos una derivación PCR con estado `SUCCESS` para listar o
modificar incidencias.

## Listar la cola

```bash
fis-data pcr-review --db-path var/fis_data.sqlite list \
  --status OPEN --limit 100 --offset 0 --format table
```

Opciones:

- `--type WELL|RUN|EPISODE|TARGET_QC|RESULT`: limita el tipo de elemento;
- `--status OPEN|RESOLVED|ALL`: filtra por estado de revisión;
- `--limit` y `--offset`: paginación estable de la cola ordenada por tipo y
  clave;
- `--format table|json`: tabla legible o JSON apto para scripts.

La salida tabular es deliberadamente compacta e incluye una columna `NEXT_STEP`
con el comando `show` que debe ejecutarse antes de decidir. `list` sirve para
priorizar la cola; no pretende contener toda la evidencia necesaria para
corregir una incidencia.

La salida JSON contiene los identificadores necesarios para automatizar filtros
o invocar `show`:

```bash
fis-data pcr-review --db-path var/fis_data.sqlite list \
  --type RESULT --status OPEN --format json
```

## Mostrar un elemento

`show` es la vista de trabajo para decidir qué acción tomar. Por defecto emite
una salida legible con:

- diagnóstico resumido de por qué existe la necesidad de revisión;
- evidencias concretas que debe comprobar el revisor;
- contexto enriquecido desde las tablas analíticas;
- acciones candidatas y cuándo usar cada una;
- comandos completos con `derivation_id` y `fingerprint`;
- historial completo y override activo, si existen.

```bash
fis-data pcr-review --db-path var/fis_data.sqlite show \
  --type RESULT --subject sample:123:target:KPC
```

Para scripts puede usarse `--format json`, que expone los mismos bloques como
`diagnosis`, `candidate_actions`, `commands`, `item`, `history` y
`active_override`.

En incidencias `EPISODE`, `show` añade los runs miembros y posibles partners
del episodio, incluyendo nombre del run, clase, fecha de episodio, targets,
conteo de muestras reales, solapamiento con el run revisado y si comparte fecha
de episodio. Un caso como `EPISODE runs:1` pasa así de indicar únicamente
`No unambiguous run partner.` a mostrar qué runs pueden ser candidatos y un
comando de este estilo:

```bash
fis-data pcr-review override episode \
  --subject runs:1 \
  --derivation-id 4 \
  --fingerprint HASH \
  --run-id 1 \
  --run-id <partner_run_id> \
  --reviewer analyst \
  --reason "Runs del mismo episodio confirmados manualmente"
```

Para resultados también incluye las interpretaciones candidatas, con
`interpretation_id`, `result_id`, llamada, Cq, run y validez QC. Los overrides
de resultado utilizan `result_id`, que corresponde al resultado Cq fuente y es
estable entre derivaciones; no debe confundirse con `interpretation_id`.

## Resolver y reabrir

Resolver confirma que una incidencia ha sido revisada sin alterar valores
analíticos:

```bash
fis-data pcr-review --db-path var/fis_data.sqlite resolve \
  --type RESULT \
  --subject sample:123:target:KPC \
  --derivation-id 4 \
  --fingerprint HASH \
  --resolution ACCEPTED \
  --reviewer analyst \
  --reason "Resultado revisado y aceptado"
```

Resoluciones admitidas:

- `ACCEPTED`: la incidencia es correcta y no requiere cambios;
- `DISMISSED`: no procede actuar sobre ella.

`CORRECTED` se reserva para los comandos `override`; no puede declararse con
`resolve` sin aportar una corrección tipada.

Una incidencia resuelta desaparece de `list --status OPEN`. Si el motivo o el
contexto cambia en otra derivación, cambia su fingerprint y reaparece abierta.

Para reabrir el fingerprint actual:

```bash
fis-data pcr-review --db-path var/fis_data.sqlite reopen \
  --type RESULT --subject sample:123:target:KPC \
  --derivation-id 4 --fingerprint HASH \
  --reviewer analyst --reason "Requiere una segunda revisión"
```

## Overrides tipados

Un override registra una corrección, marca la incidencia actual como resuelta y
se aplica en la siguiente derivación. No reescribe la evidencia original.

### Pozo

Corrige el rol y, para muestras reales, su clave canónica:

```bash
fis-data pcr-review --db-path var/fis_data.sqlite override well \
  --subject well:42 --derivation-id 4 --fingerprint HASH \
  --role REAL_SAMPLE --sample-key 12345 \
  --reviewer analyst --reason "Identificador confirmado"
```

Roles admitidos: `REAL_SAMPLE`, `POSITIVE_CONTROL`, `NEGATIVE_CONTROL` y
`NON_SAMPLE_REVIEW`. `REAL_SAMPLE` exige `--sample-key`. La rederivación usa el
rol corregido para clasificar controles, QC y resultados posteriores.

### Run

Corrige la clase del run y opcionalmente la fecha usada para asociar episodios:

```bash
fis-data pcr-review --db-path var/fis_data.sqlite override run \
  --subject run:17 --derivation-id 4 --fingerprint HASH \
  --run-class PANEL_KPC_NDM --episode-date 2024-01-31 \
  --reviewer analyst --reason "Panel y fecha verificados"
```

Clases admitidas: `SCREENING`, `PANEL_OXA48_VIM`, `PANEL_KPC_NDM`,
`COMPENSATION_PARTIAL` y `OTHER`. La fecha debe utilizar formato ISO
`YYYY-MM-DD`.

### Episodio

Define manualmente los runs que forman un episodio:

```bash
fis-data pcr-review --db-path var/fis_data.sqlite override episode \
  --subject runs:17,18 --derivation-id 4 --fingerprint HASH \
  --run-id 17 --run-id 18 \
  --reviewer analyst --reason "Runs complementarios confirmados"
```

Todos los IDs deben existir en la derivación actual. Un run no puede pertenecer
a dos overrides de episodio activos. Durante la rederivación, esos runs se
excluyen del agrupamiento automático y forman un episodio `MANUAL:<action_id>`.

### QC de target

Corrige el estado y la validez de una combinación run-target:

```bash
fis-data pcr-review --db-path var/fis_data.sqlite override qc \
  --subject run:17:target:KPC \
  --derivation-id 4 --fingerprint HASH \
  --status VALID --valid \
  --reviewer analyst --reason "Control revisado manualmente"
```

Estados admitidos: `VALID`, `WARNING`, `INVALID`, `PARTIALLY_INVALID` y
`VALID_WITH_WARNINGS`. Se usa `--valid` o `--invalid`; un estado `INVALID` no
puede marcarse válido. La selección de resultados de la nueva derivación
utiliza esta validez efectiva.

### Resultado final

Elige una interpretación fuente y/o fuerza la llamada expuesta al consumidor:

```bash
fis-data pcr-review --db-path var/fis_data.sqlite override result \
  --subject sample:123:target:KPC \
  --derivation-id 4 --fingerprint HASH \
  --preferred-result-id 9876 --call NEGATIVE \
  --reviewer analyst --reason "Curva revisada manualmente"
```

- `--preferred-result-id` debe aparecer entre los candidatos de `show`;
- `--call` admite los niveles PCR definidos por el modelo;
- puede proporcionarse uno de los dos valores o ambos;
- la interpretación preferida se incorpora al rederivar;
- la llamada forzada se presenta en `v_pcr_effective_result`, mientras que las
  vistas base conservan la llamada algorítmica.

## Revocar un override

`revoke` añade un evento de revocación; no borra ni actualiza el override
anterior:

```bash
fis-data pcr-review --db-path var/fis_data.sqlite revoke \
  --type RESULT --subject sample:123:target:KPC \
  --reviewer analyst --reason "Corrección retirada"
```

Después de revocarlo, el override deja de aplicarse y la incidencia vuelve a
estado abierto si sigue presente. Para recalcular dependencias debe ejecutarse
de nuevo `apply`.

## Consultar el historial

```bash
fis-data pcr-review --db-path var/fis_data.sqlite history \
  --type RESULT --subject sample:123:target:KPC
```

El historial se devuelve como JSON en orden de inserción e incluye acciones
`RESOLVE`, `REOPEN`, `OVERRIDE` y `REVOKE`, junto con revisor, motivo, payload,
fingerprint, derivación y fecha.

## Aplicar correcciones

`apply` ejecuta una derivación PCR consumiendo todos los overrides activos:

```bash
fis-data pcr-review --db-path var/fis_data.sqlite apply \
  --algorithm-version melting-v1 \
  --reviewer analyst \
  --rebuild
```

El comportamiento predeterminado es `--rebuild`; puede seleccionarse
`--no-rebuild` cuando se desee conservar derivaciones anteriores. La ejecución
se registra también en `ctl_etl_run` como `review:pcr:apply`, incluyendo al
revisor y los contadores resultantes.

`derive-pcr` también consume overrides activos de forma predeterminada. Para
obtener una derivación diagnóstica puramente algorítmica:

```bash
fis-data derive-pcr --db-path var/fis_data.sqlite \
  --algorithm-version melting-v1 --rebuild --ignore-overrides
```

`--ignore-overrides` no elimina ni revoca decisiones; solo las omite en esa
ejecución.

## Resultados base y efectivos

- `v_pcr_current_result` conserva la selección y llamada derivadas;
- `v_pcr_effective_result` aplica la llamada manual activa y añade
  `has_override`;
- `pcr_review_action` conserva toda la trazabilidad;
- las tablas derivadas siguen siendo reconstruibles y no deben editarse
  manualmente.

Flujo recomendado:

1. Ejecutar `list --status OPEN` con filtros y paginación.
2. Usar `show` para revisar contexto y candidatos.
3. Ejecutar `resolve` si no hay que modificar nada, u `override` si existe una
   corrección.
4. Revisar `history` cuando sea necesario auditar decisiones.
5. Ejecutar `apply` después de agrupar las correcciones deseadas.
6. Comprobar la nueva cola y `v_pcr_effective_result`.
