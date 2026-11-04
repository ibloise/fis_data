normalize_identifier <- function(x) {
  
  x <- trimws(
    as.character(x)
  )
  
  x[x == ""] <- NA_character_
  
  x
}


next_pseudonyms <- function(
    existing_pseudonyms,
    prefix,
    width,
    n
) {
  
  if (n == 0) {
    return(character())
  }
  
  if (length(existing_pseudonyms) == 0) {
    
    start <- 1L
    
  } else {
    
    pattern <- paste0(
      "^",
      prefix,
      "-"
    )
    
    numbers <- existing_pseudonyms |>
      sub(
        pattern,
        "",
        x = _,
        perl = TRUE
      ) |>
      as.integer()
    
    if (
      length(numbers) == 0 ||
      all(is.na(numbers))
    ) {
      start <- 1L
    } else {
      start <- max(
        numbers,
        na.rm = TRUE
      ) + 1L
    }
  }
  
  values <- seq.int(
    from = start,
    length.out = n
  )
  
  sprintf(
    paste0(
      prefix,
      "-%0",
      width,
      "d"
    ),
    values
  )
}


pseudonymize_dataset <- function(
    data,
    registry_path,
    patient_col = "nhc",
    sample_col = "sample",
    patient_prefix = "P",
    patient_width = 4L,
    sample_prefix = "S",
    sample_width = 5L
) {
  
  required_cols <- c(
    patient_col,
    sample_col
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
  
  
  # -------------------------
  # Prepare current IDs
  # -------------------------
  
  current_ids <- data |>
    transmute(
      patient_source_id =
        normalize_identifier(
          .data[[patient_col]]
        ),
      
      sample_source_id =
        normalize_identifier(
          .data[[sample_col]]
        )
    )
  
  
  # A sample cannot belong to more than one patient
  sample_patient_conflicts <- current_ids |>
    filter(
      !is.na(sample_source_id),
      !is.na(patient_source_id)
    ) |>
    distinct(
      sample_source_id,
      patient_source_id
    ) |>
    count(
      sample_source_id,
      name = "n_patients"
    ) |>
    filter(
      n_patients > 1
    )
  
  if (nrow(sample_patient_conflicts) > 0) {
    stop(
      nrow(sample_patient_conflicts),
      " sample identifiers are associated with ",
      "more than one patient."
    )
  }
  
  
  current_patients <- current_ids |>
    filter(
      !is.na(patient_source_id)
    ) |>
    distinct(
      patient_source_id
    ) |>
    arrange(
      patient_source_id
    )
  
  
  current_samples <- current_ids |>
    filter(
      !is.na(sample_source_id)
    ) |>
    arrange(
      sample_source_id,
      desc(!is.na(patient_source_id))
    ) |>
    distinct(
      sample_source_id,
      .keep_all = TRUE
    ) |>
    arrange(
      sample_source_id
    )
  
  
  # -------------------------
  # Open registry
  # -------------------------
  
  dir.create(
    dirname(registry_path),
    recursive = TRUE,
    showWarnings = FALSE
  )
  
  con <- DBI::dbConnect(
    RSQLite::SQLite(),
    registry_path
  )
  
  on.exit(
    DBI::dbDisconnect(con),
    add = TRUE
  )
  
  DBI::dbExecute(
    con,
    "PRAGMA foreign_keys = ON"
  )
  
  DBI::dbExecute(
    con,
    "PRAGMA busy_timeout = 10000"
  )
  
  
  # -------------------------
  # Schema
  # -------------------------
  
  DBI::dbExecute(
    con,
    "
    CREATE TABLE IF NOT EXISTS patients (
      source_id   TEXT PRIMARY KEY,
      pseudonym   TEXT NOT NULL UNIQUE,
      first_seen  TEXT NOT NULL
    )
    "
  )
  
  DBI::dbExecute(
    con,
    "
    CREATE TABLE IF NOT EXISTS samples (
      source_id          TEXT PRIMARY KEY,
      patient_source_id  TEXT,
      pseudonym          TEXT NOT NULL UNIQUE,
      first_seen         TEXT NOT NULL,
      FOREIGN KEY(patient_source_id)
        REFERENCES patients(source_id)
    )
    "
  )
  
  
  # -------------------------
  # Transaction
  # -------------------------
  
  DBI::dbExecute(
    con,
    "BEGIN IMMEDIATE"
  )
  
  committed <- FALSE
  
  on.exit(
    {
      if (!committed) {
        try(
          DBI::dbExecute(
            con,
            "ROLLBACK"
          ),
          silent = TRUE
        )
      }
    },
    add = TRUE
  )
  
  
  # -------------------------
  # Existing patient registry
  # -------------------------
  
  patient_registry <- DBI::dbGetQuery(
    con,
    "
    SELECT
      source_id,
      pseudonym
    FROM patients
    "
  )
  
  
  new_patients <- current_patients |>
    anti_join(
      patient_registry,
      by = c(
        "patient_source_id" = "source_id"
      )
    )
  
  
  if (nrow(new_patients) > 0) {
    
    new_patients$pseudonym <-
      next_pseudonyms(
        existing_pseudonyms =
          patient_registry$pseudonym,
        
        prefix =
          patient_prefix,
        
        width =
          patient_width,
        
        n =
          nrow(new_patients)
      )
    
    patient_insert <- new_patients |>
      transmute(
        source_id =
          patient_source_id,
        
        pseudonym,
        
        first_seen =
          format(
            Sys.time(),
            tz = "UTC",
            usetz = TRUE
          )
      )
    
    DBI::dbWriteTable(
      con,
      "patients",
      patient_insert,
      append = TRUE,
      row.names = FALSE
    )
  }
  
  
  # Refresh after inserts
  patient_registry <- DBI::dbGetQuery(
    con,
    "
    SELECT
      source_id,
      pseudonym
    FROM patients
    "
  )
  
  
  # -------------------------
  # Existing sample registry
  # -------------------------
  
  sample_registry <- DBI::dbGetQuery(
    con,
    "
    SELECT
      source_id,
      patient_source_id,
      pseudonym
    FROM samples
    "
  )
  
  
  # Validate existing sample -> patient relation
  sample_validation <- current_samples |>
    inner_join(
      sample_registry,
      by = c(
        "sample_source_id" = "source_id"
      ),
      suffix = c(
        "_current",
        "_registry"
      )
    ) |>
    filter(
      !is.na(patient_source_id_current),
      !is.na(patient_source_id_registry),
      patient_source_id_current !=
        patient_source_id_registry
    )
  
  if (nrow(sample_validation) > 0) {
    stop(
      nrow(sample_validation),
      " existing samples are associated with ",
      "a different patient than in the registry."
    )
  }
  
  
  # If a sample was previously registered without patient,
  # allow filling that relationship later.
  missing_sample_patients <- current_samples |>
    inner_join(
      sample_registry,
      by = c(
        "sample_source_id" = "source_id"
      ),
      suffix = c(
        "_current",
        "_registry"
      )
    ) |>
    filter(
      is.na(patient_source_id_registry),
      !is.na(patient_source_id_current)
    )
  
  
  if (nrow(missing_sample_patients) > 0) {
    
    for (
      i in seq_len(
        nrow(missing_sample_patients)
      )
    ) {
      
      DBI::dbExecute(
        con,
        "
        UPDATE samples
        SET patient_source_id = ?
        WHERE source_id = ?
        ",
        params = list(
          missing_sample_patients$
            patient_source_id_current[[i]],
          
          missing_sample_patients$
            sample_source_id[[i]]
        )
      )
    }
  }
  
  
  # -------------------------
  # New samples
  # -------------------------
  
  new_samples <- current_samples |>
    anti_join(
      sample_registry,
      by = c(
        "sample_source_id" = "source_id"
      )
    )
  
  
  if (nrow(new_samples) > 0) {
    
    new_samples$pseudonym <-
      next_pseudonyms(
        existing_pseudonyms =
          sample_registry$pseudonym,
        
        prefix =
          sample_prefix,
        
        width =
          sample_width,
        
        n =
          nrow(new_samples)
      )
    
    sample_insert <- new_samples |>
      transmute(
        source_id =
          sample_source_id,
        
        patient_source_id,
        
        pseudonym,
        
        first_seen =
          format(
            Sys.time(),
            tz = "UTC",
            usetz = TRUE
          )
      )
    
    DBI::dbWriteTable(
      con,
      "samples",
      sample_insert,
      append = TRUE,
      row.names = FALSE
    )
  }
  
  
  # -------------------------
  # Commit
  # -------------------------
  
  DBI::dbExecute(
    con,
    "COMMIT"
  )
  
  committed <- TRUE
  
  
  # -------------------------
  # Retrieve final mappings
  # -------------------------
  
  patient_registry <- DBI::dbGetQuery(
    con,
    "
    SELECT
      source_id,
      pseudonym
    FROM patients
    "
  ) |>
    rename(
      patient_source_id = source_id,
      patient_pseudonym = pseudonym
    )
  
  
  sample_registry <- DBI::dbGetQuery(
    con,
    "
    SELECT
      source_id,
      pseudonym
    FROM samples
    "
  ) |>
    rename(
      sample_source_id = source_id,
      sample_pseudonym = pseudonym
    )
  
  
  # -------------------------
  # Add pseudonyms to dataset
  # -------------------------
  
  result <- data |>
    mutate(
      .patient_source_id =
        normalize_identifier(
          .data[[patient_col]]
        ),
      
      .sample_source_id =
        normalize_identifier(
          .data[[sample_col]]
        )
    ) |>
    left_join(
      patient_registry,
      by = c(
        ".patient_source_id" =
          "patient_source_id"
      ),
      relationship = "many-to-one"
    ) |>
    left_join(
      sample_registry,
      by = c(
        ".sample_source_id" =
          "sample_source_id"
      ),
      relationship = "many-to-one"
    ) |>
    select(
      -.patient_source_id,
      -.sample_source_id
    )
  
  
  # Contract: every non-missing real identifier
  # must now have a pseudonym.
  if (
    any(
      !is.na(
        normalize_identifier(
          result[[patient_col]]
        )
      ) &
      is.na(
        result$patient_pseudonym
      )
    )
  ) {
    stop(
      "Some patient identifiers could not be pseudonymized."
    )
  }
  
  
  if (
    any(
      !is.na(
        normalize_identifier(
          result[[sample_col]]
        )
      ) &
      is.na(
        result$sample_pseudonym
      )
    )
  ) {
    stop(
      "Some sample identifiers could not be pseudonymized."
    )
  }
  
  
  result
}