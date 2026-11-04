
library(here)

general_reader <- function(file, reader, reader_args = list(), postprocess = NULL) {
  if (!file.exists(file)) {
    stop("El archivo no existe: ", file)
  }
  
  df <- do.call(reader, reader_args)
  
  if (!is.null(postprocess)) {
    df <- postprocess(df)
  }
  
  return(df)
}


read_fis_core <- function(
    file = here("data", "input", "analytical_core.xlsx"),
    sheet = "SIL PCR global"
) {
  general_reader(
    file=file,
    reader = readxl::read_excel,
    reader_args = list(path= file, sheet = sheet),
    #postprocess = function(df) {
    #  df$run_started <- as.Date(df$run_started,  origin = "1899-12-30")
    #  df
    #}
  )  
}

read_unit_services_classification <- function(
    file = here("data", "input", "unit_services_categorized_v2.csv")
) {
  
  general_reader(
    file = file,
    reader = readr::read_csv,
    reader_args = list(
      file = file,
      show_col_types = FALSE,
      col_types = readr::cols(
        origin_code = readr::col_character(),
        service_code = readr::col_character(),
        n = readr::col_integer(),
        .default = readr::col_character()
      )
    ),
    postprocess = function(df) {
      
      # El CSV actual contiene una última columna sin nombre
      # con notas adicionales de clasificación.
      if ("...14" %in% names(df)) {
        df <- df |>
          dplyr::rename(
            classification_note = `...14`
          )
      }
      
      df |>
        dplyr::mutate(
          origin_code = dplyr::na_if(
            trimws(origin_code),
            ""
          ),
          service_code = dplyr::na_if(
            trimws(service_code),
            ""
          )
        ) |>
        dplyr::rename(
          classification_support_n = n
        )
    }
  )
}

read_fis_sil <- function(
    file = here("data", "input", "analytical_core.xlsx"),
    sheet = "SIL PCR organisms"
) {
  general_reader(
    file=file,
    reader = readxl::read_excel,
    reader_args = list(path= file, sheet = sheet),
  )  
}
  