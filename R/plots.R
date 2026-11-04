plot_age_distribution <- function(
    patients,
    binwidth = 2,
    age_boundary = 18
) {
  
  patients |>
    ggplot(
      aes(
        x = age,
        fill = age_cat
      )
    ) +
    geom_histogram(
      binwidth = binwidth,
      boundary = age_boundary,
      closed = "left",
      position = "stack",
      color = "white"
    ) +
    scale_x_continuous(
      breaks = seq(0, 100, by = 10)
    ) +
    labs(
      x = "Age (years)",
      y = "Number of patients",
      fill = "Age category"
    ) +
    theme_classic()
}

prepare_swabs_per_patient_plot_data <- function(
    swabs_per_patient,
    cutoff = SWABS_PER_PATIENT_PLOT_CUTOFF
) {
  
  swabs_per_patient |>
    mutate(
      swabs_group = if_else(
        swabs_per_patient > cutoff,
        paste0(">", cutoff),
        as.character(swabs_per_patient)
      )
    ) |>
    group_by(swabs_group) |>
    summarise(
      n_patients = sum(n_patients),
      .groups = "drop"
    ) |>
    mutate(
      swabs_group = factor(
        swabs_group,
        levels = c(
          as.character(1:cutoff),
          paste0(">", cutoff)
        )
      )
    ) |>
    arrange(swabs_group) |>
    mutate(
      percent = n_patients / sum(n_patients) * 100,
      cumulative_percent = cumsum(percent)
    )
}

plot_swabs_per_patient <- function(
    swabs_per_patient,
    cutoff = SWABS_PER_PATIENT_PLOT_CUTOFF
) {
  
  swabs_plot <- prepare_swabs_per_patient_plot_data(
    swabs_per_patient,
    cutoff = cutoff
  )
  
  ggplot(
    swabs_plot,
    aes(x = swabs_group)
  ) +
    geom_col(
      aes(y = percent),
      width = 0.8
    ) +
    geom_line(
      aes(
        y = cumulative_percent,
        group = 1
      ),
      linewidth = 0.8
    ) +
    geom_point(
      aes(y = cumulative_percent),
      size = 1.8
    ) +
    scale_y_continuous(
      limits = c(0, 100),
      breaks = seq(0, 100, by = 10),
      labels = \(x) paste0(x, "%"),
      expand = expansion(mult = c(0, 0.02))
    ) +
    labs(
      x = "Number of swabs per patient",
      y = "Patients (%)"
    ) +
    theme_classic()
}


plot_monthly_carbapenemases <- function(
    monthly_carba,
    colors = CARBA_COLORS
) {
  
  ggplot(
    monthly_carba,
    aes(
      x = month,
      y = percent,
      color = carbapenemase,
      group = carbapenemase
    )
  ) +
    geom_line(
      data = \(x) dplyr::filter(
        x,
        carbapenemase == ANY_GENE_LABEL
      ),
      linewidth = 1
    ) +
    geom_line(
      data = \(x) dplyr::filter(
        x,
        carbapenemase != ANY_GENE_LABEL
      ),
      linewidth = 0.8,
      linetype = "dashed"
    ) +
    geom_point(
      size = 1.6
    ) +
    scale_x_date(
      date_breaks = "3 months",
      date_labels = "%b\n%Y"
    ) +
    scale_color_manual(
      values = colors
    ) +
    scale_y_continuous(
      labels = \(x) paste0(x, "%")
    ) +
    labs(
      x = NULL,
      y = "Positive samples (%)",
      color = NULL
    ) +
    theme_classic() +
    theme(
      legend.position = "top"
    )
}

