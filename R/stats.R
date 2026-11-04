test_carba_by <- function(data, variable) {
  # TODO:
  # Replace simple chi-square tests with a model accounting
  # for repeated samples within patients.
  data |>
    filter(!is.na(.data[[variable]])) |>
    group_by(carbapenemase) |>
    group_modify(
      ~ {
        contingency <- table(
          .x[[variable]],
          .x$positive
        )
        
        test <- chisq.test(
          contingency,
          correct = FALSE
        )
        
        tibble(
          p_value = test$p.value
        )
      }
    ) |>
    ungroup() |>
    mutate(
      p_adjusted = p.adjust(
        p_value,
        method = "BH"
      )
    )
}

test_patient_carba_demographics <- function(
    data,
    valid_sex = unname(SEX_LEVELS)
) {
  
  data |>
    group_by(carbapenemase) |>
    group_modify(
      ~ {
        
        # Age
        age_data <- .x |>
          filter(
            !is.na(age),
            positive %in% c(0, 1)
          )
        
        age_p <- if (
          n_distinct(age_data$positive) == 2
        ) {
          wilcox.test(
            age ~ positive,
            data = age_data,
            exact = FALSE
          )$p.value
        } else {
          NA_real_
        }
        
        
        # Sex
        sex_data <- .x |>
          filter(
            sex %in% valid_sex,
            positive %in% c(0, 1)
          )
        
        sex_table <- table(
          sex_data$sex,
          sex_data$positive
        )
        
        sex_test <- suppressWarnings(
          chisq.test(
            sex_table,
            correct = FALSE
          )
        )
        
        if (any(sex_test$expected < 5)) {
          sex_p <- fisher.test(
            sex_table
          )$p.value
          
          sex_method <- "Fisher exact"
        } else {
          sex_p <- sex_test$p.value
          sex_method <- "Chi-square"
        }
        
        tibble(
          age_p = age_p,
          sex_p = sex_p,
          sex_test = sex_method
        )
      }
    ) |>
    ungroup()
}

test_patient_carba_by_population <- function(
    data,
    valid_sex = unname(SEX_LEVELS)
) {
  
  analysis_data <- data |>
    mutate(
      population = case_when(
        age < 18  ~ "Pediatric",
        age >= 18 ~ "Adult",
        TRUE      ~ NA_character_
      )
    ) |>
    filter(!is.na(population))
  
  analysis_data |>
    group_by(
      population,
      carbapenemase
    ) |>
    group_modify(
      ~ {
        
        # -------------------------
        # AGE: positive vs negative
        # -------------------------
        
        age_data <- .x |>
          filter(
            !is.na(age),
            positive %in% c(0, 1)
          )
        
        age_positive <- age_data |>
          filter(positive == 1) |>
          pull(age)
        
        age_negative <- age_data |>
          filter(positive == 0) |>
          pull(age)
        
        age_p <- if (
          length(age_positive) > 0 &&
          length(age_negative) > 0
        ) {
          wilcox.test(
            age_positive,
            age_negative,
            exact = FALSE
          )$p.value
        } else {
          NA_real_
        }
        
        
        # -------------------------
        # SEX: positive vs negative
        # -------------------------
        
        sex_data <- .x |>
          filter(
            sex %in% valid_sex,
            positive %in% c(0, 1)
          )
        
        sex_table <- table(
          sex_data$sex,
          sex_data$positive
        )
        
        if (
          nrow(sex_table) == 2 &&
          ncol(sex_table) == 2
        ) {
          
          chi <- suppressWarnings(
            chisq.test(
              sex_table,
              correct = FALSE
            )
          )
          
          if (any(chi$expected < 5)) {
            sex_p <- fisher.test(sex_table)$p.value
            sex_method <- "Fisher exact"
          } else {
            sex_p <- chi$p.value
            sex_method <- "Chi-square"
          }
          
        } else {
          
          sex_p <- NA_real_
          sex_method <- NA_character_
        }
        
        
        tibble(
          age_p = age_p,
          sex_p = sex_p,
          sex_test = sex_method
        )
      }
    ) |>
    ungroup()
}

