"""Shared categorical values for the PCR pipeline."""

from enum import StrEnum


class ResultLevels(StrEnum):
    positive = "POSITIVE"
    negative = "NEGATIVE"
    indeterminate = "INDETERMINATE"
    error = "ERROR"


class PCRTargets(StrEnum):
    oxa48 = "OXA48"
    vim = "VIM"
    ndm = "NDM"
    kpc = "KPC"
    control_16s = "16S"
    screening = "Screening"
    fail = "FAIL"
    fail_2 = "FAIL-2"


class PCRTargetAliases(StrEnum):
    oxa = "OXA"
    screening = "SCREENING"


class PCRSheetKinds(StrEnum):
    cq_results = "cq_results"
    amplification_sybr = "amplification_sybr"
    melt_curve_sybr = "melt_curve_sybr"
    melt_curve_derivative_sybr = "melt_curve_derivative_sybr"
    run_information = "run_information"


class PCRFileKinds(StrEnum):
    quantification_cq_results = "quantification_cq_results"
    quantification_amplification_results = "quantification_amplification_results"
    melt_curve_rfu_results = "melt_curve_rfu_results"
    melt_curve_derivative_results = "melt_curve_derivative_results"


class PCRSheetPatterns(StrEnum):
    sybr = "sybr"


class PCRMeasurementKinds(StrEnum):
    amplification_rfu = "amplification_rfu"
    melt_curve_rfu = "melt_curve_rfu"
    melt_curve_derivative = "melt_curve_derivative"


class PCRAxisKinds(StrEnum):
    cycle = "cycle"
    temperature = "temperature"


class MaterializationStatuses(StrEnum):
    loaded = "LOADED"
    loaded_with_errors = "LOADED_WITH_ERRORS"
    load_error = "LOAD_ERROR"


class DerivationStatuses(StrEnum):
    running = "RUNNING"
    success = "SUCCESS"
    failed = "FAILED"


class WellRoles(StrEnum):
    real_sample = "REAL_SAMPLE"
    positive_control = "POSITIVE_CONTROL"
    negative_control = "NEGATIVE_CONTROL"
    non_sample_review = "NON_SAMPLE_REVIEW"


class ClassificationRules(StrEnum):
    empty = "EMPTY"
    numeric = "NUMERIC"
    unknown_text = "UNKNOWN_TEXT"


class RunClasses(StrEnum):
    failed = "FAILED"
    screening = "SCREENING"
    panel_oxa48_vim = "PANEL_OXA48_VIM"
    panel_kpc_ndm = "PANEL_KPC_NDM"
    compensation_partial = "COMPENSATION_PARTIAL"
    other = "OTHER"


class RunTypes(StrEnum):
    screening = "SCREENING"
    specific = "SPECIFIC"


class QCStatuses(StrEnum):
    valid = "VALID"
    warning = "WARNING"
    invalid = "INVALID"
    partially_invalid = "PARTIALLY_INVALID"
    valid_with_warnings = "VALID_WITH_WARNINGS"


class QCIssues(StrEnum):
    run_failed = "RUN_FAILED"
    missing_positive_control = "MISSING_POSITIVE_CONTROL"
    missing_negative_control = "MISSING_NEGATIVE_CONTROL"
    positive_control_not_positive = "POSITIVE_CONTROL_NOT_POSITIVE"
    positive_control_melting_incompatible = "POSITIVE_CONTROL_MELTING_INCOMPATIBLE"
    negative_control_positive = "NEGATIVE_CONTROL_POSITIVE"
    negative_control_not_negative = "NEGATIVE_CONTROL_NOT_NEGATIVE"
    negative_control_indeterminate = "NEGATIVE_CONTROL_INDETERMINATE"


class EpisodeAssociationStatuses(StrEnum):
    automatic = "AUTO"
    review = "REVIEW"


class ResultRelations(StrEnum):
    single = "SINGLE"
    correction = "CORRECTION"
    panel_complement = "PANEL_COMPLEMENT"
    historical_repeat = "HISTORICAL_REPEAT"


class ConcordanceLevels(StrEnum):
    no_valid_result = "NO_VALID_RESULT"
    concordant = "CONCORDANT"
    discordant = "DISCORDANT"
