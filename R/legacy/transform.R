library(tidyverse)
library(here)
library(lubridate)

source(here("R", "utils.R"))


calc_delta_16S <- function(df) {
  
  # Calcular deltas:
  # delta = log 2^(ct-16ct)
  
  fis_16S <- df %>% 
    # Nota: Cambiar este filtro para imputar el 16S del otro run
    mutate(
      cq_16S = as.numeric(cq_16S),
      cq_16S = ifelse(
        cq_16S == 0 | cq_16S > 35,
        median(cq_16S, na.rm=TRUE),
        cq_16S
      )
    ) %>% 
    mutate(
      delta = if_else(cq != 0, cq-cq_16S, NaN),
      log_delta = log10(2**(-delta))
    ) 
  
  return(fis_16S)
}

withdraw_fis_controls <- function(df) {
  df <- df %>% 
    filter(grepl("^[0-9]+$", as.character(sample)))
}

pivot_wide_fis_core <- function(df){
  # Pivota a ancho la tabla FIS
  interp <- fis_core %>%
    filter(type == "Specific") %>%
    select(sample, carba, interpretation) %>% 
    pivot_wider(
      names_from = carba,
      values_from = interpretation
    ) %>% 
    mutate(encode_carba = encode_carba(.))
  
  logd <- df %>%
    filter(type == "Specific") %>%
    select(sample, carba, log_delta) %>%
    pivot_wider(names_from = carba, values_from = log_delta)
  
  fis_core_spec <- interp %>%
    left_join(logd, by = "sample", suffix = c("", "_log_delta"))
  
  fis_core_scr <- df %>%
    filter(type == "Screening") %>%
    select(sample, carba, interpretation) %>% 
    pivot_wider(
      names_from = carba,
      values_from = interpretation
    ) %>% 
    mutate(encode_carba = encode_carba(.))
  
  fis_wide <- 
    full_join(fis_core_scr, fis_core_spec, by="sample", suffix = c("_screening", "_specific"))
  
  # Resume el resultado de FIS.
  #   Para ello, manda specific. Si se hizo specific se toma su valor, si no, se setea a 0
  
  fis_wide$encode_fis <- ifelse(is.na(fis_wide$encode_carba_specific), "0000", fis_wide$encode_carba_specific)
  
  # Por último, si encode_carba_spe es NA, se normalizar el encode_fis a 0000:

  
  return(fis_wide)
  
}

clean_duplicates_run <- function(df) {
  # Toma la datalong de FIS core y dejar el último run
  # Si una muestra ha tenido runs específicos, da igual, Se queda con el último.
  df %>% 
    arrange(sample, type, carba, run_started) %>% 
    group_by(sample, type, carba) %>% 
    slice_tail(n=1) %>% 
    ungroup()
  
  return(df)
}

simplify_cultures <- function(df){
  
  # Código que realiza el coalesce necesario para unificar los orígenes de datos
  cultures <- df %>% 
    mutate(
      nhc = nhc_number_src,
      sample = sample_number_src,
      entry_date = entry_date_src,
      result_interpretate = result_interpretate_src,
      microorg_code = microorg_code_src,
      IMP = coalesce(IMP_result, IMP_src),
      KPC = coalesce(KPC_result, KPC_src),
      NDM = coalesce(NDM_result, NDM_src),
      OXA48 = coalesce(OXA_result, OXA_src),
      VIM = coalesce(VIM_result, VIM_src),
      encoded_carba_org = coalesce(encoded_carba_org_result, encoded_carba_org_src),
      global_encoded = coalesce(global_encoded_result, global_encoded_src),
      result_sample = coalesce(sample_number_result, sample_number_src),
      result_entry_date = coalesce(entry_date_result, entry_date_src),
      result_itself = result_sample == sample
    ) %>% 
    select(
      nhc, 
      sample,
      entry_date, 
      result_interpretate, 
      microorg_code,
      microorg_name,
      OXA48,
      VIM,
      KPC,
      NDM,
      IMP,
      encoded_carba_org,
      global_encoded,
      result_sample,
      result_entry_date,
      result_itself
    )
  
  return(cultures)
}

withdraw_organism_in_clean_data <- function(df) {

  df <- df %>% 
    select(
      -microorg_code,
      -microorg_name, 
      -OXA48,
      -VIM,
      -KPC,
      -NDM,
      -IMP,
      -encoded_carba_org
    ) %>% 
    distinct()
  
  return(df)
  
}
calc_age <- function(df) {
  df %>% 
    group_by(nhc) %>% 
    mutate(
      age = floor(
        time_length(
          interval(birthdate, min(entry_date, na.rm = TRUE)),
          "years"
        )
      )
    ) %>% 
    ungroup()
}
extract_demographic_from_clean_data <- function(df) {
  
}

