build_patients <- function(data) {
  
  data |>
    arrange(nhc, entry_date) |>
    distinct(nhc, .keep_all = TRUE) |>
    select(
      nhc,
      sex,
      age,
      age_cat
    )
}

build_specific_valid <- function(data) {
  data |>
    filter(
      specific_status == "SPECIFIC_COMPLETED",
      specific_validation == "SPECIFIC_VALID"
    )
}


build_positive_samples <- function(data) {
  data |>
    filter(pcr_category == "PCR_POSITIVE")
}

build_demographic_carba_samples <- function(
    data,
    carba_map = CARBAPENEMASE_MAP
) {
  
  outcome_map <- c(
    any_gene = ANY_GENE_LABEL,
    carba_map
  )
  
  data |>
    distinct(sample, .keep_all = TRUE) |>
    mutate(
      age_group = case_when(
        age < AGE_PEDIATRIC_CUTOFF ~ AGE_GROUP_LEVELS[[1]],
        age < AGE_OLDER_ADULT_CUTOFF ~ AGE_GROUP_LEVELS[[2]],
        age >= AGE_OLDER_ADULT_CUTOFF ~ AGE_GROUP_LEVELS[[3]],
        TRUE ~ NA_character_
      ),
      
      any_gene = as.integer(
        if_any(
          all_of(names(carba_map)),
          ~ coalesce(.x, 0) == 1
        )
      )
    ) |>
    select(
      sample,
      nhc,
      sex,
      age,
      age_group,
      any_gene,
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
        levels = CARBAPENEMASE_LEVELS
      ),
      
      age_group = factor(
        age_group,
        levels = AGE_GROUP_LEVELS
      )
    ) |>
    select(
      -carbapenemase_column
    )
}


build_patient_carba_outcomes <- function(
    data,
    patients,
    carba_map = CARBAPENEMASE_MAP
) {
  
  outcome_map <- c(
    any_gene = ANY_GENE_LABEL,
    carba_map
  )
  
  patient_outcomes <- data |>
    distinct(sample, .keep_all = TRUE) |>
    group_by(nhc) |>
    summarise(
      across(
        all_of(names(carba_map)),
        ~ as.integer(any(.x == 1, na.rm = TRUE))
      ),
      .groups = "drop"
    ) |>
    mutate(
      any_gene = as.integer(
        if_any(
          all_of(names(carba_map)),
          ~ .x == 1
        )
      )
    ) |>
    pivot_longer(
      cols = all_of(names(outcome_map)),
      names_to = "carbapenemase_column",
      values_to = "positive"
    ) |>
    mutate(
      carbapenemase = unname(
        outcome_map[carbapenemase_column]
      ),
      carbapenemase = factor(
        carbapenemase,
        levels = CARBAPENEMASE_LEVELS
      )
    ) |>
    select(
      nhc,
      carbapenemase,
      positive
    )
  
  patients |>
    select(
      nhc,
      sex,
      age
    ) |>
    left_join(
      patient_outcomes,
      by = "nhc",
      relationship = "one-to-many"
    )
}

build_carba_profiles <- function(
    data,
    profile_col = CARBA_PROFILE_COL,
    genes = CARBA_PROFILE_GENES,
    invalid_values = c("INVALID")
) {
  
  profiles <- data |>
    transmute(
      sample,
      profile = as.character(.data[[profile_col]])
    ) |>
    filter(
      !is.na(profile),
      !profile %in% invalid_values
    ) |>
    distinct()
  
  
  # Una muestra no puede tener más de un perfil
  conflicts <- profiles |>
    count(sample) |>
    filter(n > 1)
  
  if (nrow(conflicts) > 0) {
    stop(
      "Some samples have more than one ",
      profile_col,
      " value."
    )
  }
  
  
  expected_pattern <- paste0(
    "^[01]{",
    length(genes),
    "}$"
  )
  
  invalid <- profiles |>
    filter(
      !stringr::str_detect(
        profile,
        expected_pattern
      )
    )
  
  if (nrow(invalid) > 0) {
    stop(
      "Unexpected carbapenemase profiles detected: ",
      paste(
        unique(invalid$profile),
        collapse = ", "
      )
    )
  }
  
  
  profiles |>
    mutate(
      n_genes = stringr::str_count(
        profile,
        "1"
      ),
      
      combination = profile_to_combination(
        profile,
        genes
      )
    )
}

