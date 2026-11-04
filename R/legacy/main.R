
library(tidyverse)
library(here)
library(zoo)
library(gt)
library(patchwork)
library(reshape2)
library(ggpubr)
library(scales)

source(here("R", "readers.R"))
source(here("R", "transform.R"))

data <- read_clean_data() 

data_non_cultures <- withdraw_organism_in_clean_data(data) %>%
  distinct()

# OJO: sigue habiendo duplicados porque la búsqueda independiente de aislados
# puede acabar provocando que el result src de un aislamiento sea diferente del del otro

# Hay que plantear una solución para esto.
data_non_cultures %>% 
  count(sample) %>% 
  filter(n>1)


# Patients # 

data_non_cultures %>% 
  select(nhc) %>% 
  distinct() %>% 
  count()

patients <- data_non_cultures %>% 
  select(nhc, sex, birthdate, age) %>% 
  distinct()

# Valoramos que no haya pacientes con más de un registro demográfico:
patients %>% 
  group_by(nhc) %>% 
  filter(n()>1)

patients %>% 
  group_by(nhc) %>% 
  summarise(n_sex = n_distinct(sex), .groups = "drop") %>% 
  filter(n_sex > 1)

patients %>% 
  group_by(nhc) %>% 
  summarise(n_birthdate = n_distinct(birthdate), .groups = "drop") %>% 
  filter(n_birthdate > 1)

patients %>% 
  group_by(nhc) %>% 
  summarise(n_age = n_distinct(age), .groups = "drop") %>% 
  filter(n_age > 1)

# Demográfico básico:

patients$age_cat <- if_else(patients$age <18, "Pediátrico", "Adulto")

tab <- patients %>% 
  count(sex) %>% 
  mutate(
    perc = n / sum(n) * 100
  ) %>% 
  mutate(
    label = sprintf("%d (%.1f%%)", n, perc)
  ) %>% 
  select(sex, label)


tab
patients %>% 
  summarise(
    median = median(age, na.rm= TRUE),
    IQR = IQR(age, na.rm = TRUE)
  )

adults <- patients %>% 
  filter(age_cat == "Adulto")

adults %>% 
  select(nhc) %>% 
  distinct() %>% 
  nrow()

adults %>% 
  count(sex) %>% 
  mutate(
    perc = n / sum(n) * 100
  ) %>% 
  mutate(
    label = sprintf("%d (%.1f%%)", n, perc)
  ) %>% 
  select(sex, label)


adults%>% 
  summarise(
    median = median(age, na.rm= TRUE),
    IQR = IQR(age, na.rm = TRUE)
  )

pedia <- patients %>% 
  filter(age_cat == "Pediátrico")

pedia %>% 
  select(nhc) %>% 
  distinct() %>% 
  nrow()

pedia %>% 
  count(sex) %>% 
  mutate(
    perc = n / sum(n) * 100
  ) %>% 
  mutate(
    label = sprintf("%d (%.1f%%)", n, perc)
  ) %>% 
  select(sex, label)


pedia%>% 
  summarise(
    median = median(age, na.rm= TRUE),
    IQR = IQR(age, na.rm = TRUE)
  )

sex_age_cat_barplot <- patients %>% 
  filter(!is.na(sex)) %>% 
  ggplot(aes(x = age_cat, fill = sex)) + 
  geom_bar() + 
  theme_bw() +
  labs(
    x = "Grupo de edad",
    y = "Número de pacientes",
    fill = "Sexo"
  )

ggsave(here("img", "sex_age_cat_barplot.png"))

age_histogram <- patients %>% 
  filter(!is.na(age), !is.na(age_cat)) %>% 
  ggplot(aes(x = age, fill = age_cat)) + 
  geom_histogram(binwidth = 5, color = "black") + 
  theme_bw() +
  labs(
    x = "Edad",
    y = "Número de pacientes",
    fill = "Grupo de edad"
  )

ggsave(here("img", "age_histogram.png"), age_histogram)

# Hisopos por pacientes

df_plot <- data_non_cultures %>% 
  filter(!is.na(nhc)) %>% 
  count(nhc, name = "n") %>% 
  count(n, name = "freq")

ggplot(df_plot, aes(x = n, y = freq)) +
  geom_col(
    fill = "#2C7FB8",
    color = "white",
    alpha = 0.9
  ) +
  scale_y_continuous(trans = scales::pseudo_log_trans(base = 10),
                     breaks = c(1, 2, 3, 5, 10, 20, 50, 100, 200, 500, 1000, 5000)) +
  scale_x_continuous(breaks = scales::pretty_breaks()) +
  labs(
    title = "Distribución del número de muestras por paciente",
    x = "Número de muestras por paciente",
    y = "Frecuencia de pacientes (log10)"
  ) +
  theme_minimal(base_size = 13) +
  theme(
    plot.title = element_text(face = "bold"),
    panel.grid.minor = element_blank()
  )

