
demographic_table <- function(patients) {

  patients_grouped <- bind_rows(
    patients %>%
      mutate(group = "Overall"),
    
    patients %>%
      mutate(group = as.character(age_cat))
  )
  
  # -------------------------------------------------------------------------
  # Sex: n (%) dentro de cada grupo
  # -------------------------------------------------------------------------
  
  sex_summary <- patients_grouped %>%
    count(group, sex, name = "n") %>%
    group_by(group) %>%
    mutate(
      pct = 100 * n / sum(n),
      value = sprintf(
        "%d (%.1f%%)",
        n,
        pct
      )
    ) %>%
    ungroup() %>%
    transmute(
      group,
      characteristic = paste0(as.character(sex), ", n (%)"),
      value
    )
  
  # -------------------------------------------------------------------------
  # Age
  # -------------------------------------------------------------------------
  
  age_summary <- patients_grouped %>%
    group_by(group) %>%
    summarise(
      age_median = median(age, na.rm = TRUE),
      age_iqr = IQR(age, na.rm = TRUE),
      age_mean = mean(age, na.rm = TRUE),
      age_sd = sd(age, na.rm = TRUE),
      .groups = "drop"
    ) %>%
    transmute(
      group,
      
      `Age, median (IQR), years` = sprintf(
        "%.1f (%.1f)",
        age_median,
        age_iqr
      ),
      
      `Age, mean (SD), years` = sprintf(
        "%.1f (%.1f)",
        age_mean,
        age_sd
      )
    ) %>%
    pivot_longer(
      cols = -group,
      names_to = "characteristic",
      values_to = "value"
    )
  # -------------------------------------------------------------------------
  # Final table
  # -------------------------------------------------------------------------
  
  demographics_table <- bind_rows(
    sex_summary,
    age_summary
  ) %>%
    mutate(
      group = factor(
        group,
        levels = c(
          "Overall",
          as.character(AgeCategories$PEDIATRIC),
          as.character(AgeCategories$ADULT)
        ),
        labels = c(
          "Overall",
          "Pediatric",
          "Adult"
        )
      )
    ) %>%
    pivot_wider(
      names_from = group,
      values_from = value
    ) %>%
    select(
      Characteristic = characteristic,
      Overall,
      Pediatric,
      Adult
    )
  
  demographics_table %>%
    gt() %>%
    cols_label(
      Characteristic = "",
      Overall = "Overall",
      Pediatric = "Pediatric",
      Adult = "Adult"
    ) %>%
    cols_align(
      align = "left",
      columns = Characteristic
    ) %>%
    cols_align(
      align = "center",
      columns = c(Overall, Pediatric, Adult)
    )
}

build_demographic_carba_table <- function(
    carba_by_sex,
    carba_by_age
) {
  
  bind_rows(
    carba_by_sex |>
      mutate(section = "Sex"),
    
    carba_by_age |>
      mutate(section = "Age group")
  ) |>
    mutate(
      value = sprintf(
        "%d/%d (%.1f%%)",
        n_positive,
        n_total,
        percent
      )
    ) |>
    select(
      section,
      level,
      carbapenemase,
      value
    ) |>
    pivot_wider(
      names_from = carbapenemase,
      values_from = value
    )
}

demographic_carba_gt <- function(data) {
  
  data |>
    gt::gt(
      groupname_col = "section",
      rowname_col = "level"
    ) |>
    gt::cols_align(
      align = "center",
      columns = gt::everything()
    ) |>
    gt::tab_source_note(
      source_note = paste(
        "Values are n/N (%), where N is the total number of samples",
        "within each demographic group."
      )
    )
}

