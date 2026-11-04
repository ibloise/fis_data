library(tidyverse)
library(lubridate)


#TODO IMPORTANTE: No está adecuadamente sobreescrito el CULTURE_CARBA en las muestras con más de un aislado
# El Culture Carba debería ser la suma binaria de todos los organismos de esa muestra, pero en las ultimas comprobaciones 
# he visto que eso no es así. Hay que corregirlo.

# El efecto lo produce la búsqueda retrospectiva. Cuando el organismo productor de carbapenemasa está en una muestra previa
# Pero es solo uno de ellos, solo registramos SU línea. Pero en algun momento sobreescribo con el resultado de la propia muestra
# sin control. Hay que ajustarlo para que solo sobreescriba cuando RESULT IN sea igual a sample.-> HECHO
#-------------Declaración de funciones-------------#

source("R/carbas.R")

#------------- Carga de datos -------------#

cultures <- openxlsx::read.xlsx('data/cross_cultures.xlsx', sheet = "Sheet1")
patients <- openxlsx::read.xlsx("data/metadata_pacientes.xlsx")
cqs <- read.csv("data/FIS_CQs.csv")
rectales <- read_csv("data/rectales_microb.csv", col_types = cols(sample_number = col_character()))
#------------- Limpieza y cruce -------------#

# Arreglo de las fechas
cultures <- cultures %>%
  mutate(
    entry_date_origin = as.Date(entry_date_origin, origin = "1899-12-30"),
    entry_date_result = as.Date(entry_date_result, origin = "1899-12-30")
  )

patients <- patients %>% 
  mutate(
    birthdate = as.Date(birthdate, origin = "1899-12-30")
  )

# Microorganismos en la tabla:

levels(as.factor(cultures$microorg_name_origin))

# Microorganismos que deben eliminarse (no deberían haber llegado pero aquí están)
NON_VALUE_MICROORG = c("Candida", "Acinetobacter", "Stenotrophomonas", "Enterococcus")
pattern <- str_c(NON_VALUE_MICROORG, collapse = "|")

# Eliminado de organismos y corrección general de los cultivos.
#TODO: Esto no ha funcionado y más abajo he tenido que introducir otra función. Arreglar
clean_cultures <- cultures %>% 
  select(
    nhc_number_origin,sample_origin, entry_date_origin, encoded_carba_fis_origin, result_interpretate_origin,
    microorg_code, microorg_name_origin, encoded_carba_org_origin, global_encoded_origin,result_in, entry_date_result, 
    result_interpretate_result, microorg_name_result, encoded_carba_org_result, global_encoded_result, valid_global_encoded
    ) %>% 
  filter(
    !is.na(valid_global_encoded), result_interpretate_origin != "Non-process culture"
    ) %>% 
  mutate(
    microorg_code = if_else(result_interpretate_origin == "Negative Culture", "0", microorg_code),
    microorg_name_origin = if_else(result_interpretate_origin == "Negative Culture", "0", microorg_name_origin)
    ) %>% 
  mutate(
    is_non_value = str_detect(microorg_name_origin, pattern),
    microorg_code = if_else(is_non_value, "0", microorg_code),
    microorg_name_origin = if_else(is_non_value, "0", microorg_name_origin)
  ) %>%
  mutate(
    is_non_value = str_detect(microorg_name_result, pattern),
    microorg_name_result = if_else(is_non_value, "0", microorg_name_result)
  ) %>% 
  mutate(
    result_interpretate_result = if_else(microorg_name_result == "0", "Negative Culture", result_interpretate_result),
    result_interpretate_origin = if_else(microorg_name_origin == "0", "Negative Culture", result_interpretate_origin)
  ) %>% 
  select(-is_non_value) %>% 
  distinct() %>% 
  left_join(patients, by = c("nhc_number_origin" = "nhc_number")) %>% 
  mutate(
    age = floor(time_length(interval(birthdate, entry_date_origin), "years")),
    adults = if_else(age >= 18, "Adulto", "Pediatrico")
  )

table(clean_cultures$microorg_name_origin, clean_cultures$result_interpretate_origin)

tab <- table(
  clean_cultures$microorg_name_origin,
  clean_cultures$result_interpretate_origin
)
tab_ord <- tab[order(rowSums(tab), decreasing = TRUE), ]
tab_ord