df_hist2 <- data_non_cultures %>% 
  filter(!is.na(nhc), !is.na(encode_fis)) %>% 
  mutate(adult = if_else(age >= 18, "Adulto", "Pediátrico")) %>% 
  group_by(nhc, adult) %>% 
  summarise(n = n(), .groups = "drop")

sample_patient_plot <- gghistogram(
  df_hist2,
  x = "n",
  fill = "adult",
  color = "adult",
  palette = c("Adulto" = "#4DBBD5", "Pediátrico" = "#E64B35"),
  binwidth = 1,
  boundary = 0.5,
  position = "identity",
  alpha = 0.35
) +
  scale_y_continuous(
    trans = scales::pseudo_log_trans(base = 10),
    breaks = c(1, 2, 3, 5, 10, 20, 50, 100, 200, 500, 1000, 5000)
  ) +
  scale_x_continuous(
    breaks = scales::pretty_breaks()
  ) +
  labs(
    title = "Distribución del número de muestras por paciente",
    x = "Número de muestras por paciente",
    y = "Frecuencia de pacientes",
    fill = "Resultado FIS",
    color = "Resultado FIS"
  ) +
  theme_minimal(base_size = 13) +
  theme(
    plot.title = element_text(face = "bold"),
    panel.grid.minor = element_blank()
  )

ggsave(here("img", "samples_patient_plot.png"), sample_patient_plot, width = 12, height = 6)
# Muestras 
swabs <- read_rectal_swabs()

n_samples_in_fis <- data_non_cultures %>% 
  filter(
    entry_date >= as.Date("2023-06-01"), # Habría que aplicar este corte a la otra también
  ) %>% 
  select(sample) %>%
  distinct() %>% 
  count()

n_samples_in_fis

data_non_cultures %>%
  filter(
    entry_date >= as.Date("2023-06-01"), # Habría que aplicar este corte a la otra también
  ) %>% 
  filter(is.na(nhc)) %>% 
  select(sample) %>% 
  distinct() %>% 
  count()

n_samples_in_fis_real <- data_non_cultures %>% 
  filter(
    entry_date >= as.Date("2023-06-01"), # Habría que aplicar este corte a la otra también
  ) %>% 
  filter(!is.na(nhc)) %>% 
  select(sample) %>%
  distinct() %>% 
  count()

n_samples_in_fis_real
n_swabs_processed <- swabs %>% 
  filter(
    entry_date >= as.Date("2023-06-01"), # Habría que aplicar este corte a la otra también
    ) %>% 
  filter(!(result_code %in% c("PEN", "NPR", "NSR", "C06", ".04", ".05", "MRE"))) %>% 
  select(sample_number) %>% 
  distinct() %>% 
  count()

n_swabs_processed
prop_processed <- n_samples_in_fis / n_swabs_processed
prop_processed

## IMG: Distribución de lo procesado

sample_day_distr <- data %>% 
  select(sample, entry_date) %>% 
  group_by(entry_date) %>% 
  summarise(n = n(), .groups = "drop") %>% 
  arrange(entry_date) %>% 
  mutate(
    n_ma7 = rollmean(n, k = 14, fill = NA, align = "center")
  ) %>% 
  ggplot(aes(x = entry_date)) +
  geom_line(aes(y = n), alpha = 0.3) +          # línea cruda al fondo
  geom_line(aes(y = n_ma7), linewidth = 1) +    # media móvil encima
  labs(
    x = "Fecha de petición",
    y = "Número de muestras",
    title = "Muestras por día con media móvil de 14 días"
  ) +
  theme_minimal() + scale_x_date(
  limits = as.Date(c("2023-06-01", "2025-05-31"))
) +
  scale_y_continuous(
    limits = c(0, 100)
  )



FIS <- data %>% 
  filter(!is.na(nhc)) %>% 
  select(sample, entry_date, ends_with('_specific')) %>% 
  distinct() %>% 
  select(-encode_carba_specific) %>% 
  pivot_longer(
    cols = ends_with("_specific")
  ) %>% 
  filter(value == 1) %>% 
  mutate(name = str_replace(name, "_specific", ""))



carba_days <- FIS %>% 
  group_by(name, entry_date) %>% 
  summarise(n = n(), .groups = "drop") %>% 
  arrange(name, entry_date) %>% 
  group_by(name) %>% 
  mutate(
    n_ma14 = rollmean(n, k = 14, fill = NA, align = "center")
  ) %>% 
  ungroup() %>% 
  ggplot(aes(x = entry_date)) +
  geom_line(aes(y = n), alpha = 0.3) +
  geom_line(aes(y = n_ma14), linewidth = 1) +
  facet_wrap(~ name) +
  labs(
    x = "Fecha de petición",
    y = "Número de muestras",
    title = "Detecciones por día con media móvil de 14 días"
  ) +
  theme_minimal() +
  scale_x_date(
    limits = as.Date(c("2023-06-01", "2025-05-31"))
  )