plot_carba_by_age_group <- function(
    carba_by_age,
    include_any_gene = FALSE
) {
  
  plot_data <- carba_by_age |>
    dplyr::mutate(
      carbapenemase = as.character(carbapenemase),
      level = factor(
        level,
        levels = c("<18 years", "18–64 years", "≥65 years")
      )
    )
  
  if (!include_any_gene) {
    plot_data <- plot_data |>
      dplyr::filter(carbapenemase != ANY_GENE_LABEL)
  }
  
  plot_data |>
    dplyr::mutate(
      carbapenemase = factor(
        carbapenemase,
        levels = c("OXA-48", "VIM", "KPC", "NDM")
      )
    ) |>
    ggplot2::ggplot(
      ggplot2::aes(
        x = carbapenemase,
        y = percent,
        fill = level
      )
    ) +
    ggplot2::geom_col(
      position = ggplot2::position_dodge(width = 0.8),
      width = 0.75
    ) +
    ggplot2::scale_y_continuous(
      expand = ggplot2::expansion(mult = c(0, 0.05)),
      labels = \(x) paste0(x, "%")
    ) +
    scale_fill_manual(
      values = c(
        "<18 years" = "#3777C8",
        "18–64 years" = "#EB6A32",
        "≥65 years" = "#28B07D"
      ),
      labels = c(
        "<18 years" = "<18 years",
        "18–64 years" = "18–64 years",
        "≥65 years" = "65+ years"
      )
    ) +
    ggplot2::labs(
      x = NULL,
      y = "Proportion of positive samples",
      fill = NULL
    ) +
    ggplot2::theme_classic()
}

plot_carba_pairwise_conditional <- function(
    pairwise_matrices,
    digits = 1
) {
  
  cooccur_prop <- pairwise_matrices$proportions
  
  # La proporción es por filas.
  #
  # Es decir, si la fila es OXA-48 y la columna VIM y el valor es 0.16,
  # quiere decir que el 16% de las OXA-48 tienen también VIM.
  #
  # Si la fila es VIM y la columna OXA-48 y el valor es 0.21,
  # quiere decir que el 21% de las VIM tienen también OXA-48.
  #
  # Por tanto:
  #
  # fila    = gen de referencia / condicionado
  # columna = gen cuya copresencia estamos midiendo
  #
  # P(columna positiva | fila positiva)
  #
  # La matriz es asimétrica de forma intencionada.
  
  genes <- colnames(cooccur_prop)
  
  plot_data <- as.data.frame(
    as.table(cooccur_prop)
  ) |>
    dplyr::rename(
      reference_gene = Var1,
      cooccurring_gene = Var2,
      proportion = Freq
    ) |>
    dplyr::mutate(
      reference_gene = factor(
        reference_gene,
        levels = rev(genes)
      ),
      cooccurring_gene = factor(
        cooccurring_gene,
        levels = genes
      ),
      
      # La diagonal es trivialmente 100%:
      # P(A | A) = 1.
      # La ocultamos para centrar la figura en la copresencia entre
      # carbapenemasas distintas.
      diagonal =
        as.character(reference_gene) ==
        as.character(cooccurring_gene),
      
      label = dplyr::if_else(
        diagonal,
        "",
        sprintf(
          paste0("%.", digits, "f%%"),
          proportion * 100
        )
      ),
      
      fill_value = dplyr::if_else(
        diagonal,
        NA_real_,
        proportion
      )
    )
  
  
  ggplot2::ggplot(
    plot_data,
    ggplot2::aes(
      x = cooccurring_gene,
      y = reference_gene,
      fill = fill_value
    )
  ) +
    ggplot2::geom_tile(
      color = "white",
      linewidth = 0.8
    ) +
    ggplot2::geom_text(
      ggplot2::aes(label = label),
      size = 3.8
    ) +
    ggplot2::scale_fill_gradient(
      low = "white",
      high = "steelblue",
      labels = scales::label_percent(
        accuracy = 1
      ),
      na.value = "grey95"
    ) +
    ggplot2::labs(
      x = "Co-occurring carbapenemase",
      y = "Reference carbapenemase",
      fill = "Co-occurrence"
    ) +
    ggplot2::coord_fixed() +
    ggplot2::theme_classic() +
    ggplot2::theme(
      axis.text.x = ggplot2::element_text(
        angle = 45,
        hjust = 1
      ),
      axis.title = ggplot2::element_text(
        face = "bold"
      ),
      legend.position = "right"
    )
}