build_patient_carba_demographic_table <- function(
    summary,
    tests
) {
  
  summary |>
    left_join(
      tests,
      by = "carbapenemase"
    ) |>
    mutate(
      positive_patients = sprintf(
        "%d/%d (%.1f%%)",
        n_positive,
        n_patients,
        percent_positive
      ),
      
      age_positive = sprintf(
        "%.0f [%.0f–%.0f]",
        age_positive_median,
        age_positive_q1,
        age_positive_q3
      ),
      
      age_negative = sprintf(
        "%.0f [%.0f–%.0f]",
        age_negative_median,
        age_negative_q1,
        age_negative_q3
      ),
      
      age_p = vapply(
        age_p,
        format_p_value,
        character(1)
      ),
      
      female = sprintf(
        "%d/%d (%.1f%%)",
        female_positive,
        female_total,
        female_percent
      ),
      
      male = sprintf(
        "%d/%d (%.1f%%)",
        male_positive,
        male_total,
        male_percent
      ),
      
      sex_p = vapply(
        sex_p,
        format_p_value,
        character(1)
      )
    ) |>
    select(
      carbapenemase,
      positive_patients,
      age_positive,
      age_negative,
      age_p,
      female,
      male,
      sex_p
    )
}

patient_carba_demographic_gt <- function(data) {
  
  data |>
    gt::gt() |>
    gt::cols_label(
      carbapenemase = "Gene",
      positive_patients = "Positive patients, n/N (%)",
      
      age_positive = "Positive",
      age_negative = "Negative",
      age_p = "p-value",
      
      female = "Female, n/N (%)",
      male = "Male, n/N (%)",
      sex_p = "p-value"
    ) |>
    gt::tab_spanner(
      label = "Age, median [Q1–Q3], years",
      columns = c(
        age_positive,
        age_negative,
        age_p
      )
    ) |>
    gt::tab_spanner(
      label = "Sex",
      columns = c(
        female,
        male,
        sex_p
      )
    ) |>
    gt::cols_align(
      align = "left",
      columns = carbapenemase
    ) |>
    gt::cols_align(
      align = "center",
      columns = -carbapenemase
    ) |>
    gt::tab_style(
      style = gt::cell_text(
        weight = "bold"
      ),
      locations = gt::cells_body(
        rows = carbapenemase == ANY_GENE_LABEL
      )
    ) |>
    gt::tab_source_note(
      source_note = paste(
        "Age comparisons use the Mann–Whitney rank-sum test.",
        "Sex comparisons use the chi-square test or Fisher's exact test",
        "when expected cell counts are <5."
      )
    )
}
build_patient_carba_population_table <- function(
    summary,
    tests
) {
  
  summary |>
    left_join(
      tests,
      by = c(
        "population",
        "carbapenemase"
      )
    ) |>
    mutate(
      population_label = sprintf(
        "%s — N = %s (Female = %s; Male = %s)",
        population,
        scales::comma(n_patients),
        scales::comma(female_total),
        scales::comma(male_total)
      ),
      
      positive_patients = sprintf(
        "%d (%.1f%%)",
        n_positive,
        percent_positive
      ),
      
      age_positive = sprintf(
        "%.0f [%.0f–%.0f]",
        age_positive_median,
        age_positive_q1,
        age_positive_q3
      ),
      
      age_negative = sprintf(
        "%.0f [%.0f–%.0f]",
        age_negative_median,
        age_negative_q1,
        age_negative_q3
      ),
      
      female = sprintf(
        "%d (%.1f%%)",
        female_positive,
        female_percent
      ),
      
      male = sprintf(
        "%d (%.1f%%)",
        male_positive,
        male_percent
      ),
      
      age_p = vapply(
        age_p_adjusted,
        format_p_value,
        character(1)
      ),
      
      sex_p = vapply(
        sex_p_adjusted,
        format_p_value,
        character(1)
      )
    ) |>
    select(
      population_label,
      carbapenemase,
      positive_patients,
      age_positive,
      age_negative,
      age_p,
      female,
      male,
      sex_p
    )
}
patient_carba_population_gt <- function(data) {
  
  data |>
    gt::gt(
      groupname_col = "population_label",
      rowname_col = "carbapenemase"
    ) |>
    gt::cols_label(
      positive_patients = "Positive patients, n (%)",
      
      age_positive = "Positive",
      age_negative = "Negative",
      age_p = "p-value",
      
      female = "Female, n (%)",
      male = "Male, n (%)",
      sex_p = "p-value"
    ) |>
    gt::tab_spanner(
      label = "Age, median [Q1–Q3], years",
      columns = c(
        age_positive,
        age_negative,
        age_p
      )
    ) |>
    gt::tab_spanner(
      label = "Sex",
      columns = c(
        female,
        male,
        sex_p
      )
    ) |>
    gt::cols_align(
      align = "center",
      columns = gt::everything()
    ) |>
    gt::tab_style(
      style = gt::cell_text(weight = "bold"),
      locations = gt::cells_body(
        rows = carbapenemase == ANY_GENE_LABEL
      )
    ) |>
    gt::tab_source_note(
      source_note = paste(
        "Values for positivity and sex are n (%).",
        "Age comparisons were performed using the Mann–Whitney rank-sum test.",
        "Sex comparisons used the chi-square test or Fisher's exact test when required.",
        "For individual carbapenemases, p-values were adjusted within each population",
        "using the Benjamini–Hochberg method; Any gene represents the global endpoint."
      )
    )
}