ggsave(here("img", "carba_dist_by_day.png"),carba_days)

# Análisis global

data %>% 
  filter(!is.na(nhc)) %>% 
  select(sample, encode_fis) %>% 
  filter(encode_fis == "0000") %>% 
  select(sample) %>% 
  distinct() %>% 
  count()

# NOTA: NO CUADRA ESTE CON EL NEGATIVO DE ARRIBA
FIS %>% 
  group_by(name) %>% 
  summarise(n = sum(value))

decode_carba_label <- function(x) {
  genes <- c("OXA", "VIM", "KPC", "NDM")
  
  sapply(x, function(code) {
    # limpiar
    code <- trimws(code)
    
    # validar
    if (!grepl("^[01]{4}$", code)) return(NA_character_)
    
    bits <- strsplit(code, "")[[1]]
    present <- genes[bits == "1"]
    
    if (length(present) == 0) {
      return("Negativo")
    } else {
      return(paste(present, collapse = "+"))
    }
  })
}

data %>% 
  filter(!is.na(nhc)) %>% 
  select(sample, encode_fis) %>% 
  distinct() %>% 
  mutate(carba_label = decode_carba_label(encode_fis)) %>% 
  filter(encode_fis != "0000") %>% 
  group_by(carba_label) %>% 
  summarise(n=n()) %>% 
  arrange(desc(n)) %>% 
  openxlsx::write.xlsx(here("data", "carbas_labeled.xlsx"))

summary_carbas <- data %>% 
  filter(!is.na(nhc)) %>% 
  select(sample, ends_with("_specific"), -encode_carba_specific) %>% 
  distinct() %>% 
  pivot_longer(cols = ends_with("_specific")) %>% 
  group_by(name) %>% 
  summarise(n = sum(value, na.rm = TRUE)) %>% 
  mutate(name = str_replace(name, "_specific", "")) %>% 
  mutate(name = factor(name, levels = c("OXA48", "VIM", "KPC", "NDM")))
  

carba_plot <- summary_carbas %>% 
  ggplot(aes(x = name, y = n, fill = name)) +  
  geom_col() + 
  geom_text(aes(label = n), vjust = -0.3) +
  theme_bw() +
  labs(fill = "Carbapenemasa")



ggsave(here("img", "summary_carba_plots.png"), carba_plot)

carba_coocur_labeled <- carba_labels <- data %>% 
  filter(!is.na(nhc)) %>% 
  select(sample, encode_fis) %>% 
  distinct() %>% 
  mutate(carba_label = decode_carba_label(encode_fis)) %>% 
  filter(encode_fis != "0000") %>% 
  group_by(carba_label) %>% 
  summarise(n=n()) %>% 
  arrange(desc(n))


multi_carba_labeled <- carba_coocur_labeled %>% 
  mutate(carba_label = fct_rev(fct_reorder(carba_label, n))) %>% 
  ggplot(aes(x = carba_label, y = n, fill = carba_label)) +  
  geom_col() + 
  geom_text(aes(label = n), vjust = -0.3) +
  theme_bw() +
  labs(fill = "Carbapenemasa", x = "Carbapenemasa") +
  theme(
    axis.text.x = element_text(angle = 45, hjust = 1),
    legend.position = "none"
  )

ggsave(here("img", "multi_carba_plot.png"), multi_carba_labeled, width = 10, height = 7)
openxlsx::write.xlsx(here("data", "carbas_labeled.xlsx"))



# Matrices de copresencia y análisis estadístico
carba_fis <- data %>% 
  filter(!is.na(nhc)) %>% 
  select(
    sample,
    OXA48_specific,
    VIM_specific,
    KPC_specific,
    NDM_specific
  ) %>% 
  distinct() %>% 
  filter(!is.na(OXA48_specific)) %>% 
  select(ends_with("_specific")) %>% 
  as.matrix()

# Calculamos la coocurrencias

# La multiplicación matricial de la transpuesta contra la original
# al ser clasificadores binario funciona como un AND acumulado
# (Cij = E_k (A_ik · B_kj))

coocur <- t(carba_fis) %*% carba_fis

# La dibujamos
levels_order <- c("OXA48", "VIM", "KPC", "NDM")