plot_confusion_mosaic <- function(data) {
  
  ggplot(
    data,
    aes(
      x = SIL,
      y = FIS,
      fill = concordance
    )
  ) +
    geom_tile(
      color = "black",
      linewidth = 0.8
    ) +
    geom_text(
      aes(label = scales::comma(n)),
      size = 5,
      fontface = "bold"
    ) +
    facet_grid(
      subset ~ carbapenemase
    ) +
    scale_fill_manual(
      values = c(
        "Concordant" = "#C8E6C9",
        "Discordant" = "#F5B7B1"
      )
    ) +
    labs(
      title = "Confusion matrices by carbapenemase",
      subtitle = "PCR compared with culture/LIS results",
      x = "Culture/LIS",
      y = "PCR",
      fill = NULL
    ) +
    theme_minimal(base_size = 13) +
    theme(
      panel.grid = element_blank(),
      strip.text = element_text(face = "bold"),
      axis.text = element_text(face = "bold")
    )
}

plot_relative_load_by_lis <- function(
    data,
    tests = NULL
) {
  
  p <- data |>
    filter(
      !is.na(lis_result)
    ) |>
    ggplot(
      aes(
        x = lis_result,
        y = relative_load,
        fill = lis_result
      )
    ) +
    geom_violin(
      trim = FALSE,
      alpha = 0.75
    ) +
    geom_boxplot(
      width = 0.18,
      fill = "white",
      outlier.shape = NA
    ) +
    facet_wrap(
      ~ carbapenemase
    ) +
    scale_fill_manual(
      values = c(
        "Negative" = "#00AFBB",
        "Positive" = "#FC4E07"
      )
    ) +
    labs(
      x = "Culture/LIS result",
      y = expression(
        log[10](2^{-Delta * C[t]})
      ),
      fill = "Culture/LIS result"
    ) +
    theme_classic(
      base_size = 13
    ) +
    theme(
      strip.text =
        element_text(
          face = "bold"
        ),
      
      legend.position = "none"
    )
  
  
  if (!is.null(tests)) {
    
    annotations <- data |>
      group_by(
        carbapenemase
      ) |>
      summarise(
        y = max(
          relative_load,
          na.rm = TRUE
        ),
        range = diff(
          range(
            relative_load,
            na.rm = TRUE
          )
        ),
        .groups = "drop"
      ) |>
      left_join(
        tests,
        by = "carbapenemase"
      ) |>
      mutate(
        y = y + 0.08 * range,
        
        label = paste0(
          "adjusted p = ",
          vapply(
            p_adjusted,
            format_p_value,
            character(1)
          )
        )
      )
    
    
    p <- p +
      geom_text(
        data = annotations,
        aes(
          x = 1.5,
          y = y,
          label = label
        ),
        inherit.aes = FALSE,
        size = 3.5
      )
  }
  
  
  p
}

plot_relative_load_over_time <- function(
    data
) {
  
  data |>
    filter(
      !is.na(entry_date),
      !is.na(relative_load)
    ) |>
    ggplot(
      aes(
        x = entry_date,
        y = relative_load
      )
    ) +
    geom_point(
      alpha = 0.20,
      size = 0.8
    ) +
    geom_smooth(
      method = "lm",
      formula = y ~ x,
      se = TRUE
    ) +
    facet_wrap(
      ~ carbapenemase
    ) +
    labs(
      x = "Sample collection date",
      y = expression(
        log[10](2^{-Delta * C[t]})
      )
    ) +
    theme_classic(
      base_size = 13
    ) +
    theme(
      strip.text =
        element_text(
          face = "bold"
        )
    )
}
plot_relative_load_by_unit <- function(
    data,
    unit_col = "origin_code",
    min_n = 10
) {
  
  plot_data <- data |>
    filter(
      !is.na(.data[[unit_col]])
    ) |>
    group_by(
      carbapenemase,
      .data[[unit_col]]
    ) |>
    filter(
      n() >= min_n
    ) |>
    ungroup() |>
    mutate(
      unit = .data[[unit_col]]
    )
  
  
  ggplot(
    plot_data,
    aes(
      x = forcats::fct_reorder(
        factor(unit),
        relative_load,
        median
      ),
      y = relative_load
    )
  ) +
    geom_boxplot(
      outlier.alpha = 0.25
    ) +
    facet_wrap(
      ~ carbapenemase,
      scales = "free_y"
    ) +
    coord_flip() +
    labs(
      x = "Hospital unit",
      y = expression(
        log[10](2^{-Delta * C[t]})
      )
    ) +
    theme_classic(
      base_size = 12
    )
}

