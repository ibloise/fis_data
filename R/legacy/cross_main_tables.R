library(tidyverse)
library(here)
library(stringi)
# Script que lee, fusiona y valida los 3 orígenes de datos principales:
#   - Tabla core del FIS
#   - Tabla de SIL (Microb) con previo imputado
#   - Tabla de Deltas


source(here("R", "readers.R"))
source(here("R", "transform.R"))

fis_core <- read_fis_core() %>% 
  clean_duplicates_run() %>% 
  withdraw_fis_controls() %>% 
  calc_delta_16S()

fis_wide <- pivot_wide_fis_core(fis_core)

# Escribimos fis_Wide para hacer los análisis específicos:
write.csv(fis_wide, here("data", "fis_wider_table.csv"))

## Nos traemos la tabla del SIL

cross_cultures <- read_cross_cultures()

#TODO: Hay que validar duro esta tabla:
# Los bichos tienen que ser siempre el mismo entre result y src
# Hay que asegurar no duplicidad (solo se admiten positive cultures duplicados de microorganismos diferentes)

cultures <- simplify_cultures(cross_cultures)

# Añadimos los gfhs y la metadata de pacienes

patients <- read_patients_metadata()

gfhs <- read_sample_gfhs()
services_catalog <- read_gfhs_services()
procs_catalog <- read_gfh_procs()

# Validación de cardinalidad 

services_catalog %>% 
  count(code) %>% 
  filter(n>1)

procs_catalog %>% 
  count(code) %>% 
  filter(n>1)

gfhs <- gfhs %>% 
  left_join(services_catalog, by=c("service_code" = "code")) %>% 
  left_join(procs_catalog, by=c("origin_code" = "code")) %>% 
  distinct()

# Cruce de tablas

#   Validamos la cardinalidad

gfhs %>% 
  count(sample) %>% 
  filter(n>1)

patients %>% 
  count(nhc_number) %>% 
  filter(n>1)

rows_prejoin <- nrow(cultures)

cultures <- cultures %>% 
  left_join(gfhs, by="sample") %>% 
  left_join(patients, by=c("nhc" = "nhc_number"))


cultures <- calc_age(cultures)
rows_postjoin <- nrow(cultures)

if (rows_prejoin > rows_postjoin){
  print("WARNING: El join ha multiplicado filas")
}
#right para que salga primero el cultures:

fis_wide %>% 
  count(sample) %>% 
  filter(n>1)

fis_clean_data <- cultures %>% 
  right_join(fis_wide, by="sample")

# Escribimos fis_cleand_data
write.csv(fis_clean_data, here("data", "clean_data.csv"), row.names = FALSE)

# Y ahora los episodios
################
N_days <- 30
#################

df_episodes <- fis_clean_data %>% 
  filter(!is.na(nhc)) %>% 
  select(-microorg_code, -microorg_name, - encoded_carba_org, -OXA48, -VIM, -KPC, -NDM, -IMP) %>% 
  distinct() %>% 
  mutate(entry_date = as.Date(entry_date)) %>% 
  arrange(nhc, entry_date) %>% 
  group_by(nhc) %>% 
  mutate(
    prev_date = lag(entry_date),
    diff_days = as.integer(entry_date - prev_date),
    
    # primer registro siempre inicia episodio
    new_episode_flag = if_else(
      is.na(diff_days) | diff_days > N_days,
      1L,
      0L
    ),
    
    # acumulado → identificador de episodio
    episode_id = cumsum(new_episode_flag),
    patient_episode = paste0(nhc, "_", episode_id)
  ) %>% 
  ungroup() %>% 
  group_by(patient_episode) %>% 
  mutate(
    tipo_episodio = if_else(
      all(encode_fis == "0000"),
      "Episodio Negativo",
      "Episodio Positivo"
    )
  ) %>% 
  arrange(entry_date, .by_group = TRUE) %>%
  mutate(
    episode_day = as.integer(entry_date - min(entry_date, na.rm = TRUE)),
    n_samples_episode = n(),
    n_samples_cat = case_when(
      n_samples_episode >= 1  & n_samples_episode <= 5  ~ "1_5",
      n_samples_episode >= 6  & n_samples_episode <= 10 ~ "6_10",
      n_samples_episode >= 11 & n_samples_episode <= 15 ~ "11_15",
      n_samples_episode > 20                          ~ ">20",
      TRUE ~ "16–20"
    )
  )%>%
  ungroup() %>% 
  mutate(across(where(is.character), stri_enc_toutf8))

write.csv(df_episodes, here("data", "episodes.csv"))
