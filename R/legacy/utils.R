encode_carba <- function(df,
                         targets = c("OXA48", "VIM", "KPC", "NDM"),
                         prefix = "",
                         suffix = "") {
  cols <- paste0(prefix, targets, suffix)
  apply(df[, cols, drop = FALSE], 1, paste0, collapse = "")
}

decode_carba <- function(code,
                         targets = c("OXA48", "VIM", "KPC", "NDM"),
                         prefix = "",
                         suffix = "") {
  
  x <- trimws(as.character(code))
  n <- length(targets)
  
  ok <- grepl(paste0("^[01]{", n, "}$"), x)
  if (any(!ok & !is.na(x))) {
    warning("Hay valores que no son códigos binarios válidos. Se pondrá NA en esas filas.")
  }
  
  mat <- matrix(
    NA_integer_,
    nrow = length(x),
    ncol = n,
    dimnames = list(NULL, paste0(prefix, targets, suffix))
  )
  
  if (any(ok)) {
    chars <- strsplit(x[ok], "", fixed = TRUE)
    mat_ok <- do.call(rbind, lapply(chars, as.integer))
    mat[ok, ] <- mat_ok
  }
  
  as.data.frame(mat)
}