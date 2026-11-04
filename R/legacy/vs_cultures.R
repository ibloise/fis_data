library(tidyverse)
library(here)
library(caret)
library(ggpubr)
library(openxlsx)
library(stringi)
library(rlang)

source(here("R", "readers.R"))
source(here("R", "utils.R"))

analyze_target_discrepancies <- function(
    target,
    data_compare,
    data_by_patient = NULL,
    data_without_issues = NULL,
    discrepant_type = c("fis_pos_sil_neg", "fis_neg_sil_pos", "any")
) {
  
  discrepant_type <- match.arg(discrepant_type)
  
  fis_col <- paste0(target, "_fis")
  sil_col <- paste0(target, "_SIL")
  
  # --- Validación ---
  required_cols <- c(fis_col, sil_col, "sample")
  missing_cols <- setdiff(required_cols, names(data_compare))
  if (length(missing_cols) > 0) {
    stop("Faltan columnas en data_compare: ", paste(missing_cols, collapse = ", "))
  }
  
  # --- Matriz principal ---
  cm_main <- confusionMatrix(
    data_compare[[fis_col]],
    data_compare[[sil_col]],
    dnn = c("FIS", "SIL"),
    positive = "Pos"
  )
  
  # --- Matriz por paciente ---
  cm_patient <- NULL
  if (!is.null(data_by_patient)) {
    cm_patient <- confusionMatrix(
      data_by_patient[[fis_col]],
      data_by_patient[[sil_col]],
      dnn = c("FIS", "SIL"),
      positive = "Pos"
    )
  }
  
  # --- Matriz sin incidencias ---
  cm_noissues <- NULL
  if (!is.null(data_without_issues)) {
    cm_noissues <- confusionMatrix(
      data_without_issues[[fis_col]],
      data_without_issues[[sil_col]],
      dnn = c("FIS", "SIL"),
      positive = "Pos"
    )
  }
  
  # --- Clasificación de discrepancias ---
  discrepant_df <- data_compare %>%
    mutate(
      discrepancy = case_when(
        .data[[fis_col]] == "Pos" & .data[[sil_col]] == "Neg" ~ "fis_pos_sil_neg",
        .data[[fis_col]] == "Neg" & .data[[sil_col]] == "Pos" ~ "fis_neg_sil_pos",
        TRUE ~ "concordant"
      )
    )
  
  discrepant_rows <- switch(
    discrepant_type,
    fis_pos_sil_neg = discrepant_df %>% filter(discrepancy == "fis_pos_sil_neg"),
    fis_neg_sil_pos = discrepant_df %>% filter(discrepancy == "fis_neg_sil_pos"),
    any = discrepant_df %>% filter(discrepancy != "concordant")
  )
  
  discrepant_samples <- discrepant_rows %>%
    pull(sample) %>%
    unique()
  
  return(list(
    target = target,
    fis_col = fis_col,
    sil_col = sil_col,
    cm_main = cm_main,
    cm_patient = cm_patient,
    cm_noissues = cm_noissues,
    discrepant_rows = discrepant_rows,
    discrepant_samples = discrepant_samples
  ))
}
  
  return(list(
    target = target,
    fis_col = fis_col,
    sil_col = sil_col,
    cm_main = cm_main,
    cm_patient = cm_patient,
    cm_noissues = cm_noissues,
    discrepant_rows = discrepant_rows,
    discrepant_samples = discrepant_samples,
    exported_data = exported_data
  ))
}

data <- read_clean_data()

# Unificamos los global encode. Esto había que hacerlo en los pasos de pre limpieza

# Tambien hay que asignar una categoría global a OXA, FIS, NDM y KPC
data <- data %>% 
  filter(
    entry_date >= as.Date("2023-06-01"), # Habría que aplicar este corte a la otra también
  ) %>% 
  filter(!is.na(nhc)) %>% 
  mutate(global_encoded = if_else(is.na(global_encoded), "0000", global_encoded),
         OXA48_fis = coalesce(OXA48_specific, 0),
         VIM_fis = coalesce(VIM_specific, 0),
         KPC_fis = coalesce(KPC_specific, 0),
         NDM_fis = coalesce(NDM_specific, 0))