# Comenzamos la simplificación de la tabla:

clean_cultures <- clean_cultures %>% 
  mutate(
    result_itself = sample_origin == result_in,
    entry_date_result = if_else(
      is.na(entry_date_result) & result_itself,
      entry_date_origin,
      entry_date_result
    ),
    result_interpretate_result = if_else(
      is.na(result_interpretate_result) & result_itself,
      result_interpretate_origin,
      result_interpretate_result
    ),
    microorg_name_result = if_else(
      is.na(microorg_name_result) & result_itself,
      microorg_name_origin,
      microorg_name_result
    ),
    encoded_carba_org_result = if_else(
      is.na(encoded_carba_org_result) & result_itself,
      encoded_carba_org_origin,
      encoded_carba_org_result
    ),
    global_encoded_result = if_else(
      is.na(global_encoded_result) & result_itself,
      global_encoded_origin,
      global_encoded_result
    )
  ) %>% 
  filter(!is.na(result_interpretate_result))

clean_cultures %>% 
  mutate(
    same_org = microorg_name_origin == microorg_name_result
  ) %>% 
  filter(same_org == FALSE)


tab <- table(
  clean_cultures$microorg_name_result,
  clean_cultures$result_interpretate_result
)
tab_ord <- tab[order(rowSums(tab), decreasing = TRUE), ]
tab_ord

clean_cultures %>% 
  group_by(sample_origin) %>% 
  summarise(n = n_distinct(result_interpretate_origin)) %>% 
  filter(n > 2)

clean_cultures <- clean_cultures %>% 
  rename(
    nhc = nhc_number_origin,
    sample = sample_origin,
    entry_date = entry_date_origin,
    carba_encoded_fis = encoded_carba_fis_origin,
    culture_result = result_interpretate_result,
    microorg = microorg_name_result,
    carba_encoded_org = encoded_carba_org_result,
    carba_encoded_culture = global_encoded_result
  )

clean_cultures_to_write <- clean_cultures %>% 
  select(
    nhc, 
    sex, 
    age, 
    adults,
    sample,
    entry_date,
    culture_result,
    microorg,
    carba_encoded_org,
    carba_encoded_culture,
    carba_encoded_fis,
    result_in,
    entry_date_result,
    result_itself
  )


# Se desempaquetan los 3 tipos de datos
clean_cultures_to_write <- unpack_carba_df(clean_cultures_to_write, carba_encoded_org, suffix = "_org")
clean_cultures_to_write <- unpack_carba_df(clean_cultures_to_write, carba_encoded_culture, suffix = "_culture")
clean_cultures_to_write <- unpack_carba_df(clean_cultures_to_write, carba_encoded_fis, suffix = "_fis")

# Vamos con los Cts:

