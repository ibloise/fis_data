summarise_study_population <- function(data, patients) {
  
  age_counts <- patients |>
    count(age_cat)
  
  list(
    n_samples = n_distinct(data$sample),
    n_patients = nrow(patients),
    
    n_adults = age_counts |>
      filter(age_cat == AgeCategories$ADULT) |>
      pull(n),
    
    n_pediatric = age_counts |>
      filter(age_cat == AgeCategories$PEDIATRIC) |>
      pull(n)
  )
}

summarise_units <- function(
    data,
    threshold = UNIT_SAMPLE_THRESHOLD
) {
  
  data |>
    distinct(sample, .keep_all = TRUE) |>
    count(
      origin_code,
      name = "n"
    ) |>
    mutate(
      group = if_else(
        n >= threshold,
        "above",
        "below"
      )
    )
}

summarise_unit_services <- function(data) {
  
  data |>
    distinct(sample, .keep_all = TRUE) |>
    count(
      origin_code,
      desc_proc,
      service_code,
      desc_service,
      name = "n"
    )
}

summarise_unit_groups <- function(units_summary) {
  
  units_summary |>
    group_by(group) |>
    summarise(
      n_units = n(),
      n_samples = sum(n),
      min_samples = min(n),
      max_samples = max(n),
      .groups = "drop"
    )
}

summarise_swabs_per_patient <- function(data) {
  
  data |>
    distinct(sample, .keep_all = TRUE) |>
    count(
      nhc,
      name = "swabs_per_patient"
    ) |>
    count(
      swabs_per_patient,
      name = "n_patients"
    ) |>
    mutate(
      percent = n_patients / sum(n_patients) * 100,
      cumulative_percent = cumsum(percent)
    ) |>
    arrange(swabs_per_patient)
}

summarise_swab_distribution <- function(
    swabs_per_patient,
    threshold = PATIENT_SWAB_THRESHOLD
) {
  
  list(
    single_swab_percent =
      swabs_per_patient |>
      filter(swabs_per_patient == 1) |>
      summarise(value = sum(percent)) |>
      pull(value),
    
    above_threshold_percent =
      swabs_per_patient |>
      filter(swabs_per_patient >= threshold) |>
      summarise(value = sum(percent)) |>
      pull(value)
  )
}

summarise_global_positivity <- function(
    positives_samples,
    study_summary
) {
  
  n_positive_samples <- positives_samples |>
    distinct(sample) |>
    nrow()
  
  n_positive_patients <- positives_samples |>
    distinct(nhc) |>
    nrow()
  
  list(
    n_samples = n_positive_samples,
    n_patients = n_positive_patients,
    proportion = n_positive_samples / study_summary$n_samples,
    percent = n_positive_samples / study_summary$n_samples * 100
  )
}

summarise_carbapenemases <- function(
    positives_samples,
    total_samples
) {
  
  positives_samples |>
    distinct(sample, .keep_all = TRUE) |>
    summarise(
      `OXA-48` = sum(oxa48_specific == 1, na.rm = TRUE),
      VIM      = sum(vim_specific == 1, na.rm = TRUE),
      KPC      = sum(kpc_specific == 1, na.rm = TRUE),
      NDM      = sum(ndm_specific == 1, na.rm = TRUE)
    ) |>
    pivot_longer(
      everything(),
      names_to = "carbapenemase",
      values_to = "n"
    ) |>
    mutate(
      percent = n / total_samples * 100
    )
}