test_adult_age_group_carba <- function(data) {
  
  adult_data <- data |>
    filter(age >= 18) |>
    mutate(
      adult_age_group = if_else(
        age < 65,
        "18–64 years",
        "≥65 years"
      )
    )
  
  adult_data |>
    group_by(carbapenemase) |>
    group_modify(
      ~ {
        
        tab <- table(
          .x$adult_age_group,
          .x$positive
        )
        
        chi <- suppressWarnings(
          chisq.test(
            tab,
            correct = FALSE
          )
        )
        
        if (any(chi$expected < 5)) {
          p <- fisher.test(tab)$p.value
          method <- "Fisher exact"
        } else {
          p <- chi$p.value
          method <- "Chi-square"
        }
        
        tibble(
          p_value = p,
          method = method
        )
      }
    ) |>
    ungroup()
}

adjust_patient_carba_population_tests <- function(tests) {
  
  tests |>
    group_by(population) |>
    group_modify(
      ~ {
        x <- .x
        
        specific <- as.character(x$carbapenemase) != ANY_GENE_LABEL
        
        x$age_p_adjusted <- x$age_p
        x$sex_p_adjusted <- x$sex_p
        
        x$age_p_adjusted[specific] <- p.adjust(
          x$age_p[specific],
          method = "BH"
        )
        
        x$sex_p_adjusted[specific] <- p.adjust(
          x$sex_p[specific],
          method = "BH"
        )
        
        x
      }
    ) |>
    ungroup()
}

analyse_carba_profile_independence <- function(
    profiles,
    genes = CARBA_PROFILE_GENES
) {
  
  profile_counts <- profiles |>
    count(
      profile,
      name = "observed"
    ) |>
    tidyr::complete(
      profile = all_binary_profiles(
        length(genes)
      ),
      fill = list(
        observed = 0L
      )
    )
  
  
  bits <- stringr::str_split_fixed(
    profile_counts$profile,
    "",
    length(genes)
  )
  
  bit_columns <- paste0(
    "bit_",
    seq_along(genes)
  )
  
  for (i in seq_along(genes)) {
    profile_counts[[bit_columns[[i]]]] <-
      as.integer(bits[, i])
  }
  
  
  # Modelo nulo:
  # presencia de los cuatro genes mutuamente independiente
  model_formula <- reformulate(
    bit_columns,
    response = "observed"
  )
  
  fit <- glm(
    model_formula,
    family = poisson(),
    data = profile_counts
  )
  
  
  profile_counts <- profile_counts |>
    mutate(
      expected = fitted(fit),
      
      oe_ratio = if_else(
        expected > 0,
        observed / expected,
        NA_real_
      ),
      
      standardized_residual =
        rstandard(
          fit,
          type = "pearson"
        ),
      
      p_value =
        2 * pnorm(
          -abs(standardized_residual)
        ),
      
      p_adjusted =
        p.adjust(
          p_value,
          method = "BH"
        ),
      
      deviation = case_when(
        p_adjusted < 0.05 &
          observed > expected ~ "Higher",
        
        p_adjusted < 0.05 &
          observed < expected ~ "Lower",
        
        TRUE ~ "As expected"
      ),
      
      n_genes = stringr::str_count(
        profile,
        "1"
      ),
      
      combination = profile_to_combination(
        profile,
        genes
      ),
      
      profile_order = strtoi(
        profile,
        base = 2
      )
    ) |>
    arrange(
      n_genes,
      desc(profile_order)
    )
  
  
  global_test <- tibble(
    statistic = deviance(fit),
    df = df.residual(fit),
    p_value = pchisq(
      deviance(fit),
      df = df.residual(fit),
      lower.tail = FALSE
    )
  )
  
  
  list(
    profiles = profile_counts,
    global_test = global_test,
    model = fit
  )
}