cqs_delta <- cqs %>% 
  mutate(
    run_dated = as.POSIXct(run_started, format = "%Y-%m-%d %H:%M:%S", tz="Europe/Madrid")
    ) %>%
  arrange(sample, target, desc(run_dated)) %>% 
  distinct(sample, target, .keep_all = TRUE) %>% 
  filter(
    target != "Screening", cq_16S != "NULL", !str_starts(sample, "^[C-c]")) %>%
  mutate(
    cq_16S = as.numeric(cq_16S),
    
    # Imputación por mediana -> Valorar cambiar a imputación del otro Run
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
  
cqs_delta %>% 
  ggplot(aes(x=target, y = log_delta)) + geom_boxplot()

cqs_delta %>% 
  ggplot(aes(x=cq_16S, y=log_delta)) + geom_point()


cqs_delta %>% 
  ggplot(aes(x=cq_16S, y=log_delta)) + geom_point() +facet_wrap(~carba)

cqs_delta %>% 
  ggplot(aes(x=target, y=cq_16S)) + geom_boxplot()

cqs_delta %>% 
  ggplot(aes(x=cq_16S, fill = carba)) + geom_density() + facet_wrap(~carba)

cqs_delta %>% 
  filter(cq>0) %>% 
  ggplot(aes(x=cq, fill = carba)) + geom_density() + facet_wrap(~carba)


max(cqs_delta$log_delta, na.rm = TRUE)

cqs_delta %>% 
  select(-melting_data) %>% 
  filter(cq_16S == 0, !is.na(delta))

cqs_delta %>% 
  select(-melting_data) %>% 
  filter(cq_16S > 35, !is.na(delta))

# Hay que pivotar la tabla, y comprobar que muestras no están en la otra tabla 
# y cuales de la otra tabla no están en esta. -> Hecho, pendiente de buscar una explicación TODO

wide_cq_delta <- cqs_delta %>% 
  pivot_wider(id_cols = sample, names_from = carba, values_from = log_delta,  names_prefix = "log_delta_")

# Vamos a arreglar las muestras sin el 0

wide_cq_delta <- wide_cq_delta %>% 
  mutate(
    sample= if_else(str_length(sample) == 9, 
                    sample, 
                    paste0("0", sample)
                    )
    )

# Muestras que tienen resultado en la tabla de cruce con cultivos pero no en la 
# de cq delta. Se han filtrado los FIS negativos -> ¿Mover a checks.R?
anti_join(clean_cultures_to_write, wide_cq_delta, by = "sample") %>% 
  filter(carba_encoded_fis != "0000")

# Muestras que tienen valor en cq delta pero no aparecen en cultivo. Hay que revisar 
# Donde se habrán perdido. 

anti_join(wide_cq_delta, clean_cultures_to_write, by = "sample") %>%  view()

duplicated_s <- clean_cultures_to_write %>% 
  group_by(sample) %>% 
  summarise(n_results = n_distinct(culture_result)) %>% 
  filter(n_results > 1) %>% 
  arrange(sample) %>% 
  select(sample) %>% 
  pull()

clean_cultures_to_write %>% 
  filter(sample %in% duplicated_s)

# Siguen existiendo duplicados, hay que retirarlos
clean_cultures_to_write_deduplicated <- clean_cultures_to_write %>% 
  group_by(sample) %>% 
  mutate(
    has_positive = any(culture_result == "Positive Culture")
  ) %>% 
  filter(
    (first(has_positive) & culture_result == "Positive Culture") |
      (!first(has_positive) & row_number() == 1)
  ) %>% 
  ungroup() %>% 
  select(-has_positive)

duplicated_s <- clean_cultures_to_write_deduplicated %>% 
  group_by(sample) %>% 
  summarise(n_results = n_distinct(culture_result)) %>% 
  filter(n_results > 1) %>% 
  arrange(sample) %>% 
  select(sample) %>% 
  pull()


clean_cultures_to_write_deduplicated %>% 
  filter(duplicated(select(., sample)) |
           duplicated(select(., sample), fromLast = TRUE)) %>% 
  filter(culture_result == "Positive Culture")


clean_cultures_to_write_final <- clean_cultures_to_write_deduplicated %>% 
  left_join(wide_cq_delta, by = "sample")

clean_cultures_to_write_final$year_week <- isoweek(clean_cultures_to_write_final$entry_date)
clean_cultures_to_write_final$year <- year(clean_cultures_to_write_final$entry_date)

gfhs <- rectales %>% 
  select(sample_number, origin_code, service_code) %>% 
  distinct()


clean_cultures_to_write_final <- left_join(clean_cultures_to_write_final, gfhs, by=c("sample" = "sample_number"))

openxlsx::write.xlsx(clean_cultures_to_write_final, "data/clean_data.xlsx")

################
N_days <- 30
#################

df_episodes <- final_df %>% 
  select(-microorg, - carba_encoded_org, -OXA_org, -VIM_org, -KPC_org, -NDM_org) %>% 
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
      all(carba_encoded_fis == "0000"),
      "Episodio Negativo",
      "Episodio Positivo"
    )
  ) %>% 
  arrange(entry_date, .by_group = TRUE) %>%
  mutate(
    episode_day = as.integer(entry_date - min(entry_date, na.rm = TRUE)),
    n_samples_episode = n(),
    n_samples_cat = case_when(
      n_samples_episode >= 1  & n_samples_episode <= 5  ~ "1–5",
      n_samples_episode >= 6  & n_samples_episode <= 10 ~ "6–10",
      n_samples_episode >= 11 & n_samples_episode <= 15 ~ "11–15",
      n_samples_episode > 20                          ~ ">20",
      TRUE ~ "16–20"
    )
  )%>%
  ungroup()


openxlsx::write.xlsx(df_episodes, "data/episodios.xlsx")