build_carba_binary_matrix <- function(
    profiles,
    genes = CARBA_PROFILE_GENES
) {
  
  profiles <- profiles |>
    arrange(sample)
  
  carba_matrix <- stringr::str_split_fixed(
    profiles$profile,
    "",
    length(genes)
  )
  
  storage.mode(carba_matrix) <- "integer"
  
  colnames(carba_matrix) <- genes
  rownames(carba_matrix) <- profiles$sample
  
  carba_matrix
}

build_pcr_lis_comparison <- function(data) {
  
  fis_cols <- c(
    fis_oxa48 = "OXA-48",
    fis_vim   = "VIM",
    fis_kpc   = "KPC",
    fis_ndm   = "NDM"
  )
  
  sil_cols <- c(
    oxa48 = "OXA-48",
    vim   = "VIM",
    kpc   = "KPC",
    ndm   = "NDM"
  )
  
  
  # -------------------------
  # Validación mínima
  # -------------------------
  
  required_cols <- c(
    "sample",
    names(fis_cols),
    names(sil_cols)
  )
  
  missing_cols <- setdiff(
    required_cols,
    names(data)
  )
  
  if (length(missing_cols) > 0) {
    stop(
      "Missing required columns: ",
      paste(missing_cols, collapse = ", ")
    )
  }
  
  
  # -------------------------
  # Una fila lógica por muestra
  # -------------------------
  
  samples <- data |>
    select(
      sample,
      all_of(names(fis_cols)),
      all_of(names(sil_cols))
    ) |>
    distinct()
  
  
  duplicated_samples <- samples |>
    count(sample) |>
    filter(n > 1)
  
  if (nrow(duplicated_samples) > 0) {
    stop(
      nrow(duplicated_samples),
      " samples have more than one FIS/SIL result."
    )
  }
  
  
  # -------------------------
  # FIS -> long
  # -------------------------
  
  fis_long <- samples |>
    select(
      sample,
      all_of(names(fis_cols))
    ) |>
    pivot_longer(
      cols = all_of(names(fis_cols)),
      names_to = "source_column",
      values_to = "fis_positive"
    ) |>
    mutate(
      carbapenemase = unname(
        fis_cols[source_column]
      )
    ) |>
    select(
      sample,
      carbapenemase,
      fis_positive
    )
  
  
  # -------------------------
  # SIL -> long
  # -------------------------
  
  sil_long <- samples |>
    select(
      sample,
      all_of(names(sil_cols))
    ) |>
    pivot_longer(
      cols = all_of(names(sil_cols)),
      names_to = "source_column",
      values_to = "sil_positive"
    ) |>
    mutate(
      carbapenemase = unname(
        sil_cols[source_column]
      )
    ) |>
    select(
      sample,
      carbapenemase,
      sil_positive
    )
  
  
  # -------------------------
  # Comparación final
  # -------------------------
  
  fis_long |>
    left_join(
      sil_long,
      by = c(
        "sample",
        "carbapenemase"
      ),
      relationship = "one-to-one"
    ) |>
    mutate(
      carbapenemase = factor(
        carbapenemase,
        levels = c(
          "OXA-48",
          "VIM",
          "KPC",
          "NDM"
        )
      )
    ) |>
    arrange(
      sample,
      carbapenemase
    )
}