data %>% 
  select(sample, global_encoded) %>% 
  distinct() %>% 
  group_by(sample) %>% 
  filter(n()>1)


unified_codes <- data %>%
  filter(!is.na(global_encoded)) %>%
  group_by(sample) %>%
  summarise(
    unified_code = {
      codes <- as.character(global_encoded)
      
      m <- do.call(rbind, lapply(codes, function(x) as.integer(strsplit(x, "")[[1]])))
      paste0(apply(m, 2, max), collapse = "")
    },
    .groups = "drop"
  )

data_clean <-data %>% 
  left_join(unified_codes, by="sample") %>% 
  mutate(global_encoded = unified_code) %>% 
  select(-unified_code)

data_clean %>% 
  select(sample, global_encoded) %>% 
  distinct() %>% 
  group_by(sample) %>% 
  filter(n()>1)

# Comenzamos la comparativa:
# Esta función hay que unificarla

data_only_samples <- data_clean %>%
  filter(!is.na(nhc)) %>% 
  select(-microorg_code, -microorg_name, -encoded_carba_org, -OXA48, -VIM, -KPC, -NDM, 
         -result_sample, - result_entry_date, - result_itself) %>% 
  distinct() %>% 
  mutate(decode_carba(global_encoded, suffix = "_SIL"))

data_to_compare <- data_only_samples %>% 
  mutate(
    across(
      matches("(_fis|_sil)$"),
      ~ factor(.x, levels = c(0, 1), labels = c("Neg", "Pos"))
    )
  )

data_to_compare %>% 
  group_by(sample) %>% 
  filter(n()>1)

by_patient <- data_to_compare %>%
  arrange(nhc, entry_date) %>%
  group_by(nhc) %>%
  slice(1) %>%
  ungroup()

boxplot_data <- data_to_compare %>% 
  pivot_longer(
    cols = c(
      OXA48_SIL, VIM_SIL, KPC_SIL, NDM_SIL,
      OXA48_log_delta, VIM_log_delta, KPC_log_delta, NDM_log_delta
    ),
    names_to = c("mecanismo", ".value"),
    names_pattern = "^(OXA48|VIM|KPC|NDM)_(SIL|log_delta)$"
  ) %>% 
  filter(!is.na(log_delta))


boxplot_data %>% 
  ggplot(aes(x = SIL, y = log_delta)) +
  geom_boxplot() +
  facet_wrap(~mecanismo, scales = "free_y") +
  stat_compare_means(
    method = "wilcox.test",
    label = "p.format"
  )

my_comparisons <- list(c("Neg", "Pos"))

# Todos los casos:
boxvioling_plot <- boxplot_data %>% 
  filter(!is.na(SIL), !is.na(log_delta)) %>% 
  mutate(
    SIL = factor(SIL, levels = c("Neg", "Pos"))
  ) %>% 
  ggviolin(
    x = "SIL",
    y = "log_delta",
    fill = "SIL",
    palette = c("#00AFBB", "#FC4E07"),
    add = "boxplot",
    add.params = list(fill = "white", width = 0.2)
  ) +
  facet_wrap(~ mecanismo) +
  stat_compare_means(
    comparisons = my_comparisons,
    method = "wilcox.test",
    label = "p.signif"
  ) +
  stat_compare_means(
    method = "wilcox.test",
    label = "p.format",
    label.y = 2.5
  ) +
  labs(
    x = "Resultado SIL",
    y = "Log ΔCt",
    fill = "SIL",
    title = "Log ΔCt vs cultivo - Datos totales"
  ) 