summarise_monthly_carbapenemases <- function(
    data,
    positive_samples,
    carba_map = CARBAPENEMASE_MAP
) {
  
  # Denominador mensual: todas las muestras
  monthly_samples <- data |>
    distinct(sample, .keep_all = TRUE) |>
    mutate(
      month = lubridate::floor_date(entry_date, "month")
    ) |>
    count(
      month,
      name = "n_samples_month"
    ) |>
    arrange(month)
  
  
  # Carbapenemasas específicas
  specific_counts <- positive_samples |>
    distinct(sample, .keep_all = TRUE) |>
    mutate(
      month = lubridate::floor_date(entry_date, "month")
    ) |>
    select(
      month,
      sample,
      all_of(names(carba_map))
    ) |>
    pivot_longer(
      cols = all_of(names(carba_map)),
      names_to = "carbapenemase_column",
      values_to = "positive"
    ) |>
    mutate(
      carbapenemase = recode(
        carbapenemase_column,
        !!!carba_map
      )
    ) |>
    group_by(
      month,
      carbapenemase
    ) |>
    summarise(
      n_positive = sum(
        positive == 1,
        na.rm = TRUE
      ),
      .groups = "drop"
    )
  
  
  # Creamos explícitamente todas las combinaciones
  # mes × carbapenemasa.
  specific_monthly <- tidyr::crossing(
    month = monthly_samples$month,
    carbapenemase = unname(carba_map)
  ) |>
    left_join(
      specific_counts,
      by = c(
        "month",
        "carbapenemase"
      )
    ) |>
    mutate(
      n_positive = coalesce(
        n_positive,
        0L
      )
    ) |>
    left_join(
      monthly_samples,
      by = "month"
    ) |>
    mutate(
      percent = n_positive / n_samples_month * 100
    )
  
  
  # Positividad para cualquier gen
  any_gene_monthly <- positive_samples |>
    distinct(sample, .keep_all = TRUE) |>
    mutate(
      month = lubridate::floor_date(entry_date, "month")
    ) |>
    count(
      month,
      name = "n_positive"
    ) |>
    right_join(
      monthly_samples,
      by = "month"
    ) |>
    mutate(
      n_positive = coalesce(
        n_positive,
        0L
      ),
      carbapenemase = ANY_GENE_LABEL,
      percent = n_positive / n_samples_month * 100
    )
  
  
  # Dataset mensual final
  bind_rows(
    any_gene_monthly,
    specific_monthly
  ) |>
    mutate(
      carbapenemase = factor(
        carbapenemase,
        levels = CARBAPENEMASE_LEVELS
      )
    ) |>
    arrange(
      month,
      carbapenemase
    )
}

summarise_monthly_carba_stats <- function(
    monthly_data,
    include_any_gene = FALSE
) {
  
  data <- monthly_data
  
  if (!include_any_gene) {
    data <- data |>
      filter(
        as.character(carbapenemase) != ANY_GENE_LABEL
      )
  }
  
  data |>
    group_by(carbapenemase) |>
    summarise(
      months_detected = sum(n_positive > 0),
      
      median_monthly = median(n_positive),
      mean_monthly = mean(n_positive),
      min_monthly = min(n_positive),
      max_monthly = max(n_positive),
      
      median_percent = median(percent),
      min_percent = min(percent),
      max_percent = max(percent),
      
      .groups = "drop"
    )
}

summarise_carba_by <- function(data, variable) {
  
  data |>
    filter(!is.na(.data[[variable]])) |>
    group_by(
      level = .data[[variable]],
      carbapenemase
    ) |>
    summarise(
      n_total = n_distinct(sample),
      n_positive = sum(positive == 1),
      percent = n_positive / n_total * 100,
      .groups = "drop"
    )
}

summarise_patient_carba_demographics <- function(
    data,
    female = SEX_LEVELS[["FEMALE"]],
    male = SEX_LEVELS[["MALE"]]
) {
  
  data |>
    group_by(carbapenemase) |>
    summarise(
      n_patients = n_distinct(nhc),
      
      n_positive = sum(
        positive == 1,
        na.rm = TRUE
      ),
      
      percent_positive =
        n_positive / n_patients * 100,
      
      # Age: positive patients
      age_positive_median = median(
        age[positive == 1],
        na.rm = TRUE
      ),
      
      age_positive_q1 = quantile(
        age[positive == 1],
        probs = 0.25,
        na.rm = TRUE,
        names = FALSE
      ),
      
      age_positive_q3 = quantile(
        age[positive == 1],
        probs = 0.75,
        na.rm = TRUE,
        names = FALSE
      ),
      
      # Age: negative patients
      age_negative_median = median(
        age[positive == 0],
        na.rm = TRUE
      ),
      
      age_negative_q1 = quantile(
        age[positive == 0],
        probs = 0.25,
        na.rm = TRUE,
        names = FALSE
      ),
      
      age_negative_q3 = quantile(
        age[positive == 0],
        probs = 0.75,
        na.rm = TRUE,
        names = FALSE
      ),
      
      # Sex
      female_total = sum(
        sex == female,
        na.rm = TRUE
      ),
      
      female_positive = sum(
        sex == female & positive == 1,
        na.rm = TRUE
      ),
      
      male_total = sum(
        sex == male,
        na.rm = TRUE
      ),
      
      male_positive = sum(
        sex == male & positive == 1,
        na.rm = TRUE
      ),
      
      .groups = "drop"
    ) |>
    mutate(
      female_percent =
        female_positive / female_total * 100,
      
      male_percent =
        male_positive / male_total * 100
    )
}

