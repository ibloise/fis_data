library(dplyr)
library(lubridate)

add_patient_age <- function(
    data,
    mode = c("first", "last", "mean", "strict"),
    id_col = "nhc",
    birth_col = "birth_date",
    entry_col = "entry_date"
) {
  # Función que toma el dataframe core y lo devuelve con la edad
  
  mode <- match.arg(mode)
  
  # Evitar conflictos si la función se ejecuta más de una vez
  data <- data %>%
    dplyr::select(-dplyr::any_of("age"))
  
  # Edad estricta: calculate_age() ya mantiene una fila por registro
  if (mode == "strict") {
    
    result <- calculate_age(
      data,
      mode = mode,
      id_col = id_col,
      birth_col = birth_col,
      entry_col = entry_col
    )
    
    # Protección adicional
    if (nrow(result) != nrow(data)) {
      stop(
        "Error: mode='strict' ha modificado el número de registros."
      )
    }
    
    return(result)
  }
  
  # Modos agregados: una edad por paciente
  ages <- calculate_age(
    data,
    mode = mode,
    id_col = id_col,
    birth_col = birth_col,
    entry_col = entry_col
  )
  
  # Comprobar explícitamente que la tabla de edades
  # tiene como máximo una fila por paciente
  duplicated_ids <- ages %>%
    dplyr::count(.data[[id_col]]) %>%
    dplyr::filter(n > 1)
  
  if (nrow(duplicated_ids) > 0) {
    stop(
      "Error: calculate_age() ha devuelto más de una fila por ",
      id_col,
      ". El join podría multiplicar registros."
    )
  }
  
  n_before <- nrow(data)
  
  # relationship = "many-to-one" obliga a que la tabla derecha
  # tenga como máximo una coincidencia por paciente
  result <- data %>%
    dplyr::left_join(
      ages,
      by = id_col,
      relationship = "many-to-one"
    )
  
  # Segunda protección frente a multiplicación
  if (nrow(result) != n_before) {
    stop(
      "Error: el join ha modificado el número de registros: ",
      n_before, " -> ", nrow(result)
    )
  }
  
  result
}

calculate_age <- function(
    data,
    mode = c("first", "last", "mean", "strict"),
    id_col = "nhc",
    birth_col = "birth_date",
    entry_col = "entry_date"
) {
  
  mode <- match.arg(mode)
  
  # Calcular edad en cada registro
  data_age <- data %>%
    mutate(
      .birth_date = as.Date(.data[[birth_col]]),
      .entry_date = as.Date(.data[[entry_col]]),
      age = floor(
        time_length(
          interval(.birth_date, .entry_date),
          unit = "years"
        )
      ),
      age = if_else(age < 0, NA_real_, age)
    )
  
  # Edad estricta: conservar todos los registros
  if (mode == "strict") {
    return(
      data_age %>%
        select(-.birth_date, -.entry_date)
    )
  }
  
  # Primera edad
  if (mode == "first") {
    return(
      data_age %>%
        filter(!is.na(.entry_date)) %>%
        arrange(.entry_date) %>%
        group_by(.data[[id_col]]) %>%
        slice_head(n = 1) %>%
        ungroup() %>%
        select(all_of(id_col), age)
    )
  }
  
  # Última edad
  if (mode == "last") {
    return(
      data_age %>%
        filter(!is.na(.entry_date)) %>%
        arrange(desc(.entry_date)) %>%
        group_by(.data[[id_col]]) %>%
        slice_head(n = 1) %>%
        ungroup() %>%
        select(all_of(id_col), age)
    )
  }
  
  # Edad media
  if (mode == "mean") {
    return(
      data_age %>%
        group_by(.data[[id_col]]) %>%
        summarise(
          age = if (all(is.na(age))) NA_real_ else mean(age, na.rm = TRUE),
          .groups = "drop"
        )
    )
  }
}

prepare_fis_data <- function(data) {
  
  result <- data |>
    filter(
      entry_date >= STUDY_START,
      entry_date <= STUDY_END
    ) |>
    add_patient_age(mode = "first") |>
    mutate(
      age_cat = if_else(
        age < 18,
        AgeCategories$PEDIATRIC,
        AgeCategories$ADULT
      )
    )
  
  stopifnot(
    all(result$entry_date >= STUDY_START, na.rm = TRUE),
    all(result$entry_date <= STUDY_END, na.rm = TRUE)
  )
  
  result
}

prepare_fis_sil <- function(
    data,
    fis_data,
    sample_col = "sample"
) {
  
  # Comprobar contrato mínimo
  required_cols <- sample_col
  
  missing_data_cols <- setdiff(required_cols, names(data))
  missing_fis_cols <- setdiff(required_cols, names(fis_data))
  
  if (length(missing_data_cols) > 0) {
    stop(
      "fis_sil is missing required columns: ",
      paste(missing_data_cols, collapse = ", ")
    )
  }
  
  if (length(missing_fis_cols) > 0) {
    stop(
      "fis_data is missing required columns: ",
      paste(missing_fis_cols, collapse = ", ")
    )
  }
  
  # Universo de muestras del análisis FIS
  fis_samples <- fis_data |>
    filter(!is.na(.data[[sample_col]])) |>
    distinct(.data[[sample_col]])
  
  sil_samples <- data |>
    filter(!is.na(.data[[sample_col]])) |>
    distinct(.data[[sample_col]])
  
  # Toda muestra FIS debe existir en SIL
  missing_in_sil <- fis_samples |>
    anti_join(
      sil_samples,
      by = sample_col
    )
  
  if (nrow(missing_in_sil) > 0) {
    stop(
      nrow(missing_in_sil),
      " samples from fis_data are missing from fis_sil: ",
      paste(
        head(missing_in_sil[[sample_col]], 10),
        collapse = ", "
      ),
      if (nrow(missing_in_sil) > 10) "..." else ""
    )
  }
  
  # Nos quedamos únicamente con el universo del estudio
  result <- data |>
    semi_join(
      fis_samples,
      by = sample_col
    )
  
  result
}