ggsave(here("img", "boxviolin_alldata.png"), boxvioling_plot)
# Solo un caso por paciente
boxplot_data %>% 
  arrange(nhc, entry_date) %>%
  group_by(nhc) %>%
  slice(1) %>%
  ungroup() %>% 
  filter(!is.na(SIL), !is.na(log_delta)) %>% 
  mutate(
    SIL = factor(SIL, levels = c("Neg", "Pos"))
  ) %>% 
  ggviolin(
    x = "SIL",
    y = "log_delta",
    fill = "SIL",
    palette = c("#00AFBB", "#FC4E07"),
    add = "boxplot",
    add.params = list(fill = "white", width = 0.2)
  ) +
  facet_wrap(~ mecanismo) +
  stat_compare_means(
    comparisons = my_comparisons,
    method = "wilcox.test",
    label = "p.signif"
  ) +
  stat_compare_means(
    method = "wilcox.test",
    label = "p.format",
    label.y = 2.5
  ) +
  labs(
    x = "Resultado SIL",
    y = "Log Δ",
    fill = "SIL"
  ) 

# Vamos a retirar los resultados positivos con 0000 para hacer la comparativa a ver que tal

data_without_issues <- data_to_compare %>% 
  filter(!(result_interpretate == "Positive Culture" & global_encoded == "0000"))

boxplot_data_nonis <- data_without_issues %>% 
  pivot_longer(
    cols = c(
      OXA48_SIL, VIM_SIL, KPC_SIL, NDM_SIL,
      OXA48_log_delta, VIM_log_delta, KPC_log_delta, NDM_log_delta
    ),
    names_to = c("mecanismo", ".value"),
    names_pattern = "^(OXA48|VIM|KPC|NDM)_(SIL|log_delta)$"
  ) %>% 
  filter(!is.na(log_delta))

boxplot_violin_nonis <- boxplot_data_nonis %>% 
  filter(!is.na(SIL), !is.na(log_delta)) %>% 
  mutate(
    SIL = factor(SIL, levels = c("Neg", "Pos"))
  ) %>% 
  ggviolin(
    x = "SIL",
    y = "log_delta",
    fill = "SIL",
    palette = c("#00AFBB", "#FC4E07"),
    add = "boxplot",
    add.params = list(fill = "white", width = 0.2)
  ) +
  facet_wrap(~ mecanismo) +
  stat_compare_means(
    comparisons = my_comparisons,
    method = "wilcox.test",
    label = "p.signif"
  ) +
  stat_compare_means(
    method = "wilcox.test",
    label = "p.format",
    label.y = 2.5
  ) +
  labs(
    x = "Resultado SIL",
    y = "Log Δ",
    fill = "SIL"
  ) 

ggsave(here("img", "boxplot_violin_filtered_nonis.png"), boxplot_violin_nonis)
# NOTA IMPORTANTE: Vamos a hacer estas comparativas habiendo retirado los casos dudosos
# OXA:

res_oxa <- analyze_target_discrepancies(
  target = "OXA48",
  data_compare = data_to_compare,
  data_by_patient = by_patient,
  data_without_issues = data_without_issues,
  discrepant_type = "fis_pos_sil_neg"
)

res_oxa$cm_noissues$table


res_VIM <- analyze_target_discrepancies(
  target = "VIM",
  data_compare = data_to_compare,
  data_by_patient = by_patient,
  data_without_issues = data_without_issues,
  discrepant_type = "fis_pos_sil_neg"
)
res_NDM <- analyze_target_discrepancies(
  target = "NDM",
  data_compare = data_to_compare,
  data_by_patient = by_patient,
  data_without_issues = data_without_issues,
  discrepant_type = "fis_pos_sil_neg"
)
res_KPC <- analyze_target_discrepancies(
  target = "KPC",
  data_compare = data_to_compare,
  data_by_patient = by_patient,
  data_without_issues = data_without_issues,
  discrepant_type = "fis_pos_sil_neg"
)

View(res_KPC$discrepant_rows)


cm_to_df <- function(cm, mecanismo, subset_name) {
  as.data.frame(cm$table) %>%
    rename(FIS = 1, SIL = 2, n = 3) %>%
    mutate(
      mecanismo = mecanismo,
      subset = subset_name
    )
}