summarise_patient_carba_by_population <- function(
    data,
    female = SEX_LEVELS[["FEMALE"]],
    male = SEX_LEVELS[["MALE"]]
) {

  data |>
    mutate(
      population = case_when(
        age < 18  ~ "Pediatric",
        age >= 18 ~ "Adult",
        TRUE      ~ NA_character_
      )
    ) |>
    filter(!is.na(population)) |>
    group_by(
      population,
      carbapenemase
    ) |>
    summarise(
      n_patients = n_distinct(nhc),

      n_positive = sum(
        positive == 1,
        na.rm = TRUE
      ),

      percent_positive =
        100 * n_positive / n_patients,

      # Positive age
      age_positive_median = median(
        age[positive == 1],
        na.rm = TRUE
      ),

      age_positive_q1 = quantile(
        age[positive == 1],
        0.25,
        na.rm = TRUE,
        names = FALSE
      ),

      age_positive_q3 = quantile(
        age[positive == 1],
        0.75,
        na.rm = TRUE,
        names = FALSE
      ),

      # Negative age
      age_negative_median = median(
        age[positive == 0],
        na.rm = TRUE
      ),

      age_negative_q1 = quantile(
        age[positive == 0],
        0.25,
        na.rm = TRUE,
        names = FALSE
      ),

      age_negative_q3 = quantile(
        age[positive == 0],
        0.75,
        na.rm = TRUE,
        names = FALSE
      ),

      # Female
      female_total = sum(
        sex == female,
        na.rm = TRUE
      ),

      female_positive = sum(
        sex == female &
        positive == 1,
        na.rm = TRUE
      ),

      # Male
      male_total = sum(
        sex == male,
        na.rm = TRUE
      ),

      male_positive = sum(
        sex == male &
        positive == 1,
        na.rm = TRUE
      ),

      .groups = "drop"
    ) |>
    mutate(
      female_percent =
        100 * female_positive / female_total,

      male_percent =
        100 * male_positive / male_total
    )
}


summarise_carba_cooccurrence <- function(
    profiles,
    independence
) {
  
  profile_stats <- independence$profiles
  
  n_positive <- profiles |>
    filter(n_genes >= 1) |>
    nrow()
  
  n_multiple <- profiles |>
    filter(n_genes >= 2) |>
    nrow()
  
  
  top_combinations <- profile_stats |>
    filter(n_genes >= 2) |>
    arrange(desc(observed)) |>
    slice_head(n = 3)
  
  
  single_profiles <- profile_stats |>
    filter(n_genes == 1)
  
  
  pair_profiles <- profile_stats |>
    filter(n_genes == 2)
  
  
  higher_pairs <- pair_profiles |>
    filter(deviation == "Higher")
  
  expected_pairs <- pair_profiles |>
    filter(deviation == "As expected")
  
  
  list(
    n_positive = n_positive,
    
    n_multiple = n_multiple,
    
    percent_multiple =
      100 * n_multiple / n_positive,
    
    single_oe_min =
      min(single_profiles$oe_ratio),
    
    single_oe_max =
      max(single_profiles$oe_ratio),
    
    top_combinations =
      top_combinations,
    
    higher_pairs =
      higher_pairs,
    
    expected_pairs =
      expected_pairs,
    
    global_p =
      independence$global_test$p_value[[1]]
  )
}

summarise_carba_pairwise_cooccurrence <- function(carba_matrix) {
  
  # Calculamos las coocurrencias.
  #
  # La multiplicación matricial de la transpuesta contra la original,
  # al ser clasificadores binarios, funciona como un AND acumulado:
  #
  # C_ij = sum_k(A_ki * A_kj)
  #
  # Por tanto:
  # - la diagonal contiene el número total de positivos para cada gen;
  # - las posiciones fuera de la diagonal contienen el número de muestras
  #   en las que ambos genes están presentes.
  #
  # IMPORTANTE: la coocurrencia OXA-48/VIM incluye también muestras
  # triples o cuádruples. No equivale al perfil exacto 1100.
  
  cooccur <- t(carba_matrix) %*% carba_matrix
  
  
  # Proporción: división de cada fila de cooccur por su diagonal.
  #
  # La proporción es por filas.
  # Es decir, si la fila es OXA-48 y la columna VIM y el valor es 0.16,
  # quiere decir que el 16% de las OXA-48 tienen también VIM.
  #
  # Si la fila es VIM y la columna OXA-48 y el valor es 0.21,
  # quiere decir que el 21% de las VIM tienen también OXA-48.
  #
  # Por este motivo la matriz de recuentos es simétrica, pero la matriz
  # de proporciones condicionales no tiene por qué serlo.
  
  denominators <- diag(cooccur)
  
  cooccur_prop <- sweep(
    cooccur,
    1,
    denominators,
    "/"
  )
  
  cooccur_prop[
    !is.finite(cooccur_prop)
  ] <- NA_real_
  
  
  list(
    counts = cooccur,
    proportions = cooccur_prop
  )
}