plot_carba_by_department <- function(data) {
  
  plot_data <- data |>
    mutate(
      population_context = factor(
        population_context,
        levels = c("Adult", "Pediatric")
      ),
      department_label = sprintf(
        "%s (N=%d)",
        department_description,
        n_samples
      ),
      label = sprintf(
        "%.1f%%\n(n=%d)",
        percent,
        n_positive
      )
    ) |>
    group_by(carbapenemase) |>
    mutate(
      fill_value = scales::rescale(
        percent,
        to = c(0, 1)
      )
    ) |>
    ungroup()
  
  department_order <- plot_data |>
    distinct(
      department_label,
      n_samples
    ) |>
    arrange(n_samples) |>
    pull(department_label)
  
  plot_data <- plot_data |>
    mutate(
      department_label = factor(
        department_label,
        levels = unique(department_order)
      )
    )
  
  ggplot(
    plot_data,
    aes(
      x = carbapenemase,
      y = department_label,
      fill = fill_value
    )
  ) +
    geom_tile(
      color = "white",
      linewidth = 0.6
    ) +
    geom_text(
      aes(label = label),
      size = 4.2,
      lineheight = 0.95
    ) +
    scale_fill_gradientn(
      colors = c(
        "#fff7bc",
        "#fec44f",
        "#fe9929",
        "#f03b20",
        "#99000d"
      ),
      limits = c(0, 1),
      guide = "none"
    ) +
    facet_grid(
      population_context ~ .,
      scales = "free_y",
      space = "free_y"
    ) +
    labs(
      x = NULL,
      y = NULL
    ) +
    theme_classic(
      base_size = 14
    ) +
    theme(
      strip.background = element_blank(),
      
      strip.text.y = element_text(
        face = "bold",
        size = 14
      ),
      
      axis.text.x = element_text(
        face = "bold",
        size = 14
      ),
      
      axis.text.y = element_text(
        size = 12
      )
    )
}

plot_relative_load_by_department <- function(
    data,
    department_col = "department_description",
    min_n = 10
) {
  
  plot_relative_load_by_unit(
    data,
    unit_col = department_col,
    min_n = min_n
  ) +
    labs(
      x = "Clinical department"
    )
}

# =============================================================================
# Microorganisms
# =============================================================================

plot_carba_organism_composition <- function(
    data,
    value = c(
      "percent",
      "n_recoveries"
    ),
    ncol = 2
) {
  
  value <- match.arg(
    value
  )
  
  
  required_cols <- c(
    "carbapenemase",
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
  # Independent organism order within each carbapenemase
  #
  # A composite internal factor is required because the same organism
  # can occur in more than one facet.
  # -------------------------------------------------------------------------
  
  plot_data <- data |>
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
      
      organism_facet =
        paste(
          organism,
          carbapenemase,
          sep = "___"
        )
    )
  
  
  organism_levels <- plot_data |>
    arrange(
      carbapenemase,
      .data[[value]],
      organism
    ) |>
    pull(
      organism_facet
    ) |>
    unique()
  
  
  plot_data <- plot_data |>
    mutate(
      organism_facet =
        factor(
          organism_facet,
          levels =
            organism_levels
        )
    )
  
  
  # -------------------------------------------------------------------------
  # Plot
  # -------------------------------------------------------------------------
  
  p <- ggplot(
    plot_data,
    aes(
      x =
        .data[[value]],
      y =
        organism_facet
    )
  ) +
    geom_col() +
    facet_wrap(
      ~ carbapenemase,
      ncol =
        ncol,
      scales =
        "free"
    ) +
    scale_y_discrete(
      labels =
        function(x) {
          sub(
            "___.*$",
            "",
            x
          )
        }
    ) +
    labs(
      x =
        if (
          value ==
          "percent"
        ) {
          "Attributed recoveries (%)"
        } else {
          "Attributed recoveries"
        },
      
      y = NULL
    ) +
    theme_classic()
  
  
  if (
    value ==
    "percent"
  ) {
    
    p <- p +
      scale_x_continuous(
        labels =
          \(x)
        paste0(
          x,
          "%"
        )
      )
  }
  
  
  p
}

