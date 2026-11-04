library(tidyverse)
library(vcd)
library(caret)
library(lubridate)
library(zoo)
library(reshape2)

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

cultures <- openxlsx::read.xlsx('cross_cultures.xlsx')
patients <- openxlsx::read.xlsx("metadata_pacientes.xlsx")


#------------- Limpieza y cruce -------------#


cultures <- cultures %>%
  mutate(
    entry_date_origin = as.Date(entry_date_origin, origin = "1899-12-30"),
    entry_date_result = as.Date(entry_date_result, origin = "1899-12-30")
  )

patients <- patients %>% 
  mutate(
    birthdate = as.Date(birthdate, origin = "1899-12-30")
  )

# Microorganismos que deben eliminarse (no deberían haber llegado pero aquí están)
NON_VALUE_MICROORG = c("Candida", "Acinetobacter", "Stenotrophomonas", "Enterococcus")
pattern <- str_c(NON_VALUE_MICROORG, collapse = "|")

clean_cultures <- cultures %>% 
  select(nhc_number_origin,sample_origin, entry_date_origin, encoded_carba_fis_origin, result_interpretate_origin,
         microorg_code, microorg_name_origin, encoded_carba_org_origin, global_encoded_origin,
         result_in, entry_date_result, result_interpretate_result, microorg_name_result, encoded_carba_org_result, global_encoded_result, valid_global_encoded
         ) %>% 
  filter(!is.na(valid_global_encoded),
         result_interpretate_origin != "Non-process culture") %>% 
  mutate(microorg_code = if_else(result_interpretate_origin == "Negative Culture", "0", microorg_code),
         microorg_name_origin = if_else(result_interpretate_origin == "Negative Culture", "0", microorg_name_origin)) %>% 
  mutate(
    is_non_value = str_detect(microorg_name_origin, pattern),
    microorg_code = if_else(is_non_value, "0", microorg_code),
    microorg_name_origin = if_else(is_non_value, "0", microorg_name_origin)
  ) %>%
  mutate(
    result_interpretate_origin = if_else(microorg_code == "0", "Negative Culture", result_interpretate_origin)
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
# Eliminamos los registros en 0 que han quedado colgando de los Positive cultures
clean_cultures <- clean_cultures %>%
  group_by(sample_origin) %>%
  mutate(
    n_pos = sum(result_interpretate_origin == "Positive Culture", na.rm = TRUE)
  ) %>%
  ungroup() %>% 
  filter(
    !(result_interpretate_origin == "Positive Culture" &
        n_pos > 1 &
        microorg_code == "0")
  ) %>%
  select(-n_pos) %>% 
  group_by(sample_origin, microorg_code) %>%
  # Hay fechas con NA. Se deben limpiar
  arrange(
    desc(!is.na(entry_date_result)),   # primero las que tienen fecha
    desc(entry_date_result)             # y la más reciente si hay varias
  ) %>%
  slice(1) %>%
  ungroup()

  
# Tabla deduplicada por muestras: se eliminan los resultados de microorganismos
cultures_by_sample <- clean_cultures %>% 
  select(nhc_number_origin, sample_origin, entry_date_origin, 
         encoded_carba_fis_origin, result_interpretate_origin, 
         global_encoded_origin,result_in, entry_date_result, 
         result_interpretate_result, global_encoded_result, valid_global_encoded, 
         sex,birthdate, age, adults
  ) %>% 
  distinct() %>% 
  group_by(sample_origin) %>%
  # Hay fechas con NA. Se deben limpiar
  arrange(
    desc(!is.na(entry_date_result)),   # primero las que tienen fecha
    desc(entry_date_result)             # y la más reciente si hay varias
  ) %>%
  slice(1) %>%
  ungroup() ## Añadimos la info de pacientes


# ------------------ Análisis de datos ------------------- #


# ----------- Muestras en el proyecto ------------ #

cultures_by_sample %>% 
  select(sample_origin) %>% 
  distinct() %>% 
  nrow()

# Tendencia temporal

cultures_by_sample %>% 
  group_by(entry_date_origin) %>% 
  summarise(n = n(), .groups = "drop") %>% 
  arrange(entry_date_origin) %>% 
  mutate(
    n_ma7 = rollmean(n, k = 14, fill = NA, align = "center")
  ) %>% 
ggplot(aes(x = entry_date_origin)) +
  geom_line(aes(y = n), alpha = 0.3) +          # línea cruda al fondo
  geom_line(aes(y = n_ma7), linewidth = 1) +    # media móvil encima
  labs(
    x = "Fecha de petición",
    y = "Número de muestras",
    title = "Muestras por día con media móvil de 14 días"
  ) +
  theme_minimal()

# Muestras por año

cultures_by_sample %>% 
  mutate(
    year = year(entry_date_origin)
  ) %>% 
  group_by(year) %>% 
  summarise(
    n = n()
  )

# ----------- Pacientes en el proyecto ------------ #

cultures_by_sample %>% 
  select(nhc_number_origin) %>% 
  distinct() %>% 
  nrow()

# Extraemos los pacientes del FIS sin el resto de datos para aligerar calculos

fis_patients <- cultures_by_sample %>% 
  group_by(nhc_number_origin) %>%
  slice_min(entry_date_origin, n = 1, with_ties = FALSE) %>%
  ungroup() %>%
  select(nhc_number_origin, age, sex, adults) %>% 
  distinct()

# Tabla básica de demos:

fis_patients %>% 
  mutate(
    sex = factor(sex),
    age = as.numeric(age)
  ) %>% 
  group_by(sex) %>% 
  summarise(
    n = n(),
    
    # Edad
    age_median = median(age, na.rm = TRUE),
    age_IQR = IQR(age, na.rm = TRUE),
    # Edad
    age_avg = mean(age, na.rm = TRUE),
    age_sd = sd(age, na.rm = TRUE)
  )

# Pediatria vs Adultos

fis_patients %>% 
  summarise(
    n = n(),
    
    # Frecuencias absolutas
    n_ped = sum(adults == "Pediatrico", na.rm = TRUE),
    n_adu = sum(adults == "Adulto", na.rm = TRUE),
    
    # Frecuencias relativas
    prop_ped = mean(adults == "Pediatrico", na.rm = TRUE),
    prop_adu = mean(adults == "Adulto", na.rm = TRUE),
  )

fis_patients %>% 
  ggplot(aes(x=age)) + geom_histogram()

fis_patients %>% 
  ggplot(aes(x=age)) + geom_histogram() + facet_wrap(~adults, ncol=1)

# Analisis diferenciado

fis_pedia <- filter(fis_patients, adults == "Pediatrico")

fis_pedia %>% 
  mutate(
    sex = factor(sex),
    age = as.numeric(age)
  ) %>% 
  group_by(sex) %>% 
  summarise(
    n = n(),
    # Edad
    age_median = median(age, na.rm = TRUE),
    age_IQR = IQR(age, na.rm = TRUE),
    # Edad
    age_avg = mean(age, na.rm = TRUE),
    age_sd = sd(age, na.rm = TRUE)
  )


fis_adultos <- filter(fis_patients, adults == "Adulto")

fis_adultos %>% 
  mutate(
    sex = factor(sex),
    age = as.numeric(age)
  ) %>% 
  group_by(sex) %>% 
  summarise(
    n = n(),
    # Edad
    age_median = median(age, na.rm = TRUE),
    age_IQR = IQR(age, na.rm = TRUE),
    # Edad
    age_avg = mean(age, na.rm = TRUE),
    age_sd = sd(age, na.rm = TRUE)
  )

## Profundización de pacientes

# Hay pacientes que tienen resultados negativos y positivos. Hay que pensar como manipular correctamente esto.
cultures_by_sample %>% 
  select(nhc_number_origin, age, sex, adults, result_interpretate_origin) %>% 
  distinct(nhc_number_origin, result_interpretate_origin, .keep_all = TRUE) %>% 
  group_by(result_interpretate_origin) %>% 
  summarise(
    n=n(),
    # Frecuencias absolutas
    n_male = sum(sex == "M", na.rm = TRUE),
    n_female = sum(sex == "F", na.rm = TRUE),
    
    # Frecuencias relativas
    prop_male = mean(sex == "M", na.rm = TRUE),
    prop_female = mean(sex == "F", na.rm = TRUE),
    
    # Edad
    age_median = median(age, na.rm = TRUE),
    age_IQR = IQR(age, na.rm = TRUE)
  )

# Número de torundas positivas por paciente:

# Nota: Hay que ir pensando en crear una clasificación por episodios ¿Savana? ¿O construirlo mediante pruebas consecutivas?


# ----------- Análisis de carbapenemasas (de FIS) ------------ #

# Expandimos de nuevo
cultures_by_sample <- cultures_by_sample %>% 
  unpack_carba_df(encoded_carba_fis_origin, suffix = "_fis") %>% 
  unpack_carba_df(valid_global_encoded, suffix = "_sil") 



# ---- OXA-48 ----#

cultures_by_sample %>% 
  group_by(OXA_fis) %>% 
  summarise(n=n())

cultures_by_sample %>% 
  filter(OXA_fis == 1) %>% 
  group_by(entry_date_origin) %>% 
  summarise(n=n()) %>% 
  ungroup() %>% 
arrange(entry_date_origin) %>% 
  mutate(
    n_ma7 = rollmean(n, k = 14, fill = NA, align = "center")
  ) %>% 
  ggplot(aes(x = entry_date_origin)) +
  geom_line(aes(y = n), alpha = 0.3) +          # línea cruda al fondo
  geom_line(aes(y = n_ma7), linewidth = 1) +    # media móvil encima
  labs(
    x = "Fecha de petición",
    y = "Número de muestras",
    title = "OXA48 por día con media móvil de 14 días"
  ) +
  theme_minimal()


# ---- VIM ----#

cultures_by_sample %>% 
  group_by(VIM_fis) %>% 
  summarise(n=n())

cultures_by_sample %>% 
  filter(VIM_fis == 1) %>% 
  group_by(entry_date_origin) %>% 
  summarise(n=n()) %>% 
  ungroup() %>% 
  arrange(entry_date_origin) %>% 
  mutate(
    n_ma7 = rollmean(n, k = 14, fill = NA, align = "center")
  ) %>% 
  ggplot(aes(x = entry_date_origin)) +
  geom_line(aes(y = n), alpha = 0.3) +          # línea cruda al fondo
  geom_line(aes(y = n_ma7), linewidth = 1) +    # media móvil encima
  labs(
    x = "Fecha de petición",
    y = "Número de muestras",
    title = "VIM por día con media móvil de 14 días"
  ) +
  theme_minimal()

# ---- KPC ----#

cultures_by_sample %>% 
  group_by(KPC_fis) %>% 
  summarise(n=n())

cultures_by_sample %>% 
  filter(KPC_fis == 1) %>% 
  group_by(entry_date_origin) %>% 
  summarise(n=n()) %>% 
  ungroup() %>% 
  arrange(entry_date_origin) %>% 
  mutate(
    n_ma7 = rollmean(n, k = 14, fill = NA, align = "center")
  ) %>% 
  ggplot(aes(x = entry_date_origin)) +
  geom_line(aes(y = n), alpha = 0.3) +          # línea cruda al fondo
  geom_line(aes(y = n_ma7), linewidth = 1) +    # media móvil encima
  labs(
    x = "Fecha de petición",
    y = "Número de muestras",
    title = "KPC por día con media móvil de 14 días"
  ) +
  theme_minimal()


# ---- NDM ---- #

cultures_by_sample %>% 
  group_by(NDM_fis) %>% 
  summarise(n=n())

cultures_by_sample %>% 
  filter(NDM_fis == 1) %>% 
  group_by(entry_date_origin) %>% 
  summarise(n=n()) %>% 
  ungroup() %>% 
  arrange(entry_date_origin) %>% 
  mutate(
    n_ma7 = rollmean(n, k = 14, fill = NA, align = "center")
  ) %>% 
  ggplot(aes(x = entry_date_origin)) +
  geom_line(aes(y = n), alpha = 0.3) +          # línea cruda al fondo
  geom_line(aes(y = n_ma7), linewidth = 1) +    # media móvil encima
  labs(
    x = "Fecha de petición",
    y = "Número de muestras",
    title = "KPC por día con media móvil de 14 días"
  ) +
  theme_minimal()


# ---- Co-ocurrencias ---- # 

carba_df <- cultures_by_sample %>%
  select(sample_origin, OXA_fis, VIM_fis, NDM_fis, KPC_fis)

carba_mat <- as.matrix(carba_df %>% select(OXA_fis, VIM_fis, NDM_fis, KPC_fis))

# Calculo de coocurrencias
cooccur <- t(carba_mat) %*% carba_mat
cooccur

# Proporción: división de cada fila coocur por su diagonal
cooccur_prop <-round(sweep(cooccur, 1, diag(cooccur), "/"), 4)

# La proporción es por filas! Es decir, si la fila es OXA_fis
# y la columna es VIM_fis y el valor es 0.16 quiere decir que 
# el 16% de las OXAs tienen VIM
# Si la fila es VIM y la columna OXA y el valor 0.21, quiere decir
# que el 21% de las VIM tienen OXA.
cooccur_prop

pattern_freq <- carba_df %>%
  count(OXA_fis, VIM_fis, NDM_fis, KPC_fis, name = "n") %>%
  mutate(
    n_carbas = OXA_fis + VIM_fis + NDM_fis + KPC_fis
  ) %>% 
  arrange(desc(n))

pattern_freq

# TODO: Ver total de perdidas por FIS y total de perdidas por SIL
# TODO: Sacar las cargas con la ecuación delta log(2^-(cq-cq_16S)


melt(cooccur) %>%
  ggplot(aes(Var1, Var2, fill = value)) +
  geom_tile() +
  geom_text(aes(label = value)) +
  scale_fill_gradient(low = "white", high = "steelblue") +
  labs(title = "Co-ocurrencia de carbapenemasas")


# ----------------- Microorganismos

# TODO: revisar los recuentos de organismos. Está contando todas las ocurrencias
# no las que tienen carbapenemasas.
table(clean_cultures$microorg_name_origin, clean_cultures$encoded_carba_org_origin)
table(clean_cultures$microorg_name_origin, clean_cultures$encoded_carba_fis_origin)

table(cultures_by_sample$encoded_carba_fis_origin, cultures_by_sample$valid_global_encoded, dnn = c('FIS', 'SIL'))

# Reclasificamos
cultures_by_sample_cat<- cultures_by_sample %>%
  mutate(
    OXA_fis = factor(OXA_fis, levels = c(0, 1), labels = c("Neg", "Pos")),
    OXA_sil = factor(OXA_sil, levels = c(0, 1), labels = c("Neg", "Pos")),
    VIM_fis = factor(VIM_fis, levels = c(0, 1), labels = c("Neg", "Pos")),
    VIM_sil = factor(VIM_sil, levels = c(0, 1), labels = c("Neg", "Pos")),
    KPC_fis = factor(KPC_fis, levels = c(0, 1), labels = c("Neg", "Pos")),
    KPC_sil = factor(KPC_sil, levels = c(0, 1), labels = c("Neg", "Pos")),
    NDM_fis = factor(NDM_fis, levels = c(0, 1), labels = c("Neg", "Pos")),
    NDM_sil = factor(NDM_sil, levels = c(0, 1), labels = c("Neg", "Pos"))
  )


cm <- confusionMatrix(cultures_by_sample_cat$OXA_fis, cultures_by_sample_cat$OXA_sil, dnn = c('FIS', 'SIL'), positive = "Pos")
cm$table

cm <- confusionMatrix(cultures_by_sample_cat$VIM_fis, cultures_by_sample_cat$VIM_sil, dnn = c('FIS', 'SIL'), positive = "Pos")
cm$table

cm <- confusionMatrix(cultures_by_sample_cat$KPC_fis, cultures_by_sample_cat$KPC_sil, dnn = c('FIS', 'SIL'), positive = "Pos")
cm$table

cm <- confusionMatrix(cultures_by_sample_cat$NDM_fis, cultures_by_sample_cat$NDM_sil, dnn = c('FIS', 'SIL'), positive = "Pos")
cm$table


# Analizamos por paciente

cultures_one_patient <- cultures_by_sample_cat %>% 
  arrange(nhc_number_origin, entry_date_origin) %>% 
  group_by(nhc_number_origin) %>% 
  slice(1) %>% 
  ungroup()
  

cm <- confusionMatrix(cultures_one_patient$OXA_fis, cultures_one_patient$OXA_sil, dnn = c('FIS', 'SIL'), positive = "Pos")
cm$table

cm <- confusionMatrix(cultures_one_patient$VIM_fis, cultures_one_patient$VIM_sil, dnn = c('FIS', 'SIL'), positive = "Pos")
cm$table

cm <- confusionMatrix(cultures_one_patient$KPC_fis, cultures_one_patient$KPC_sil, dnn = c('FIS', 'SIL'), positive = "Pos")
cm$table

cm <- confusionMatrix(cultures_one_patient$NDM_fis, cultures_one_patient$NDM_sil, dnn = c('FIS', 'SIL'), positive = "Pos")
cm$table