build_first_sample_per_patient <- function(
    data,
    id_col = "nhc",
    sample_col = "sample",
    date_col = "entry_date"
) {
  
  required_cols <- c(
    id_col,
    sample_col,
    date_col
  )
  
  missing_cols <- setdiff(
    required_cols,
    names(data)
  )
  
  if (length(missing_cols) > 0) {
    stop(
      "Missing required columns: ",
      paste(missing_cols, collapse = ", ")
    )
  }
  
  
  samples <- data |>
    select(
      all_of(required_cols)
    ) |>
    distinct() |>
    filter(
      !is.na(.data[[id_col]]),
      !is.na(.data[[sample_col]]),
      !is.na(.data[[date_col]])
    )
  
  
  # Una muestra no debe pertenecer a más de un paciente
  invalid_samples <- samples |>
    count(.data[[sample_col]]) |>
    filter(n > 1)
  
  if (nrow(invalid_samples) > 0) {
    stop(
      nrow(invalid_samples),
      " samples are associated with more than one patient/date combination."
    )
  }
  
  
  samples |>
    arrange(
      .data[[id_col]],
      .data[[date_col]],
      .data[[sample_col]]
    ) |>
    group_by(
      .data[[id_col]]
    ) |>
    slice_head(n = 1) |>
    ungroup()
}


build_relative_load_data <- function(
    data,
    load_map = RELATIVE_LOAD_MAP,
    fis_map = FIS_CANONICAL_MAP,
    sil_map = SIL_CANONICAL_MAP,
    metadata_cols = c(
      "sample",
      "nhc",
      "entry_date",
      "origin_code",
      "department_description",
      "population_context"
    )
) {
  
  required_cols <- c(
    metadata_cols,
    names(load_map),
    names(fis_map),
    names(sil_map)
  )
  
  missing_cols <- setdiff(
    required_cols,
    names(data)
  )
  
  if (length(missing_cols) > 0) {
    stop(
      "Missing required columns: ",
      paste(missing_cols, collapse = ", ")
    )
  }
  
  
  # El core debe contener un único registro por muestra
  duplicated_samples <- data |>
    count(sample) |>
    filter(n > 1)
  
  if (nrow(duplicated_samples) > 0) {
    stop(
      nrow(duplicated_samples),
      " samples occur more than once in the FIS core."
    )
  }
  
  
  # -------------------------
  # Relative loads
  # -------------------------
  
  loads <- data |>
    select(
      all_of(metadata_cols),
      all_of(names(load_map))
    ) |>
    pivot_longer(
      cols = all_of(names(load_map)),
      names_to = "source_column",
      values_to = "relative_load"
    ) |>
    mutate(
      carbapenemase = unname(
        load_map[source_column]
      )
    ) |>
    select(
      -source_column
    )
  
  
  # -------------------------
  # FIS result
  # -------------------------
  
  fis <- data |>
    select(
      sample,
      all_of(names(fis_map))
    ) |>
    pivot_longer(
      cols = all_of(names(fis_map)),
      names_to = "source_column",
      values_to = "fis_positive"
    ) |>
    mutate(
      carbapenemase = unname(
        fis_map[source_column]
      )
    ) |>
    select(
      sample,
      carbapenemase,
      fis_positive
    )
  
  
  # -------------------------
  # SIL result
  # -------------------------
  
  sil <- data |>
    select(
      sample,
      all_of(names(sil_map))
    ) |>
    pivot_longer(
      cols = all_of(names(sil_map)),
      names_to = "source_column",
      values_to = "sil_positive"
    ) |>
    mutate(
      carbapenemase = unname(
        sil_map[source_column]
      )
    ) |>
    select(
      sample,
      carbapenemase,
      sil_positive
    )
  
  
  result <- loads |>
    left_join(
      fis,
      by = c(
        "sample",
        "carbapenemase"
      ),
      relationship = "one-to-one"
    ) |>
    left_join(
      sil,
      by = c(
        "sample",
        "carbapenemase"
      ),
      relationship = "one-to-one"
    )
  
  
  # Una carga cuantificada debe corresponder a un FIS positivo
  invalid_loads <- result |>
    filter(
      !is.na(relative_load),
      fis_positive != 1 | is.na(fis_positive)
    )
  
  if (nrow(invalid_loads) > 0) {
    stop(
      nrow(invalid_loads),
      " relative-load values correspond to non-positive FIS results."
    )
  }
  
  
  result |>
    filter(
      !is.na(relative_load)
    ) |>
    mutate(
      relative_ratio = 10^relative_load,
      relative_percent = 100 * relative_ratio,
      
      lis_result = factor(
        sil_positive,
        levels = c(0, 1),
        labels = c(
          "Negative",
          "Positive"
        )
      ),
      
      carbapenemase = factor(
        carbapenemase,
        levels = unname(load_map)
      )
    )
}