plot_organism_carba_profiles <- function(
    data,
    value = c(
      "n_recoveries",
      "percent"
    ),
    ncol = 4,
    profile_levels = CARBA_PROFILE_COMBINATION_LEVELS
) {
  
  value <- match.arg(
    value
  )
  
  
  required_cols <- c(
    "organism",
    "combination",
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
  # Maximum four facet columns
  # -------------------------------------------------------------------------
  
  ncol <- min(
    as.integer(ncol),
    4L
  )
  
  if (ncol < 1) {
    stop(
      "ncol must be >= 1."
    )
  }
  
  
  # -------------------------------------------------------------------------
  # Order organisms by total number of carbapenemase-positive recoveries
  # -------------------------------------------------------------------------
  
  organism_levels <- data |>
    group_by(
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
    ) |>
    pull(
      organism
    )
  
  
  # -------------------------------------------------------------------------
  # Fixed canonical profile order
  #
  # 1 mechanism:
  #   OXA-48, VIM, KPC, NDM
  #
  # 2 mechanisms:
  #   OXA-48 + VIM
  #   OXA-48 + KPC
  #   OXA-48 + NDM
  #   VIM + KPC
  #   VIM + NDM
  #   KPC + NDM
  #
  # then triple combinations and the four-mechanism profile.
  # -------------------------------------------------------------------------
  
  plot_data <- data |>
    mutate(
      organism =
        factor(
          organism,
          levels =
            organism_levels
        ),
      
      combination =
        factor(
          as.character(
            combination
          ),
          levels =
            profile_levels
        )
    )
  
  
  # -------------------------------------------------------------------------
  # Plot
  # -------------------------------------------------------------------------
  
  p <- ggplot(
    plot_data,
    aes(
      x =
        combination,
      y =
        .data[[value]]
    )
  ) +
    geom_col() +
    facet_wrap(
      ~ organism,
      ncol = ncol,
      scales = "fixed"
    ) +
    scale_x_discrete(
      drop = FALSE,
      labels =
        function(x) {
          stringr::str_replace_all(
            x,
            " \\+ ",
            "+"
          )
        }
    ) +
    labs(
      x =
        "Carbapenemase profile",
      
      y =
        if (
          value ==
          "percent"
        ) {
          "Recoveries (%)"
        } else {
          "Recoveries"
        }
    ) +
    theme_classic() +
    theme(
      axis.text.x =
        element_text(
          angle = 90,
          hjust = 1,
          vjust = 0.5
        )
    )
  
  
  if (
    value ==
    "percent"
  ) {
    
    p <- p +
      scale_y_continuous(
        labels =
          \(x)
        paste0(
          x,
          "%"
        )
      )
  }
  
  
  p
}

plot_pcr_target_concordance <- function(
    data
) {
  
  required_cols <- c(
    "carbapenemase",
    "comparison",
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
  
  
  plot_data <- data |>
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
        )
    )
  
  
  ggplot(
    plot_data,
    aes(
      x =
        carbapenemase,
      y =
        percent,
      fill =
        comparison
    )
  ) +
    geom_col(
      width = 0.75
    ) +
    scale_y_continuous(
      limits = c(
        0,
        100
      ),
      breaks =
        seq(
          0,
          100,
          by = 10
        ),
      labels =
        \(x)
      paste0(
        x,
        "%"
      ),
      expand =
        expansion(
          mult = c(
            0,
            0
          )
        )
    ) +
    labs(
      x = NULL,
      y = "Samples (%)",
      fill = NULL
    ) +
    theme_classic() +
    theme(
      legend.position = "top"
    )
}

plot_pcr_profile_agreement <- function(
    data
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
  
  
  plot_data <- data |>
    mutate(
      profile_relation =
        factor(
          as.character(
            profile_relation
          ),
          levels =
            relation_levels
        )
    )
  
  
  ggplot(
    plot_data,
    aes(
      x =
        profile_relation,
      y =
        percent
    )
  ) +
    geom_col(
      width = 0.7
    ) +
    geom_text(
      aes(
        label =
          paste0(
            n_samples,
            " (",
            round(
              percent,
              1
            ),
            "%)"
          )
      ),
      vjust = -0.4
    ) +
    scale_y_continuous(
      labels =
        \(x)
      paste0(
        x,
        "%"
      ),
      expand =
        expansion(
          mult = c(
            0,
            0.1
          )
        )
    ) +
    labs(
      x = NULL,
      y = "Samples (%)"
    ) +
    theme_classic() +
    theme(
      axis.text.x =
        element_text(
          angle = 25,
          hjust = 1
        )
    )
}