# Unimos todas
cm_df <- bind_rows(
  cm_to_df(cm_oxa, "OXA48", "Todos"),
  cm_to_df(cm_oxa_nonis, "OXA48", "Filtrada"),
  cm_to_df(cm_vim, "VIM", "Todos"),
  cm_to_df(cm_vim_nonis, "VIM", "Filtrada"),
  cm_to_df(cm_kpc, "KPC", "Todos"),
  cm_to_df(cm_kpc_nonis, "KPC", "Filtrada"),
  cm_to_df(cm_ndm, "NDM", "Todos"),
  cm_to_df(cm_ndm_nonis, "NDM", "Filtrada")
) %>%
  mutate(
    mecanismo = factor(mecanismo, levels = c("OXA48","VIM", "KPC", "NDM")),
    subset = factor(subset, levels = c("Todos", "Filtrada")),
    FIS = fct_rev(FIS)   # para que Neg quede abajo y Pos arriba
  )


cm_df_flag <- cm_df %>%
  mutate(
    concordancia = ifelse(FIS == SIL, "Concordante", "Discrepante"),
    concordancia = factor(concordancia, levels = c("Concordante", "Discrepante"))
  )

mosaic_multiplot <- ggplot(cm_df_flag, aes(x = SIL, y = FIS, fill = concordancia)) +
  geom_tile(color = "black", linewidth = 0.8) +
  geom_text(aes(label = n), size = 5, fontface = "bold") +
  facet_grid(subset ~ mecanismo) +
  scale_fill_manual(
    values = c(
      "Concordante" = "#C8E6C9",   # verde pastel
      "Discrepante" = "#F5B7B1"    # rojo suave
    )
  ) +
  labs(
    title = "Matrices de confusión por mecanismo",
    subtitle = "Concordancias vs discrepancias",
    x = "SIL",
    y = "FIS",
    fill = ""
  ) +
  theme_minimal(base_size = 13) +
  theme(
    panel.grid = element_blank(),
    strip.text = element_text(face = "bold"),
    axis.text = element_text(face = "bold")
  )

ggsave(here("img", "mosaic_multiplot.png"), mosaic_multiplot)


# Análisis de especies
microorg_carba <- data_clean %>% 
  filter(result_interpretate == "Positive Culture") %>% 
  mutate(microorg_name = if_else(microorg_name == "Klebsiella pneumoniae ssp pneumoniae", "Klebsiella pneumoniae", microorg_name)) %>% 
  mutate(microorg_name = if_else(microorg_name == "Proteus mirabilis blee", "Proteus mirabilis", microorg_name)) %>% 
  select(sample, microorg_name, OXA48, VIM, KPC, NDM) %>% 
  pivot_longer(cols = c(OXA48, VIM, KPC, NDM)) %>% 
  group_by(microorg_name, name) %>% 
  summarise(n = sum(value)) %>% 
  arrange(desc(n))

microorg_carba %>% 
ggplot(aes(x = name, y = microorg_name, fill = n)) +
  geom_tile(color = "white") +
  geom_text(aes(label = ifelse(n == 0, "", n)), size = 3) +
  scale_fill_gradient(low = "white", high = "#fcae91") +
  theme_classic() +
  labs(
    x = "Carbapenemasa",
    y = "Microorganismo",
    fill = "n"
  )  

microorg_dist <- microorg_carba %>% 
group_by(microorg_name) %>% 
  filter(sum(n, na.rm = TRUE) > 0) %>% 
  ungroup() %>% 
  mutate(
    microorg_name = forcats::fct_reorder(microorg_name, n, .fun = sum),
    name = factor(name, levels = c("OXA48", "VIM", "KPC", "NDM", "IMP")),
    label = ifelse(n == 0, NA, n)
  ) %>% 
  ggplot(aes(x = name, y = microorg_name, fill = n)) +
  geom_tile(color = "grey90") +
  geom_text(aes(label = label), size = 3, na.rm = TRUE) +
  scale_fill_gradient(low = "white", high = "#fcae91") +
  theme_classic() +
  theme(
    axis.text.x = element_text(angle = 45, hjust = 1)
  ) +
  labs(
    x = "Carbapenemasa",
    y = "Microorganismo",
    fill = "Frecuencia"
  )


ggsave(here("img", "microorg_dist.png"), microorg_dist, width = 12, height = 6)