summarise_carba_pairwise_results <- function(pairwise_stats) {
  
  stats <- pairwise_stats |>
    mutate(
      pair = paste(
        gene_a,
        gene_b,
        sep = " + "
      )
    )
  
  list(
    n_pairs = nrow(stats),
    
    n_positive_associations = sum(
      stats$association == "Positive",
      na.rm = TRUE
    ),
    
    oe_min = min(
      stats$oe_ratio,
      na.rm = TRUE
    ),
    
    oe_max = max(
      stats$oe_ratio,
      na.rm = TRUE
    ),
    
    most_frequent = stats |>
      slice_max(
        observed,
        n = 1,
        with_ties = FALSE
      ),
    
    strongest_enrichment = stats |>
      slice_max(
        oe_ratio,
        n = 1,
        with_ties = FALSE
      )
  )
}

summarise_pcr_lis_performance <- function(data) {
  
  data |>
    filter(
      fis_positive %in% c(0, 1),
      sil_positive %in% c(0, 1)
    ) |>
    group_by(carbapenemase) |>
    summarise(
      n = n(),
      
      tp = sum(
        fis_positive == 1 &
          sil_positive == 1
      ),
      
      fn = sum(
        fis_positive == 0 &
          sil_positive == 1
      ),
      
      fp = sum(
        fis_positive == 1 &
          sil_positive == 0
      ),
      
      tn = sum(
        fis_positive == 0 &
          sil_positive == 0
      ),
      
      .groups = "drop"
    ) |>
    mutate(
      sensitivity = tp / (tp + fn),
      specificity = tn / (tn + fp),
      ppv = tp / (tp + fp),
      npv = tn / (tn + fn)
    )
}

build_confusion_mosaic_data <- function(
    data,
    subset_label = "All samples"
) {
  
  data |>
    count(
      carbapenemase,
      fis_positive,
      sil_positive,
      name = "n"
    ) |>
    mutate(
      FIS = factor(
        fis_positive,
        levels = c(0, 1),
        labels = c("Negative", "Positive")
      ),
      SIL = factor(
        sil_positive,
        levels = c(0, 1),
        labels = c("Negative", "Positive")
      ),
      concordance = if_else(
        fis_positive == sil_positive,
        "Concordant",
        "Discordant"
      ),
      concordance = factor(
        concordance,
        levels = c("Concordant", "Discordant")
      ),
      subset = subset_label
    ) |>
    select(
      carbapenemase,
      subset,
      FIS,
      SIL,
      concordance,
      n
    ) |>
    mutate(
      FIS = forcats::fct_rev(FIS)
    )
}

summarise_relative_load <- function(data) {
  
  data |>
    group_by(carbapenemase) |>
    summarise(
      n = n(),
      
      mean = mean(
        relative_load,
        na.rm = TRUE
      ),
      
      sd = sd(
        relative_load,
        na.rm = TRUE
      ),
      
      median = median(
        relative_load,
        na.rm = TRUE
      ),
      
      q1 = quantile(
        relative_load,
        0.25,
        na.rm = TRUE,
        names = FALSE
      ),
      
      q3 = quantile(
        relative_load,
        0.75,
        na.rm = TRUE,
        names = FALSE
      ),
      
      iqr = IQR(
        relative_load,
        na.rm = TRUE
      ),
      
      min = min(
        relative_load,
        na.rm = TRUE
      ),
      
      max = max(
        relative_load,
        na.rm = TRUE
      ),
      
      .groups = "drop"
    ) |>
    mutate(
      # Back-transformation of summaries on the log10 scale
      geometric_mean_percent =
        100 * 10^mean,
      
      median_percent =
        100 * 10^median
    )
}

