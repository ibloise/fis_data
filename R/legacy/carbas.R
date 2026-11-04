library(tidyverse)


plot_carba_tp <- function(df, carba, log_col = NULL) {
  
  fis_col <- paste0(carba, "_fis")
  culture_col <- paste0(carba, "_culture")
  org_col <- paste0(carba, "_org")
  
  if (is.null(log_col)) {
    log_col <- paste0("log_delta_", carba)
  }
  
  df_plot <- df %>%
    filter(.data[[fis_col]] == 1) %>%
    select(
      sample,
      entry_date,
      culture_result,
      !!sym(culture_col),
      !!sym(fis_col),
      !!sym(log_col)
    ) %>%
    distinct() %>% 
    mutate(
      TP = if_else(
        .data[[culture_col]] == "0",
        "False Negative",
        "True Positive"
      )
    )
  
  ggplot(df_plot, aes(x = TP, y = .data[[log_col]])) +
    geom_boxplot() + labs(title=carba)
}


get_carba_df <- function(df, carba, log_col = NULL) {
  
  fis_col <- paste0(carba, "_fis")
  culture_col <- paste0(carba, "_culture")
  org_col <- paste0(carba, "_org")
  
  if (is.null(log_col)) {
    log_col <- paste0("log_delta_", carba)
  }
  
  df %>%
    filter(.data[[fis_col]] == 1) %>%
    transmute(
      sample,
      entry_date,
      culture_result,
      microorg,
      org = .data[[org_col]],
      culture = .data[[culture_col]],
      fis = .data[[fis_col]],
      log_delta = .data[[log_col]],
      TP = case_when(
        is.na(culture) ~ NA_character_,
        culture == "0" ~ "False Negative",
        TRUE ~ "True Positive"
      ),
      carbapenemasa = carba
    ) %>%
    filter(!is.na(TP), !is.na(log_delta))
}

unpack_carba_df <- function(df, code_col,
                            colnames = c("OXA", "VIM", "KPC", "NDM"),
                            suffix = "",
                            drop = FALSE) {
  
  ## Función para desempaquetar el patrón 1111 a OXA, VIM, KPC y NDM
  
  code_col <- deparse(substitute(code_col))
  
  x <- as.character(df[[code_col]])
  x <- trimws(x)
  
  ok <- grepl("^[01]{4}$", x)
  if (any(!ok)) warning("Hay valores que no son códigos binarios de 4 dígitos (0/1). Se pondrá NA en esas filas.")
  
  mat <- matrix(NA_integer_, nrow = nrow(df), ncol = 4,
                dimnames = list(NULL, paste0(colnames, suffix)))
  
  if (any(ok)) {
    chars <- strsplit(x[ok], "", fixed = TRUE)
    mat_ok <- do.call(rbind, lapply(chars, function(v) as.integer(v)))
    mat[ok, ] <- mat_ok
  }
  # 
  df[colnames(mat)] <- as.data.frame(mat)
  
  if (drop) df[[code_col]] <- NULL
  
  df
}