add_unit_service_classification <- function(
    data,
    unit_services
) {
  
  key_cols <- c(
    "origin_code",
    "service_code"
  )
  
  classification_cols <- c(
    "classification_support_n",
    "department_code",
    "department_description",
    "clinical_group_code",
    "clinical_group_description",
    "population_context",
    "classification_confidence",
    "assignment_rule",
    "population_rule",
    "classification_note"
  )
  
  # -------------------------
  # Validación mínima
  # -------------------------
  
  missing_data_cols <- setdiff(
    key_cols,
    names(data)
  )
  
  if (length(missing_data_cols) > 0) {
    stop(
      "data is missing required columns: ",
      paste(missing_data_cols, collapse = ", ")
    )
  }
  
  missing_lookup_cols <- setdiff(
    c(key_cols, classification_cols),
    names(unit_services)
  )
  
  if (length(missing_lookup_cols) > 0) {
    stop(
      "unit_services is missing required columns: ",
      paste(missing_lookup_cols, collapse = ", ")
    )
  }
  
  # -------------------------
  # Una clasificación por
  # origin × service
  # -------------------------
  
  duplicated_keys <- unit_services |>
    dplyr::count(
      origin_code,
      service_code
    ) |>
    dplyr::filter(n > 1)
  
  if (nrow(duplicated_keys) > 0) {
    stop(
      nrow(duplicated_keys),
      " origin_code/service_code combinations ",
      "have more than one classification."
    )
  }
  
  classification <- unit_services |>
    dplyr::select(
      dplyr::all_of(key_cols),
      dplyr::all_of(classification_cols)
    )
  
  # Permitir ejecutar la derivation más de una vez
  data <- data |>
    dplyr::select(
      -dplyr::any_of(classification_cols)
    )
  
  n_before <- nrow(data)
  
  result <- data |>
    dplyr::left_join(
      classification,
      by = key_cols,
      relationship = "many-to-one",
      na_matches = "na"
    )
  
  if (nrow(result) != n_before) {
    stop(
      "Unit/service classification join changed ",
      "the number of rows: ",
      n_before,
      " -> ",
      nrow(result)
    )
  }
  
  result
}

# =============================================================================
# Microorganisms
# =============================================================================