summarise_carba_by_department <- function(
    data,
    threshold = CLINICAL_CATEGORY_SAMPLE_THRESHOLD,
    carba_map = CARBAPENEMASE_MAP
) {
  
  outcome_map <- carba_map
  
  sample_data <- data |>
    distinct(sample, .keep_all = TRUE) |>
    filter(
      !is.na(department_description),
      department_description != "",
      population_context %in% c("adult", "pediatric")
    ) |>
    mutate(
      population_context = recode(
        population_context,
        adult = "Adult",
        pediatric = "Pediatric"
      )
    )
  # N real de muestras, recalculado dentro de cada población
  department_sizes <- sample_data |>
    count(
      population_context,
      department_description,
      name = "n_samples"
    ) |>
    filter(n_samples > threshold)
  
  sample_data |>
    semi_join(
      department_sizes,
      by = c(
        "population_context",
        "department_description"
      )
    ) |>
    select(
      sample,
      population_context,
      department_description,
      all_of(names(carba_map))
    ) |>
    pivot_longer(
      cols = all_of(names(outcome_map)),
      names_to = "carbapenemase_column",
      values_to = "positive"
    ) |>
    mutate(
      positive = coalesce(
        as.integer(positive),
        0L
      ),
      carbapenemase = unname(
        outcome_map[carbapenemase_column]
      ),
      carbapenemase = factor(
        carbapenemase,
        levels = unname(carba_map)
      )
    ) |>
    group_by(
      population_context,
      department_description,
      carbapenemase
    ) |>
    summarise(
      n_samples = n_distinct(sample),
      n_positive = sum(positive == 1),
      percent = 100 * n_positive / n_samples,
      .groups = "drop"
    )
}

# =============================================================================
# Microorganisms
# =============================================================================

summarise_carba_organism_composition <- function(
    positive_organism_carba,
    min_recoveries = 1
) {
  
  required_cols <- c(
    "carbapenemase",
    "organism_key",
    "organism"
  )
  
  missing_cols <- setdiff(
    required_cols,
    names(positive_organism_carba)
  )
  
  if (length(missing_cols) > 0) {
    stop(
      "Missing required columns: ",
      paste(
        missing_cols,
        collapse = ", "
      )
    )
  }
  
  
  positive_organism_carba |>
    count(
      carbapenemase,
      organism_key,
      organism,
      name = "n_recoveries"
    ) |>
    group_by(
      carbapenemase
    ) |>
    mutate(
      n_all_recoveries =
        sum(
          n_recoveries
        ),
      
      percent =
        100 *
        n_recoveries /
        n_all_recoveries
    ) |>
    ungroup() |>
    filter(
      n_recoveries >=
        min_recoveries
    ) |>
    arrange(
      carbapenemase,
      desc(
        n_recoveries
      ),
      organism
    )
}

summarise_organism_carba_profiles <- function(
    organism_profiles,
    min_organism_recoveries = 1,
    profile_levels = CARBA_PROFILE_COMBINATION_LEVELS
) {
  
  required_cols <- c(
    "organism_key",
    "organism",
    "combination",
    "n_mechanisms"
  )
  
  missing_cols <- setdiff(
    required_cols,
    names(organism_profiles)
  )
  
  if (length(missing_cols) > 0) {
    stop(
      "Missing required columns: ",
      paste(
        missing_cols,
        collapse = ", "
      )
    )
  }
  
  
  # -------------------------------------------------------------------------
  # Keep only complete positive carbapenemase profiles
  #
  # Negative cultures / organisms without an attributed carbapenemase
  # are not part of this analysis.
  # -------------------------------------------------------------------------
  
  profile_data <- organism_profiles |>
    filter(
      !is.na(combination),
      !is.na(n_mechanisms),
      n_mechanisms > 0
    )
  
  
  # -------------------------------------------------------------------------
  # Validate profile vocabulary before converting to factor
  # -------------------------------------------------------------------------
  
  unexpected_profiles <- setdiff(
    unique(
      profile_data$combination
    ),
    profile_levels
  )
  
  if (length(unexpected_profiles) > 0) {
    stop(
      "Unexpected carbapenemase profiles: ",
      paste(
        unexpected_profiles,
        collapse = ", "
      )
    )
  }
  
  
  # -------------------------------------------------------------------------
  # Frequency of each profile within each organism
  # -------------------------------------------------------------------------
  
  profile_data |>
    count(
      organism_key,
      organism,
      combination,
      name = "n_recoveries"
    ) |>
    group_by(
      organism_key,
      organism
    ) |>
    mutate(
      n_organism_recoveries =
        sum(
          n_recoveries
        ),
      
      percent =
        100 *
        n_recoveries /
        n_organism_recoveries
    ) |>
    ungroup() |>
    filter(
      n_organism_recoveries >=
        min_organism_recoveries
    ) |>
    mutate(
      combination =
        factor(
          combination,
          levels =
            profile_levels
        )
    ) |>
    arrange(
      desc(
        n_organism_recoveries
      ),
      organism,
      combination
    )
}