build_carba_profile_table <- function(
    independence
) {
  
  independence$profiles |>
    transmute(
      profile,
      combination,
      
      observed,
      
      expected = sprintf(
        "%.1f",
        expected
      ),
      
      oe_ratio = sprintf(
        "%.2f",
        oe_ratio
      ),
      
      deviation
    )
}

carba_profile_gt <- function(data) {
  
  data |>
    gt::gt() |>
    gt::cols_label(
      profile = "Profile",
      combination = "Carbapenemase profile",
      observed = "Observed, n",
      expected = "Expected, n",
      oe_ratio = "Observed / expected",
      deviation = "Deviation"
    ) |>
    gt::cols_align(
      align = "center",
      columns = gt::everything()
    )
}

pcr_lis_performance_gt <- function(data) {
  
  data |>
    select(
      carbapenemase,
      sensitivity,
      specificity,
      ppv,
      npv
    ) |>
    gt::gt() |>
    gt::cols_label(
      carbapenemase = "Carbapenemase",
      sensitivity = "Sensitivity",
      specificity = "Specificity",
      ppv = "PPV",
      npv = "NPV"
    ) |>
    gt::fmt_percent(
      columns = c(
        sensitivity,
        specificity,
        ppv,
        npv
      ),
      decimals = 1
    ) |>
    gt::cols_align(
      align = "left",
      columns = carbapenemase
    ) |>
    gt::cols_align(
      align = "center",
      columns = -carbapenemase
    )
}

build_relative_load_table <- function(summary) {
  
  summary |>
    select(
      carbapenemase,
      n,
      mean,
      sd,
      median,
      q1,
      q3,
      iqr,
      min,
      max
    )
}

relative_load_gt <- function(data) {
  
  data |>
    gt::gt(
      rowname_col = "carbapenemase"
    ) |>
    gt::cols_label(
      n = "Detection count (N)",
      mean = "Mean",
      sd = "SD",
      median = "Median",
      q1 = "Q1",
      q3 = "Q3",
      iqr = "IQR",
      min = "Min",
      max = "Max"
    ) |>
    gt::fmt_integer(
      columns = n
    ) |>
    gt::fmt_number(
      columns = c(
        mean,
        sd,
        median,
        q1,
        q3,
        iqr,
        min,
        max
      ),
      decimals = 2
    ) |>
    gt::cols_align(
      align = "center",
      columns = gt::everything()
    ) |>
    gt::tab_source_note(
      source_note =
        "Relative load is expressed as log10(2^−ΔCt), using 16S rRNA as the reference target."
    )
}

# =============================================================================
# Microorganisms
# =============================================================================