analyse_carba_pairwise_cooccurrence <- function(carba_matrix) {
  
  genes <- colnames(carba_matrix)
  
  pairs <- combn(
    genes,
    2,
    simplify = FALSE
  )
  
  results <- purrr::map_dfr(
    pairs,
    function(pair) {
      
      gene_a <- pair[[1]]
      gene_b <- pair[[2]]
      
      a <- carba_matrix[, gene_a]
      b <- carba_matrix[, gene_b]
      
      # Tabla 2x2:
      #
      #                  Gene B+
      #                  Gene B-
      #
      # Gene A+          both       A only
      # Gene A-          B only     neither
      
      both <- sum(
        a == 1 & b == 1
      )
      
      a_only <- sum(
        a == 1 & b == 0
      )
      
      b_only <- sum(
        a == 0 & b == 1
      )
      
      neither <- sum(
        a == 0 & b == 0
      )
      
      n <- length(a)
      
      n_a <- both + a_only
      n_b <- both + b_only
      
      
      # Bajo independencia:
      #
      # P(A ∩ B) = P(A) * P(B)
      #
      # Por tanto, el número esperado de muestras con ambos genes es:
      #
      # E(A,B) = N * P(A) * P(B)
      #        = n_A * n_B / N
      
      expected <- n_a * n_b / n
      
      
      # Observed / Expected:
      #
      # > 1  -> los genes aparecen juntos más de lo esperado
      # = 1  -> asociación compatible con independencia
      # < 1  -> aparecen juntos menos de lo esperado
      
      oe_ratio <- ifelse(
        expected > 0,
        both / expected,
        NA_real_
      )
      
      
      # Proporciones condicionales:
      #
      # P(B|A): proporción de positivos para A que también tienen B.
      # P(A|B): proporción de positivos para B que también tienen A.
      #
      # Estas dos cantidades no son necesariamente iguales.
      
      p_b_given_a <- ifelse(
        n_a > 0,
        both / n_a,
        NA_real_
      )
      
      p_a_given_b <- ifelse(
        n_b > 0,
        both / n_b,
        NA_real_
      )
      
      
      contingency <- matrix(
        c(
          both,
          a_only,
          b_only,
          neither
        ),
        nrow = 2,
        byrow = TRUE,
        dimnames = list(
          c(
            paste0(gene_a, "+"),
            paste0(gene_a, "-")
          ),
          c(
            paste0(gene_b, "+"),
            paste0(gene_b, "-")
          )
        )
      )
      
      
      fisher <- fisher.test(
        contingency
      )
      
      
      tibble::tibble(
        gene_a = gene_a,
        gene_b = gene_b,
        
        n = n,
        
        gene_a_positive = n_a,
        gene_b_positive = n_b,
        
        observed = both,
        expected = expected,
        oe_ratio = oe_ratio,
        
        p_b_given_a = p_b_given_a,
        p_a_given_b = p_a_given_b,
        
        odds_ratio = unname(
          fisher$estimate
        ),
        
        conf_low = fisher$conf.int[[1]],
        conf_high = fisher$conf.int[[2]],
        
        p_value = fisher$p.value
      )
    }
  )
  
  
  results |>
    mutate(
      p_adjusted = p.adjust(
        p_value,
        method = "BH"
      ),
      
      association = case_when(
        p_adjusted < 0.05 &
          odds_ratio > 1 ~ "Positive",
        
        p_adjusted < 0.05 &
          odds_ratio < 1 ~ "Negative",
        
        TRUE ~ "No significant association"
      )
    )
}

test_relative_load_by_lis <- function(data) {
  
  data |>
    filter(
      sil_positive %in% c(0, 1)
    ) |>
    group_by(
      carbapenemase
    ) |>
    group_modify(
      ~ {
        
        if (
          dplyr::n_distinct(
            .x$sil_positive
          ) < 2
        ) {
          return(
            tibble(
              n_lis_negative = sum(
                .x$sil_positive == 0
              ),
              n_lis_positive = sum(
                .x$sil_positive == 1
              ),
              median_lis_negative = NA_real_,
              median_lis_positive = NA_real_,
              statistic = NA_real_,
              p_value = NA_real_
            )
          )
        }
        
        
        test <- wilcox.test(
          relative_load ~ sil_positive,
          data = .x,
          exact = FALSE
        )
        
        
        tibble(
          n_lis_negative = sum(
            .x$sil_positive == 0
          ),
          
          n_lis_positive = sum(
            .x$sil_positive == 1
          ),
          
          median_lis_negative = median(
            .x$relative_load[
              .x$sil_positive == 0
            ],
            na.rm = TRUE
          ),
          
          median_lis_positive = median(
            .x$relative_load[
              .x$sil_positive == 1
            ],
            na.rm = TRUE
          ),
          
          statistic = unname(
            test$statistic
          ),
          
          p_value = test$p.value
        )
      }
    ) |>
    ungroup() |>
    mutate(
      median_difference =
        median_lis_positive -
        median_lis_negative,
      
      p_adjusted = p.adjust(
        p_value,
        method = "BH"
      )
    )
}

