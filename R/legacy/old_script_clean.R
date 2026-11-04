library(tidyverse)
library(lubridate)
# library(ggpmisc)
library(ggpubr)


#TODO IMPORTANTE: No está adecuadamente sobreescrito el CULTURE_CARBA en las muestras con más de un aislado
# El Culture Carba debería ser la suma binaria de todos los organismos de esa muestra, pero en las ultimas comprobaciones 
# he visto que eso no es así. Hay que corregirlo.

# El efecto lo produce la búsqueda retrospectiva. Cuando el organismo productor de carbapenemasa está en una muestra previa
# Pero es solo uno de ellos, solo registramos SU línea. Pero en algun momento sobreescribo con el resultado de la propia muestra
# sin control. Hay que ajustarlo para que solo sobreescriba cuando RESULT IN sea igual a sample.-> HECHO
#-------------Declaración de funciones-------------#

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

#------------- Carga de datos -------------#

cultures <- openxlsx::read.xlsx('data/cross_cultures.xlsx', sheet = "Sheet1")
patients <- openxlsx::read.xlsx("data/metadata_pacientes.xlsx")
cqs <- read.csv("data/FIS_CQs.csv")

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

# clean_cultures <- clean_cultures %>% 
#   mutate(
#     entry_date_result = coalesce(entry_date_result, entry_date_origin),
#     result_interpretate_result = coalesce(result_interpretate_result, result_interpretate_origin),
#     microorg_name_result = coalesce(microorg_name_result, microorg_name_origin),
#     encoded_carba_org_result = coalesce(encoded_carba_org_result, encoded_carba_org_origin),
#     global_encoded_result = coalesce(global_encoded_result, global_encoded_origin),
#     result_itself = sample_origin == result_in
#   )

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


# cqs_delta %>% 
#   ggplot(aes(y = cq, x = log_delta)) +
#   geom_point() +
#   geom_smooth(method = "lm") +
#   stat_poly_eq(
#     aes(label = paste(..eq.label.., ..rr.label.., sep = "~~~")),
#     formula = y ~ x,
#     parse = TRUE
#   ) +
#   facet_wrap(~carba)


max(cqs_delta$log_delta, na.rm = TRUE)


cqs_delta %>% 
  select(-melting_data) %>% 
  filter(cq_16S == 0, !is.na(delta))

cqs_delta %>% 
  select(-melting_data) %>% 
  filter(cq_16S > 35, !is.na(delta))

# Hay que pivotar la tabla, y comprobar que muestras no están en la otra tabla 
# y cuales de la otra tabla no están en esta.

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
# de cq delta. Se han filtrado los FIS negativos
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


# Vamos a hacer algunas comprobaciones

final_df <- clean_cultures_to_write_final


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

plot_carba_tp(final_df, "OXA", "log_delta_OXA48")
plot_carba_tp(final_df, "VIM")
plot_carba_tp(final_df, "NDM")
plot_carba_tp(final_df, "KPC")

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

carba_df <- bind_rows(
  get_carba_df(final_df, "OXA", "log_delta_OXA48"),
  get_carba_df(final_df, "VIM"),
  get_carba_df(final_df, "NDM"),
  get_carba_df(final_df, "KPC")
)

carba_df %>%
  ggplot(aes(x = TP, y = log_delta)) +
  geom_boxplot() +
  stat_compare_means(
    method = "wilcox.test",
    label = "p.format"
  ) +
  facet_wrap(~ carbapenemasa, scales = "free_y")


patients_swabs_positives <- final_df %>% 
  filter(carba_encoded_fis != "0000") %>% 
  select(nhc, sample, culture_result) %>% 
  distinct() %>% 
  group_by(nhc, culture_result) %>% 
  summarise(n=n())

patients_swabs_positives <- patients_swabs_by_result %>% 
  filter(culture_result == "Positive Culture", n>1) %>% 
  select(nhc) %>% pull()

swabs_positives_greaterone <- final_df %>% 
  filter(nhc %in% patients_swabs_positives, culture_result == "Positive Culture", carba_encoded_fis != "0000") %>% 
  arrange(nhc, entry_date)


swabs_positives_greaterone %>% 
  arrange(nhc, entry_date) %>% 
  group_by(nhc) %>% 
  mutate(sample_order = row_number()) %>% 
  ggplot(aes(x = sample_order, y = carba_encoded_fis, group = nhc)) +
  geom_line(alpha = 0.1)


patients_swabs_by_result %>% 
  filter(culture_result == "Positive Culture", n>15)

final_df %>% 
  filter(nhc==970795) %>%
  arrange(entry_date) %>% 
  view()

# IDEAS INTERESANTES PARA SEGUIR (Este script era en origen para dejar la tabla limpia, hay que splitearlo a otros):
# - Desarrollar análisis de redes(cambios de estado, cambios de carba)
# - Carga por microorganismo causal
# - Dinámicas 1 carbapenemasa vs varias.
# Hay que pensar en una categorización de episodios: Muestras seguidas hasta gap de N dias
N_days <- 30

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

openxlsx::write.xlsx(final_df, "data/clean_data.xlsx")
openxlsx::write.xlsx(df_episodes, "data/episodios.xlsx")

ggplot(df_episodes, aes(x=n_samples_episode)) + geom_histogram()
df_episodes %>% 
  select(nhc, episode_id) %>% 
  distinct() %>% 
  ggplot(aes(x=episode_id))+ geom_histogram(binwidth = 1)


df_episodes %>% 
  filter(episode_day > 30) %>% 
  View(.)
df_episodes %>% 
  filter(tipo_episodio == "Episodio Positivo", n_samples_episode >1) %>% 
  arrange(nhc, entry_date) %>% 
  group_by(patient_episode) %>% 
  ggplot(aes(x = episode_day, y = carba_encoded_fis, group = patient_episode, color=nhc)) +
  geom_line(alpha = 0.5) + theme(legend.position = "none") + facet_wrap(~n_samples_cat, scale = "free")
  

df_episodes %>% 
  filter(tipo_episodio == "Episodio Positivo", n_samples_episode >15) %>% 
  arrange(nhc, entry_date) %>% 
  group_by(patient_episode) %>% 
  ggplot(aes(x = episode_day, y = carba_encoded_fis, group = patient_episode, color=nhc)) +
  geom_line(alpha = 0.5) + theme(legend.position = "none")
  

df_episodes %>% 
  filter(tipo_episodio == "Episodio Positivo", n_samples_episode >15) %>% 
  arrange(nhc, entry_date) %>% 
  group_by(patient_episode) %>% 
  ggplot(aes(x = episode_day, y = carba_encoded_fis, group = patient_episode, color=nhc)) +
  geom_line(alpha = 0.8) #+ theme(legend.position = "none")