build_organism_records <- function(
    data,
    sample_col = "sample",
    organism_code_col = "microorg_code",
    organism_col = "microorg_name",
    culture_status_col = "result_interpretate",
    positive_culture_pattern = "positive",
    carba_map = SIL_CANONICAL_MAP
) {
  
  required_cols <- c(
    sample_col,
    organism_code_col,
    organism_col,
    culture_status_col,
    names(carba_map)
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
  # Keep only actual organism recoveries
  #
  # Culture positivity is identified by the presence of
  # positive_culture_pattern in the culture-status field,
  # case-insensitively.
  # -------------------------------------------------------------------------
  
  organism_data <- data |>
    transmute(
      sample =
        .data[[sample_col]],
      
      organism_code =
        trimws(
          as.character(
            .data[[organism_code_col]]
          )
        ),
      
      organism =
        trimws(
          as.character(
            .data[[organism_col]]
          )
        ),
      
      culture_status =
        stringr::str_squish(
          as.character(
            .data[[culture_status_col]]
          )
        ),
      
      across(
        all_of(
          names(carba_map)
        )
      )
    ) |>
    mutate(
      organism_code =
        na_if(
          organism_code,
          ""
        ),
      
      organism =
        na_if(
          organism,
          ""
        ),
      
      culture_positive =
        stringr::str_detect(
          stringr::str_to_lower(
            coalesce(
              culture_status,
              ""
            )
          ),
          stringr::fixed(
            stringr::str_to_lower(
              positive_culture_pattern
            )
          )
        )
    ) |>
    filter(
      culture_positive,
      
      !is.na(sample),
      
      !is.na(organism),
      
      is.na(organism_code) |
        organism_code != "0"
    )
  
  
  # -------------------------------------------------------------------------
  # Validate carbapenemase fields
  # -------------------------------------------------------------------------
  
  for (col in names(carba_map)) {
    
    unexpected_values <- organism_data |>
      filter(
        !is.na(.data[[col]]),
        !.data[[col]] %in% c(0, 1)
      ) |>
      distinct(
        .data[[col]]
      ) |>
      pull()
    
    if (length(unexpected_values) > 0) {
      stop(
        "Unexpected values in ",
        col,
        ": ",
        paste(
          unexpected_values,
          collapse = ", "
        )
      )
    }
  }
  
  
  # -------------------------------------------------------------------------
  # Stable organism identifier
  #
  # Prefer the SIL organism code. If unavailable, fall back to the name.
  # -------------------------------------------------------------------------
  
  organism_data <- organism_data |>
    mutate(
      organism_key =
        if_else(
          !is.na(organism_code),
          paste0(
            "code:",
            organism_code
          ),
          paste0(
            "name:",
            organism
          )
        )
    )
  
  
  # -------------------------------------------------------------------------
  # A given organism code should not map to multiple names
  # -------------------------------------------------------------------------
  
  organism_name_conflicts <- organism_data |>
    filter(
      !is.na(organism_code)
    ) |>
    distinct(
      organism_code,
      organism
    ) |>
    count(
      organism_code,
      name = "n_names"
    ) |>
    filter(
      n_names > 1
    )
  
  if (nrow(organism_name_conflicts) > 0) {
    stop(
      nrow(organism_name_conflicts),
      " organism codes are associated with more than one organism name."
    )
  }
  
  
  # -------------------------------------------------------------------------
  # Deduplicate sample × organism
  #
  # If duplicated SIL rows exist:
  #   - any positive result dominates;
  #   - otherwise an observed negative is retained;
  #   - all missing remains missing.
  # -------------------------------------------------------------------------
  
  collapse_binary <- function(x) {
    
    if (all(is.na(x))) {
      return(NA_integer_)
    }
    
    if (any(x == 1, na.rm = TRUE)) {
      return(1L)
    }
    
    0L
  }
  
  
  organism_data |>
    group_by(
      sample,
      organism_key,
      organism_code,
      organism
    ) |>
    summarise(
      across(
        all_of(
          names(carba_map)
        ),
        collapse_binary
      ),
      
      .groups = "drop"
    )
}

build_positive_organism_carba_pairs <- function(
    organism_records,
    carba_map = SIL_CANONICAL_MAP
) {
  
  required_cols <- c(
    "sample",
    "organism_key",
    "organism",
    names(carba_map)
  )
  
  missing_cols <- setdiff(
    required_cols,
    names(organism_records)
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
  
  
  organism_records |>
    pivot_longer(
      cols =
        all_of(
          names(carba_map)
        ),
      names_to =
        "source_column",
      values_to =
        "attributed_positive"
    ) |>
    mutate(
      carbapenemase =
        unname(
          carba_map[
            source_column
          ]
        ),
      
      carbapenemase =
        factor(
          carbapenemase,
          levels =
            unname(
              carba_map
            )
        )
    ) |>
    filter(
      attributed_positive == 1L
    ) |>
    select(
      sample,
      organism_key,
      organism_code,
      organism,
      carbapenemase
    )
}

build_organism_carba_profiles <- function(
    organism_records,
    carba_map = SIL_CANONICAL_MAP
) {
  
  required_cols <- c(
    "sample",
    "organism_key",
    "organism",
    names(carba_map)
  )
  
  missing_cols <- setdiff(
    required_cols,
    names(organism_records)
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
  # Build four-bit carbapenemase profile
  #
  # Order is inherited from SIL_CANONICAL_MAP:
  # OXA-48 -> VIM -> KPC -> NDM
  #
  # Profiles are only considered complete when all four target states
  # are available.
  # -------------------------------------------------------------------------
  
  profile_matrix <- organism_records |>
    select(
      all_of(
        names(carba_map)
      )
    ) |>
    as.matrix()
  
  
  profile <- apply(
    profile_matrix,
    1,
    function(x) {
      
      if (any(is.na(x))) {
        return(NA_character_)
      }
      
      paste0(
        as.integer(x),
        collapse = ""
      )
    }
  )
  
  
  # -------------------------------------------------------------------------
  # Number of attributed mechanisms
  # -------------------------------------------------------------------------
  
  n_mechanisms <- apply(
    profile_matrix,
    1,
    function(x) {
      
      if (any(is.na(x))) {
        return(NA_integer_)
      }
      
      as.integer(
        sum(
          x == 1L
        )
      )
    }
  )
  
  
  # -------------------------------------------------------------------------
  # Human-readable profile
  #
  # Examples:
  # 1000 -> OXA-48
  # 1100 -> OXA-48 + VIM
  # 1111 -> OXA-48 + VIM + KPC + NDM
  #
  # profile_to_combination() already exists in utils.R.
  # -------------------------------------------------------------------------
  
  combination <- vapply(
    profile,
    function(x) {
      
      if (is.na(x)) {
        return(NA_character_)
      }
      
      profile_to_combination(
        x,
        genes =
          unname(
            carba_map
          )
      )
    },
    character(1)
  )
  
  
  organism_records |>
    mutate(
      profile =
        profile,
      
      combination =
        combination,
      
      n_mechanisms =
        n_mechanisms
    )
}

build_pcr_carrier_comparison <- function(
    pcr_data,
    positive_organism_carba,
    sample_col = "sample",
    pcr_map = CARBAPENEMASE_MAP
) {
  
  required_pcr_cols <- c(
    sample_col,
    names(pcr_map)
  )
  
  missing_pcr_cols <- setdiff(
    required_pcr_cols,
    names(pcr_data)
  )
  
  if (length(missing_pcr_cols) > 0) {
    stop(
      "Missing PCR columns: ",
      paste(
        missing_pcr_cols,
        collapse = ", "
      )
    )
  }
  
  
  required_carrier_cols <- c(
    "sample",
    "organism_key",
    "organism",
    "carbapenemase"
  )
  
  missing_carrier_cols <- setdiff(
    required_carrier_cols,
    names(positive_organism_carba)
  )
  
  if (length(missing_carrier_cols) > 0) {
    stop(
      "Missing carrier columns: ",
      paste(
        missing_carrier_cols,
        collapse = ", "
      )
    )
  }
  
  
  # -------------------------------------------------------------------------
  # PCR core must contain one row per sample
  # -------------------------------------------------------------------------
  
  duplicated_samples <- pcr_data |>
    count(
      .data[[sample_col]],
      name = "n"
    ) |>
    filter(
      n > 1
    )
  
  if (nrow(duplicated_samples) > 0) {
    stop(
      nrow(duplicated_samples),
      " samples occur more than once in the PCR core."
    )
  }
  
  
  # -------------------------------------------------------------------------
  # PCR results -> long
  # -------------------------------------------------------------------------
  
  pcr_long <- pcr_data |>
    select(
      all_of(
        required_pcr_cols
      )
    ) |>
    pivot_longer(
      cols =
        all_of(
          names(pcr_map)
        ),
      names_to =
        "source_column",
      values_to =
        "pcr_positive"
    ) |>
    mutate(
      carbapenemase =
        unname(
          pcr_map[
            source_column
          ]
        ),
      
      carbapenemase =
        factor(
          carbapenemase,
          levels =
            unname(
              pcr_map
            )
        )
    ) |>
    select(
      sample =
        all_of(
          sample_col
        ),
      carbapenemase,
      pcr_positive
    )
  
  
  # -------------------------------------------------------------------------
  # Culture: at least one recovered organism carrying the mechanism
  # -------------------------------------------------------------------------
  
  carrier_summary <- positive_organism_carba |>
    group_by(
      sample,
      carbapenemase
    ) |>
    summarise(
      n_carrier_organisms =
        n_distinct(
          organism_key
        ),
      
      carrier_organisms =
        paste(
          sort(
            unique(
              organism
            )
          ),
          collapse = " + "
        ),
      
      .groups = "drop"
    )
  
  
  # -------------------------------------------------------------------------
  # PCR × attributed carrier recovery
  # -------------------------------------------------------------------------
  
  pcr_long |>
    left_join(
      carrier_summary,
      by = c(
        "sample",
        "carbapenemase"
      ),
      relationship =
        "one-to-one"
    ) |>
    mutate(
      n_carrier_organisms =
        coalesce(
          n_carrier_organisms,
          0L
        ),
      
      carrier_recovered =
        n_carrier_organisms > 0,
      
      comparison =
        case_when(
          is.na(pcr_positive) ~
            "PCR missing",
          
          pcr_positive == 1 &
            carrier_recovered ~
            "PCR+ / carrier recovered",
          
          pcr_positive == 1 &
            !carrier_recovered ~
            "PCR+ / no carrier recovered",
          
          pcr_positive == 0 &
            carrier_recovered ~
            "PCR− / carrier recovered",
          
          pcr_positive == 0 &
            !carrier_recovered ~
            "PCR− / no carrier recovered"
        )
    )
}

build_pcr_culture_profile_agreement <- function(
    target_comparison,
    genes = CARBA_PROFILE_GENES
) {
  
  required_cols <- c(
    "sample",
    "carbapenemase",
    "pcr_positive",
    "carrier_recovered"
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
  
  
  # -------------------------------------------------------------------------
  # One row per sample × carbapenemase
  # -------------------------------------------------------------------------
  
  duplicated_targets <- target_comparison |>
    count(
      sample,
      carbapenemase,
      name = "n"
    ) |>
    filter(
      n > 1
    )
  
  if (nrow(duplicated_targets) > 0) {
    stop(
      nrow(duplicated_targets),
      " sample/carbapenemase combinations occur more than once."
    )
  }
  
  
  # -------------------------------------------------------------------------
  # Every sample must contain the four expected targets
  # -------------------------------------------------------------------------
  
  incomplete_samples <- target_comparison |>
    filter(
      as.character(carbapenemase) %in%
        genes
    ) |>
    group_by(
      sample
    ) |>
    summarise(
      n_targets =
        n_distinct(
          carbapenemase
        ),
      .groups = "drop"
    ) |>
    filter(
      n_targets !=
        length(genes)
    )
  
  if (nrow(incomplete_samples) > 0) {
    stop(
      nrow(incomplete_samples),
      " samples do not contain all expected carbapenemase targets."
    )
  }
  
  
  # -------------------------------------------------------------------------
  # Build PCR and culture profiles
  #
  # PCR:
  #   direct molecular result.
  #
  # Culture:
  #   union of mechanisms attributed to all recovered carrier organisms.
  #
  # Order:
  #   OXA-48 -> VIM -> KPC -> NDM
  # -------------------------------------------------------------------------
  
  profile_data <- target_comparison |>
    filter(
      as.character(carbapenemase) %in%
        genes
    ) |>
    mutate(
      carbapenemase =
        factor(
          as.character(
            carbapenemase
          ),
          levels =
            genes
        )
    ) |>
    arrange(
      sample,
      carbapenemase
    ) |>
    group_by(
      sample
    ) |>
    summarise(
      pcr_profile =
        if (
          any(
            is.na(
              pcr_positive
            )
          )
        ) {
          NA_character_
        } else {
          paste0(
            as.integer(
              pcr_positive
            ),
            collapse = ""
          )
        },
      
      culture_profile =
        paste0(
          as.integer(
            carrier_recovered
          ),
          collapse = ""
        ),
      
      .groups = "drop"
    )
  
  
  # -------------------------------------------------------------------------
  # Human-readable combinations
  # -------------------------------------------------------------------------
  
  pcr_combination <-
    rep(
      NA_character_,
      nrow(profile_data)
    )
  
  complete_pcr_profile <-
    !is.na(
      profile_data$pcr_profile
    )
  
  pcr_combination[
    complete_pcr_profile
  ] <-
    profile_to_combination(
      profile_data$pcr_profile[
        complete_pcr_profile
      ],
      genes =
        genes
    )
  
  
  culture_combination <-
    profile_to_combination(
      profile_data$culture_profile,
      genes =
        genes
    )
  
  
  profile_data <- profile_data |>
    mutate(
      pcr_combination =
        pcr_combination,
      
      culture_combination =
        culture_combination
    )
  
  # -------------------------------------------------------------------------
  # Profile-level relationship
  # -------------------------------------------------------------------------
  
  profile_data |>
    mutate(
      profile_relation =
        case_when(
          is.na(
            pcr_profile
          ) ~
            "PCR profile missing",
          
          pcr_profile ==
            culture_profile ~
            "Exact profile match",
          
          TRUE ~ {
            
            pcr_bits <-
              stringr::str_split_fixed(
                pcr_profile,
                "",
                length(genes)
              )
            
            culture_bits <-
              stringr::str_split_fixed(
                culture_profile,
                "",
                length(genes)
              )
            
            pcr_only <-
              rowSums(
                pcr_bits == "1" &
                  culture_bits == "0"
              )
            
            culture_only <-
              rowSums(
                pcr_bits == "0" &
                  culture_bits == "1"
              )
            
            case_when(
              pcr_only > 0 &
                culture_only == 0 ~
                "Additional PCR mechanism(s)",
              
              pcr_only == 0 &
                culture_only > 0 ~
                "Additional culture-attributed mechanism(s)",
              
              pcr_only > 0 &
                culture_only > 0 ~
                "Mixed profile mismatch",
              
              TRUE ~
                "Unclassified"
            )
          }
        )
    )
}

build_sample_mechanism_architecture <- function(
    positive_organism_carba
) {
  
  required_cols <- c(
    "sample",
    "organism_key",
    "carbapenemase"
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
  
  
  # -------------------------------------------------------------------------
  # One row per sample
  #
  # Only samples with at least one attributed organism–carbapenemase pair
  # enter this object.
  # -------------------------------------------------------------------------
  
  positive_organism_carba |>
    group_by(
      sample
    ) |>
    summarise(
      n_carrier_organisms =
        n_distinct(
          organism_key
        ),
      
      n_attributed_mechanisms =
        n_distinct(
          carbapenemase
        ),
      
      n_organism_mechanism_pairs =
        n_distinct(
          paste(
            organism_key,
            carbapenemase,
            sep = "___"
          )
        ),
      
      .groups = "drop"
    ) |>
    mutate(
      mechanism_architecture =
        case_when(
          n_carrier_organisms == 1 &
            n_attributed_mechanisms == 1 ~
            "Single carrier / single mechanism",
          
          n_carrier_organisms == 1 &
            n_attributed_mechanisms > 1 ~
            "Single carrier / multiple mechanisms",
          
          n_carrier_organisms > 1 &
            n_attributed_mechanisms == 1 ~
            "Multiple carriers / single mechanism",
          
          n_carrier_organisms > 1 &
            n_attributed_mechanisms > 1 ~
            "Multiple carriers / multiple mechanisms",
          
          TRUE ~
            "Unclassified"
        )
    )
}