table_carba_organism_composition <- function(
    data,
    digits = 1
) {
  
  required_cols <- c(
    "carbapenemase",
    "organism_key",
    "organism",
    "n_recoveries",
    "percent"
  )
  
  missing_cols <- setdiff(
    required_cols,
    names(data)
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
  # Order organisms by overall number of attributed recoveries
  # -------------------------------------------------------------------------
  
  organism_order <- data |>
    group_by(
      organism_key,
      organism
    ) |>
    summarise(
      n_total =
        sum(
          n_recoveries
        ),
      .groups = "drop"
    ) |>
    arrange(
      desc(
        n_total
      ),
      organism
    )
  
  
  # -------------------------------------------------------------------------
  # Format each carbapenemase-specific cell as n (%)
  # -------------------------------------------------------------------------
  
  table_data <- data |>
    mutate(
      carbapenemase =
        factor(
          as.character(
            carbapenemase
          ),
          levels =
            unname(
              CARBAPENEMASE_MAP
            )
        ),
      
      value =
        paste0(
          n_recoveries,
          " (",
          formatC(
            percent,
            format = "f",
            digits = digits
          ),
          "%)"
        )
    ) |>
    select(
      organism_key,
      organism,
      carbapenemase,
      value
    ) |>
    tidyr::complete(
      organism_key,
      organism,
      carbapenemase =
        factor(
          unname(
            CARBAPENEMASE_MAP
          ),
          levels =
            unname(
              CARBAPENEMASE_MAP
            )
        ),
      fill =
        list(
          value = "0 (0.0%)"
        )
    ) |>
    pivot_wider(
      names_from =
        carbapenemase,
      values_from =
        value
    )
  
  
  # -------------------------------------------------------------------------
  # Restore frequency-based organism order
  # -------------------------------------------------------------------------
  
  organism_order |>
    select(
      organism_key,
      organism,
      n_total
    ) |>
    left_join(
      table_data,
      by = c(
        "organism_key",
        "organism"
      )
    ) |>
    arrange(
      desc(
        n_total
      ),
      organism
    ) |>
    select(
      -organism_key,
      -n_total
    )
}

table_organism_carba_profiles <- function(
    data,
    digits = 1,
    profile_levels = CARBA_PROFILE_COMBINATION_LEVELS
) {
  
  required_cols <- c(
    "organism_key",
    "organism",
    "combination",
    "n_recoveries",
    "n_organism_recoveries",
    "percent"
  )
  
  missing_cols <- setdiff(
    required_cols,
    names(data)
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
  # Validate profile vocabulary
  # -------------------------------------------------------------------------
  
  unexpected_profiles <- setdiff(
    as.character(
      unique(
        data$combination
      )
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
  # Complete missing organism × profile combinations
  # -------------------------------------------------------------------------
  
  table_data <- data |>
    mutate(
      combination =
        factor(
          as.character(
            combination
          ),
          levels =
            profile_levels
        )
    ) |>
    tidyr::complete(
      tidyr::nesting(
        organism_key,
        organism,
        n_organism_recoveries
      ),
      combination =
        factor(
          profile_levels,
          levels =
            profile_levels
        ),
      fill =
        list(
          n_recoveries = 0L,
          percent = 0
        )
    ) |>
    mutate(
      value =
        paste0(
          n_recoveries,
          " (",
          formatC(
            percent,
            format = "f",
            digits = digits
          ),
          "%)"
        )
    )
  
  
  # -------------------------------------------------------------------------
  # Wide table with canonical profile order
  # -------------------------------------------------------------------------
  
  table_data |>
    select(
      organism_key,
      organism,
      n_organism_recoveries,
      combination,
      value
    ) |>
    pivot_wider(
      names_from =
        combination,
      values_from =
        value,
      names_expand = TRUE
    ) |>
    arrange(
      desc(
        n_organism_recoveries
      ),
      organism
    ) |>
    select(
      -organism_key,
      organism,
      n_organism_recoveries,
      all_of(
        profile_levels
      )
    )
}

table_pcr_target_concordance <- function(
    data,
    digits = 1
) {
  
  required_cols <- c(
    "carbapenemase",
    "comparison",
    "n_samples",
    "percent"
  )
  
  missing_cols <- setdiff(
    required_cols,
    names(data)
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
  
  
  # -------------------------------------------------------------------------
  # Validate comparison vocabulary
  # -------------------------------------------------------------------------
  
  unexpected_comparisons <- setdiff(
    as.character(
      unique(
        data$comparison
      )
    ),
    comparison_levels
  )
  
  if (length(unexpected_comparisons) > 0) {
    stop(
      "Unexpected comparison categories: ",
      paste(
        unexpected_comparisons,
        collapse = ", "
      )
    )
  }
  
  
  # -------------------------------------------------------------------------
  # Format cells as n (%)
  # -------------------------------------------------------------------------
  
  data |>
    mutate(
      carbapenemase =
        factor(
          as.character(
            carbapenemase
          ),
          levels =
            unname(
              CARBAPENEMASE_MAP
            )
        ),
      
      comparison =
        factor(
          as.character(
            comparison
          ),
          levels =
            comparison_levels
        ),
      
      value =
        paste0(
          n_samples,
          " (",
          formatC(
            percent,
            format = "f",
            digits = digits
          ),
          "%)"
        )
    ) |>
    tidyr::complete(
      carbapenemase =
        factor(
          unname(
            CARBAPENEMASE_MAP
          ),
          levels =
            unname(
              CARBAPENEMASE_MAP
            )
        ),
      
      comparison =
        factor(
          comparison_levels,
          levels =
            comparison_levels
        ),
      
      fill =
        list(
          n_samples = 0L,
          percent = 0
        )
    ) |>
    mutate(
      value =
        if_else(
          is.na(value),
          paste0(
            "0 (",
            formatC(
              0,
              format = "f",
              digits = digits
            ),
            "%)"
          ),
          value
        )
    ) |>
    select(
      carbapenemase,
      comparison,
      value
    ) |>
    pivot_wider(
      names_from =
        comparison,
      values_from =
        value,
      names_expand = TRUE
    ) |>
    arrange(
      carbapenemase
    )
}

table_pcr_profile_agreement <- function(
    data,
    digits = 1
) {
  
  required_cols <- c(
    "profile_relation",
    "n_samples",
    "percent"
  )
  
  missing_cols <- setdiff(
    required_cols,
    names(data)
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
  
  
  # -------------------------------------------------------------------------
  # Validate profile-relation vocabulary
  # -------------------------------------------------------------------------
  
  unexpected_relations <- setdiff(
    as.character(
      unique(
        data$profile_relation
      )
    ),
    relation_levels
  )
  
  if (length(unexpected_relations) > 0) {
    stop(
      "Unexpected profile-relation categories: ",
      paste(
        unexpected_relations,
        collapse = ", "
      )
    )
  }
  
  
  # -------------------------------------------------------------------------
  # Complete categories and format n (%)
  # -------------------------------------------------------------------------
  
  data |>
    mutate(
      profile_relation =
        factor(
          as.character(
            profile_relation
          ),
          levels =
            relation_levels
        )
    ) |>
    tidyr::complete(
      profile_relation =
        factor(
          relation_levels,
          levels =
            relation_levels
        ),
      fill =
        list(
          n_samples = 0L,
          percent = 0
        )
    ) |>
    mutate(
      value =
        paste0(
          n_samples,
          " (",
          formatC(
            percent,
            format = "f",
            digits = digits
          ),
          "%)"
        )
    ) |>
    select(
      profile_relation,
      n_samples,
      percent,
      value
    ) |>
    arrange(
      profile_relation
    )
}


table_mechanism_architecture <- function(
    data,
    digits = 1
) {
  
  required_cols <- c(
    "mechanism_architecture",
    "n_samples",
    "percent"
  )
  
  missing_cols <- setdiff(
    required_cols,
    names(data)
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
  # Validate architecture vocabulary
  # -------------------------------------------------------------------------
  
  unexpected_architectures <- setdiff(
    as.character(
      unique(
        data$mechanism_architecture
      )
    ),
    architecture_levels
  )
  
  if (length(unexpected_architectures) > 0) {
    stop(
      "Unexpected mechanism-architecture categories: ",
      paste(
        unexpected_architectures,
        collapse = ", "
      )
    )
  }
  
  
  # -------------------------------------------------------------------------
  # Complete categories and format n (%)
  # -------------------------------------------------------------------------
  
  data |>
    mutate(
      mechanism_architecture =
        factor(
          as.character(
            mechanism_architecture
          ),
          levels =
            architecture_levels
        )
    ) |>
    tidyr::complete(
      mechanism_architecture =
        factor(
          architecture_levels,
          levels =
            architecture_levels
        ),
      fill =
        list(
          n_samples = 0L,
          percent = 0
        )
    ) |>
    mutate(
      value =
        paste0(
          n_samples,
          " (",
          formatC(
            percent,
            format = "f",
            digits = digits
          ),
          "%)"
        )
    ) |>
    select(
      mechanism_architecture,
      n_samples,
      percent,
      value
    ) |>
    arrange(
      mechanism_architecture
    )
}