analyse_relative_load_time_trend <- function(
    data,
    origin_date = STUDY_START
) {
  
  analysis_data <- data |>
    filter(
      !is.na(entry_date),
      !is.na(relative_load)
    ) |>
    mutate(
      time_years =
        as.numeric(
          as.Date(entry_date) -
            as.Date(origin_date)
        ) / 365.25
    )
  
  
  analysis_data |>
    group_by(
      carbapenemase
    ) |>
    group_modify(
      ~ {
        
        fit <- lm(
          relative_load ~ time_years,
          data = .x
        )
        
        coef_table <-
          summary(fit)$coefficients
        
        ci <- confint(
          fit,
          "time_years",
          level = 0.95
        )
        
        
        tibble(
          n = nrow(.x),
          
          slope_per_year =
            coef_table[
              "time_years",
              "Estimate"
            ],
          
          slope_se =
            coef_table[
              "time_years",
              "Std. Error"
            ],
          
          conf_low =
            ci[1],
          
          conf_high =
            ci[2],
          
          p_value =
            coef_table[
              "time_years",
              "Pr(>|t|)"
            ],
          
          r_squared =
            summary(fit)$r.squared
        )
      }
    ) |>
    ungroup() |>
    mutate(
      p_adjusted = p.adjust(
        p_value,
        method = "BH"
      )
    )
}

test_relative_load_by_unit <- function(
    data,
    unit_col = "origin_code",
    min_n = 10
) {
  
  analysis_data <- data |>
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
    ungroup()
  
  
  analysis_data |>
    group_by(
      carbapenemase
    ) |>
    group_modify(
      ~ {
        
        unit <- .x[[unit_col]]
        
        if (
          dplyr::n_distinct(unit) < 2
        ) {
          return(
            tibble(
              n = nrow(.x),
              n_units =
                dplyr::n_distinct(unit),
              statistic = NA_real_,
              df = NA_real_,
              p_value = NA_real_
            )
          )
        }
        
        
        test <- kruskal.test(
          .x$relative_load,
          unit
        )
        
        
        tibble(
          n = nrow(.x),
          
          n_units =
            dplyr::n_distinct(unit),
          
          statistic =
            unname(test$statistic),
          
          df =
            unname(test$parameter),
          
          p_value =
            test$p.value
        )
      }
    ) |>
    ungroup() |>
    mutate(
      p_adjusted = p.adjust(
        p_value,
        method = "BH"
      )
    )
}

test_relative_load_unit_pairs <- function(
    data,
    unit_col = "origin_code",
    min_n = 10
) {
  
  analysis_data <- data |>
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
    ungroup()
  
  
  analysis_data |>
    group_by(
      carbapenemase
    ) |>
    group_modify(
      ~ {
        
        units <- .x[[unit_col]]
        
        if (
          dplyr::n_distinct(units) < 2
        ) {
          return(
            tibble(
              unit_a = character(),
              unit_b = character(),
              p_adjusted = numeric()
            )
          )
        }
        
        
        pw <- pairwise.wilcox.test(
          x = .x$relative_load,
          g = units,
          p.adjust.method = "BH",
          exact = FALSE
        )
        
        
        as.data.frame(
          as.table(
            pw$p.value
          )
        ) |>
          transmute(
            unit_a = as.character(Var1),
            unit_b = as.character(Var2),
            p_adjusted = Freq
          ) |>
          filter(
            !is.na(p_adjusted)
          )
      }
    ) |>
    ungroup()
}

test_relative_load_by_department <- function(
    data,
    department_col = "department_description",
    min_n = 10
) {
  
  test_relative_load_by_unit(
    data,
    unit_col = department_col,
    min_n = min_n
  ) |>
    rename(
      n_departments = n_units
    )
}


test_relative_load_department_pairs <- function(
    data,
    department_col = "department_description",
    min_n = 10
) {
  
  test_relative_load_unit_pairs(
    data,
    unit_col = department_col,
    min_n = min_n
  ) |>
    rename(
      department_a = unit_a,
      department_b = unit_b
    )
}