summarise_pcr_target_concordance <- function(
    target_comparison
) {
  
  required_cols <- c(
    "carbapenemase",
    "pcr_positive",
    "comparison"
  )
  
  missing_cols <- setdiff(
    required_cols,
    names(target_comparison)
  )
  
  if (length(missing_cols) > 0) {
    stop(
      "Missing required columns: ",
      paste(
        missing_cols,
        collapse = ", "
      )
    )
  }
  
  
  comparison_levels <- c(
    "PCR+ / carrier recovered",
    "PCR+ / no carrier recovered",
    "PCR− / carrier recovered",
    "PCR− / no carrier recovered"
  )
  
  
  target_comparison |>
    filter(
      !is.na(
        pcr_positive
      )
    ) |>
    count(
      carbapenemase,
      comparison,
      name = "n_samples"
    ) |>
    group_by(
      carbapenemase
    ) |>
    mutate(
      n_all_samples =
        sum(
          n_samples
        ),
      
      percent =
        100 *
        n_samples /
        n_all_samples
    ) |>
    ungroup() |>
    mutate(
      comparison =
        factor(
          comparison,
          levels =
            comparison_levels
        ),
      
      carbapenemase =
        factor(
          as.character(
            carbapenemase
          ),
          levels =
            unname(
              CARBAPENEMASE_MAP
            )
        )
    ) |>
    arrange(
      carbapenemase,
      comparison
    )
}

summarise_pcr_profile_agreement <- function(
    profile_agreement
) {
  
  required_cols <- c(
    "profile_relation"
  )
  
  missing_cols <- setdiff(
    required_cols,
    names(profile_agreement)
  )
  
  if (length(missing_cols) > 0) {
    stop(
      "Missing required columns: ",
      paste(
        missing_cols,
        collapse = ", "
      )
    )
  }
  
  
  relation_levels <- c(
    "Exact profile match",
    "Additional PCR mechanism(s)",
    "Additional culture-attributed mechanism(s)",
    "Mixed profile mismatch"
  )
  
  
  profile_agreement |>
    filter(
      profile_relation !=
        "PCR profile missing"
    ) |>
    count(
      profile_relation,
      name = "n_samples"
    ) |>
    mutate(
      n_all_samples =
        sum(
          n_samples
        ),
      
      percent =
        100 *
        n_samples /
        n_all_samples,
      
      profile_relation =
        factor(
          profile_relation,
          levels =
            relation_levels
        )
    ) |>
    arrange(
      profile_relation
    )
}

summarise_mechanism_architecture <- function(
    sample_architecture
) {
  
  required_cols <- c(
    "sample",
    "mechanism_architecture"
  )
  
  missing_cols <- setdiff(
    required_cols,
    names(sample_architecture)
  )
  
  if (length(missing_cols) > 0) {
    stop(
      "Missing required columns: ",
      paste(
        missing_cols,
        collapse = ", "
      )
    )
  }
  
  
  architecture_levels <- c(
    "Single carrier / single mechanism",
    "Single carrier / multiple mechanisms",
    "Multiple carriers / single mechanism",
    "Multiple carriers / multiple mechanisms"
  )
  
  
  # -------------------------------------------------------------------------
  # Validate one record per sample
  # -------------------------------------------------------------------------
  
  duplicated_samples <- sample_architecture |>
    count(
      sample
    ) |>
    filter(
      n > 1
    )
  
  if (nrow(duplicated_samples) > 0) {
    stop(
      "Sample architecture contains duplicated samples."
    )
  }
  
  
  # -------------------------------------------------------------------------
  # Summarise architecture
  # -------------------------------------------------------------------------
  
  sample_architecture |>
    count(
      mechanism_architecture,
      name = "n_samples"
    ) |>
    mutate(
      mechanism_architecture =
        factor(
          mechanism_architecture,
          levels = architecture_levels
        )
    ) |>
    tidyr::complete(
      mechanism_architecture,
      fill = list(
        n_samples = 0L
      )
    ) |>
    mutate(
      n_all_samples =
        sum(n_samples),
      
      percent =
        if_else(
          n_all_samples > 0,
          100 * n_samples / n_all_samples,
          NA_real_
        )
    ) |>
    arrange(
      mechanism_architecture
    )
}

