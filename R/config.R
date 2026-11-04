AgeCategories <- list(
  PEDIATRIC = "Pediatric",
  ADULT = "Adult"
)

SEX_LEVELS <- c(
  FEMALE = "F",
  MALE = "M"
)

AGE_PEDIATRIC_CUTOFF <- 18
AGE_OLDER_ADULT_CUTOFF <- 65

AGE_GROUP_LEVELS <- c(
  "<18 years",
  "18–64 years",
  "≥65 years"
)

STUDY_START <- as.Date("2023-07-01")
STUDY_END   <- as.Date("2025-05-31")

# Controla el split de unidades entre "alta frecuencia" y baja
UNIT_SAMPLE_THRESHOLD <- 50

# Controla el split de agregación de cola de torundas por paciente
PATIENT_SWAB_THRESHOLD <- 10

# Este hace lo mismo pero para el gráfico de Pareto. Lo dejamos independiente
SWABS_PER_PATIENT_PLOT_CUTOFF <- 10

# COntrola el umbral para dibujar categorías clínicas en el heatmap.
CLINICAL_CATEGORY_SAMPLE_THRESHOLD <- 50


CARBAPENEMASE_MAP <- c(
  oxa48_specific = "OXA-48",
  vim_specific   = "VIM",
  kpc_specific   = "KPC",
  ndm_specific   = "NDM"
)

ANY_GENE_LABEL <- "Any gene"

CARBAPENEMASE_LEVELS <- c(
  ANY_GENE_LABEL,
  unname(CARBAPENEMASE_MAP)
)

CARBA_COLORS <- c(
  "Any gene" = "#000000",
  "OXA-48"   = "#D55E00",
  "VIM"      = "#0072B2",
  "KPC"      = "#009E73",
  "NDM"      = "#CC79A7"
)


CARBA_PROFILE_COL <- "encode_fis"

CARBA_PROFILE_GENES <- c(
  "OXA-48",
  "VIM",
  "KPC",
  "NDM"
)

# Orden canónico de perfiles positivos de carbapenemasas.
#
# Genera:
# 1 mecanismo:
#   OXA-48
#   VIM
#   KPC
#   NDM
#
# 2 mecanismos:
#   OXA-48 + VIM
#   OXA-48 + KPC
#   OXA-48 + NDM
#   VIM + KPC
#   VIM + NDM
#   KPC + NDM
#
# Después las combinaciones triples y finalmente la cuádruple.

CARBA_PROFILE_COMBINATION_LEVELS <- unlist(
  lapply(
    seq_along(CARBA_PROFILE_GENES),
    function(k) {
      
      combinations <- combn(
        CARBA_PROFILE_GENES,
        k
      )
      
      apply(
        combinations,
        2,
        paste,
        collapse = " + "
      )
    }
  ),
  use.names = FALSE
)
FIS_CANONICAL_MAP <- c(
  fis_oxa48 = "OXA-48",
  fis_vim   = "VIM",
  fis_kpc   = "KPC",
  fis_ndm   = "NDM"
)

SIL_CANONICAL_MAP <- c(
  oxa48 = "OXA-48",
  vim   = "VIM",
  kpc   = "KPC",
  ndm   = "NDM"
)

RELATIVE_LOAD_MAP <- c(
  oxa48_log_delta = "OXA-48",
  vim_log_delta   = "VIM",
  kpc_log_delta   = "KPC",
  ndm_log_delta   = "NDM"
)

