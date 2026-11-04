get_value <- function(
    df,
    key_value,
    key_col = "group",
    value_col = "n"
) {
  
  value <- df |>
    filter(.data[[key_col]] == key_value) |>
    pull(.data[[value_col]])
  
  if (length(value) != 1) {
    stop(
      "Expected exactly one value for ",
      key_col, " = ", key_value,
      "; found ", length(value)
    )
  }
  
  value[[1]]
}
inline_stat <- function(
    df,
    key,
    column = "n",
    key_column = "carbapenemase",
    decimals = NULL,
    suffix = ""
) {
  value <- df |>
    filter(.data[[key_column]] == key) |>
    pull(.data[[column]])
  
  if (length(value) != 1) {
    stop("Expected exactly one value for key: ", key)
  }
  
  if (!is.null(decimals)) {
    value <- sprintf(paste0("%.", decimals, "f"), value)
  }
  
  paste0(value, suffix)
}

inline_n <- function(df, key, key_column = "carbapenemase") {
  inline_stat(
    df,
    key,
    column = "n",
    key_column = key_column
  )
}

inline_pct <- function(
    df,
    key,
    key_column = "carbapenemase",
    decimals = 1
) {
  inline_stat(
    df,
    key,
    column = "percent",
    key_column = key_column,
    decimals = decimals,
    suffix = "%"
  )
}


demographic_stat <- function(
    df,
    group,
    carbapenemase = "Any gene",
    stat = "percent",
    digits = 1
) {
  
  value <- df |>
    filter(
      as.character(level) == group,
      as.character(carbapenemase) == !!carbapenemase
    ) |>
    pull(.data[[stat]])
  
  if (length(value) != 1) {
    stop(
      "Expected exactly one value for ",
      group,
      " / ",
      carbapenemase
    )
  }
  
  if (stat == "percent") {
    return(sprintf(
      paste0("%.", digits, "f"),
      value
    ))
  }
  
  value
}

format_p_value <- function(
    p,
    digits = 4,
    threshold = 0.0001
) {
  
  if (is.na(p)) {
    return("NA")
  }
  
  if (p < threshold) {
    return(
      paste0(
        "<",
        format(
          threshold,
          scientific = FALSE
        )
      )
    )
  }
  
  sprintf(
    paste0("%.", digits, "f"),
    p
  )
}

monthly_stat <- function(
    summary,
    carba,
    stat
) {
  get_value(
    summary,
    carba,
    key_col = "carbapenemase",
    value_col = stat
  )
}

patient_carba_age <- function(
    summary,
    population,
    carba = ANY_GENE_LABEL,
    status = c("positive", "negative")
) {
  
  status <- match.arg(status)
  
  row <- summary |>
    filter(
      population == !!population,
      as.character(carbapenemase) == carba
    )
  
  if (nrow(row) != 1) {
    stop(
      "Expected exactly one row for ",
      population, " / ", carba
    )
  }
  
  sprintf(
    "%.0f [%.0f–%.0f]",
    row[[paste0("age_", status, "_median")]],
    row[[paste0("age_", status, "_q1")]],
    row[[paste0("age_", status, "_q3")]]
  )
}


patient_carba_p <- function(
    tests,
    population,
    carba = ANY_GENE_LABEL,
    test = c("age", "sex")
) {
  
  test <- match.arg(test)
  
  row <- tests |>
    filter(
      population == !!population,
      as.character(carbapenemase) == carba
    )
  
  if (nrow(row) != 1) {
    stop(
      "Expected exactly one row for ",
      population, " / ", carba
    )
  }
  
  p_col <- paste0(test, "_p_adjusted")
  
  format_p_value(
    row[[p_col]]
  )
}

profile_to_combination <- function(
    profile,
    genes = CARBA_PROFILE_GENES
) {
  
  vapply(
    profile,
    function(x) {
      
      bits <- strsplit(
        x,
        "",
        fixed = TRUE
      )[[1]] == "1"
      
      if (!any(bits)) {
        return("Negative")
      }
      
      paste(
        genes[bits],
        collapse = " + "
      )
    },
    character(1)
  )
}


all_binary_profiles <- function(
    n = length(CARBA_PROFILE_GENES)
) {
  
  grid <- expand.grid(
    rep(
      list(c(0L, 1L)),
      n
    ),
    KEEP.OUT.ATTRS = FALSE,
    stringsAsFactors = FALSE
  )
  
  apply(
    grid,
    1,
    paste0,
    collapse = ""
  )
}

format_top_combinations <- function(
    summary
) {
  
  x <- summary$top_combinations
  
  items <- sprintf(
    "%s (%s samples)",
    x$combination,
    scales::comma(x$observed)
  )
  
  if (length(items) == 1) {
    return(items)
  }
  
  if (length(items) == 2) {
    return(
      paste(
        items,
        collapse = " and "
      )
    )
  }
  
  paste0(
    paste(
      items[-length(items)],
      collapse = ", "
    ),
    ", and ",
    items[[length(items)]]
  )
}

pairwise_summary_value <- function(
    summary,
    section = c(
      "most_frequent",
      "strongest_enrichment"
    ),
    column
) {
  
  section <- match.arg(section)
  
  x <- summary[[section]]
  
  if (nrow(x) != 1) {
    stop(
      "Expected exactly one row in ",
      section
    )
  }
  
  x[[column]][[1]]
}


pairwise_summary_pair <- function(
    summary,
    section = c(
      "most_frequent",
      "strongest_enrichment"
    )
) {
  
  section <- match.arg(section)
  
  pairwise_summary_value(
    summary,
    section,
    "pair"
  )
}

pairwise_conditional_percent <- function(
    pairwise_stats,
    reference_gene,
    cooccurring_gene,
    digits = 1
) {
  
  row <- pairwise_stats |>
    filter(
      (gene_a == reference_gene &
         gene_b == cooccurring_gene) |
        (gene_a == cooccurring_gene &
           gene_b == reference_gene)
    )
  
  if (nrow(row) != 1) {
    stop(
      "Expected exactly one pairwise result for ",
      reference_gene,
      " / ",
      cooccurring_gene
    )
  }
  
  value <- if (
    row$gene_a[[1]] == reference_gene
  ) {
    row$p_b_given_a[[1]]
  } else {
    row$p_a_given_b[[1]]
  }
  
  sprintf(
    paste0("%.", digits, "f"),
    value * 100
  )
}