heatmap_coocur <- melt(coocur) %>%
  mutate(
    across(c(Var1, Var2), ~str_replace(.x, "_specific", "")),
    Var1 = factor(Var1, levels = levels_order),
    Var2 = factor(Var2, levels = rev(levels_order))
  ) %>%  
  ggplot(aes(Var1, Var2, fill = value)) +
  geom_tile() +
  geom_text(aes(label = value)) +
  scale_fill_gradient(low = "white", high = "steelblue") +
  labs(title = "Co-ocurrencia de carbapenemasas",x=NULL, y=NULL) + theme_classic() +
  theme(
    plot.title = element_text(hjust=0.5)
  )

ggsave(here("img", "heatmap_coocurr.png"), heatmap_coocur)


# Proporción: división de cada fila coocur por su diagonal
cooccur_prop <-round(sweep(coocur, 1, diag(coocur), "/"), 4)

# La proporción es por filas! Es decir, si la fila es OXA_fis
# y la columna es VIM_fis y el valor es 0.16 quiere decir que 
# el 16% de las OXAs tienen VIM
# Si la fila es VIM y la columna OXA y el valor 0.21, quiere decir
# que el 21% de las VIM tienen OXA.
cooccur_prop

# En el heatmap se ha invertido
heatmap_coocur_prop <- melt(cooccur_prop) %>%
  mutate(
    across(c(Var1, Var2), ~str_replace(.x, "_specific", "")),
    Var1 = factor(Var1, levels = levels_order),
    Var2 = factor(Var2, levels = rev(levels_order))
  ) %>%  
  ggplot(aes(Var1, Var2, fill = value)) +
  geom_tile() +
  geom_text(aes(label = value)) +
  scale_fill_gradient(low = "white", high = "steelblue") +
  labs(title = "Co-ocurrencia de carbapenemasas (proporciones)",x=NULL, y=NULL) + theme_classic() +
  theme(
    plot.title = element_text(hjust=0.5)
  )

heatmap_coocur_prop
ggsave(here("img", "heatmap_coocurr_prop.png"), heatmap_coocur_prop)



# Repetimos pero solo con un aislado por paciente
carba_fis_patient <- data %>% 
  filter(!is.na(nhc)) %>% 
  select(
    nhc,
    entry_date,
    sample,
    OXA48_specific,
    VIM_specific,
    KPC_specific,
    NDM_specific
  ) %>% 
  arrange(nhc, entry_date) %>% 
  group_by(nhc) %>% 
  slice(1) %>%                # ← primer resultado por paciente
  ungroup() %>% 
  distinct() %>% 
  filter(!is.na(OXA48_specific)) %>% 
  select(ends_with("_specific")) %>% 
  as.matrix()

coocur_pat <- t(carba_fis_patient) %*% carba_fis_patient

# La dibujamos
levels_order <- c("OXA48", "VIM", "KPC", "NDM")

heatmap_coocur_pat <- melt(coocur_pat) %>%
  mutate(
    across(c(Var1, Var2), ~str_replace(.x, "_specific", "")),
    Var1 = factor(Var1, levels = levels_order),
    Var2 = factor(Var2, levels = rev(levels_order))
  ) %>%  
  ggplot(aes(Var1, Var2, fill = value)) +
  geom_tile() +
  geom_text(aes(label = value)) +
  scale_fill_gradient(low = "white", high = "steelblue") +
  labs(title = "Co-ocurrencia de carbapenemasas",x=NULL, y=NULL) + theme_classic() +
  theme(
    plot.title = element_text(hjust=0.5)
  )

heatmap_coocur_pat


# Proporción: división de cada fila coocur por su diagonal
cooccur_prop_pat <-round(sweep(coocur_pat, 1, diag(coocur_pat), "/"), 4)

# La proporción es por filas! Es decir, si la fila es OXA_fis
# y la columna es VIM_fis y el valor es 0.16 quiere decir que 
# el 16% de las OXAs tienen VIM
# Si la fila es VIM y la columna OXA y el valor 0.21, quiere decir
# que el 21% de las VIM tienen OXA.
cooccur_prop_pat

# En el heatmap se ha invertido
heatmap_coocur_prop_patient <- melt(cooccur_prop_pat) %>%
  mutate(
    across(c(Var1, Var2), ~str_replace(.x, "_specific", "")),
    Var1 = factor(Var1, levels = levels_order),
    Var2 = factor(Var2, levels = rev(levels_order))
  ) %>%  
  ggplot(aes(Var1, Var2, fill = value)) +
  geom_tile() +
  geom_text(aes(label = value)) +
  scale_fill_gradient(low = "white", high = "steelblue") +
  labs(title = "Co-ocurrencia de carbapenemasas (solo 1 por paciente)",x=NULL, y=NULL) + theme_classic() +
  theme(
    plot.title = element_text(hjust=0.5)
  )

heatmap_coocur_prop_patient

ggsave(here("img", "coocur_prop_pat.png"), heatmap_coocur_prop_patient)


