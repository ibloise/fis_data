-- Diagnostico agregado. Ejecutar manualmente en la base analitica con psql
-- o un cliente SQL. No modifica datos ni muestra pacientes, muestras o aislados.
-- Contrasta la derivacion vigente con sus fuentes actualmente disponibles;
-- no reconstruye fuentes que hayan cambiado desde aquella derivacion.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '5min';

SELECT derivation_id, algorithm_version, started_at, finished_at,
       parameters_json::jsonb -> 'pcr_sil_linking' AS sil_linking_policy
FROM pcr_derivation_run
WHERE status = 'SUCCESS' AND is_current = TRUE;

SELECT candidate.policy_version, candidate.candidate_kind,
       candidate.candidate_status, COUNT(*) AS candidate_rows
FROM pcr_sil_link_candidate AS candidate
JOIN pcr_derivation_run AS run ON run.derivation_id = candidate.derivation_id
WHERE run.status = 'SUCCESS' AND run.is_current = TRUE
GROUP BY candidate.policy_version, candidate.candidate_kind,
         candidate.candidate_status
ORDER BY 1, 2, 3;

WITH current_run AS (
    SELECT derivation_id FROM pcr_derivation_run
    WHERE status = 'SUCCESS' AND is_current = TRUE
), exported AS (
    -- Detectar multiplicidad sin multiplicar el diagnostico de las fuentes.
    SELECT sample, microorg_code, COUNT(*) AS exported_rows,
           MAX(organism_oxa48) AS organism_oxa48,
           MAX(organism_vim) AS organism_vim,
           MAX(organism_kpc) AS organism_kpc,
           MAX(organism_ndm) AS organism_ndm,
           MAX(oxa48) AS global_oxa48, MAX(vim) AS global_vim,
           MAX(kpc) AS global_kpc, MAX(ndm) AS global_ndm
    FROM v_downstream_sil_pcr_organism
    WHERE microorg_code IS NOT NULL
    GROUP BY sample, microorg_code
), organism_targets AS (
    SELECT organism.*, target.marker_code, target.direct_positive,
           target.exported_positive, target.global_positive,
           exported.exported_rows,
           evidence.evidence_status,
           evidence.evidence_microb_sample_id
    FROM pcr_sil_organism_marker_summary AS organism
    JOIN current_run ON current_run.derivation_id = organism.derivation_id
    JOIN pcr_sample AS sample ON sample.pcr_sample_id = organism.pcr_sample_id
    LEFT JOIN exported
      ON exported.sample = sample.sample_key
     AND exported.microorg_code = organism.microorganism_code
    LEFT JOIN pcr_sil_retrospective_evidence AS evidence
      ON evidence.derivation_id = organism.derivation_id
     AND evidence.pcr_sample_id = organism.pcr_sample_id
     AND evidence.current_microb_sample_id = organism.microb_sample_id
    CROSS JOIN LATERAL (VALUES
        ('OXA', organism.oxa_detected, exported.organism_oxa48, exported.global_oxa48),
        ('VIM', organism.vim_detected, exported.organism_vim, exported.global_vim),
        ('KPC', organism.kpc_detected, exported.organism_kpc, exported.global_kpc),
        ('NDM', organism.ndm_detected, exported.organism_ndm, exported.global_ndm)
    ) AS target(marker_code, direct_positive, exported_positive, global_positive)
), support AS (
    SELECT target.*,
           EXISTS (
               SELECT 1 FROM pcr_sil_link_candidate AS candidate
               WHERE candidate.derivation_id = target.derivation_id
                 AND candidate.pcr_sample_id = target.pcr_sample_id
                 AND candidate.current_microb_sample_id = target.microb_sample_id
                 AND candidate.evidence_microb_sample_id = target.evidence_microb_sample_id
                 AND candidate.microorganism_code = target.microorganism_code
                 AND candidate.marker_code = target.marker_code
                 AND candidate.candidate_kind = 'PRIOR'
                 AND candidate.candidate_status = 'ACCEPTED'
           ) AS stored_candidate_positive,
           EXISTS (
               SELECT 1 FROM microb_sample_analysis AS analysis
               JOIN microb_analysis_result AS result
                 ON result.sample_analysis_id = analysis.sample_analysis_id
               JOIN microb_isolate AS isolate
                 ON isolate.analysis_result_id = result.analysis_result_id
                AND isolate.microorganism_code = target.microorganism_code
               JOIN microb_susceptibility_result AS susceptibility
                 ON susceptibility.isolate_id = isolate.isolate_id
                AND susceptibility.interpretation_code = 'P'
                AND susceptibility.antibiotic_code = target.marker_code
               WHERE analysis.sample_id = target.evidence_microb_sample_id
                 AND analysis.analysis_code = 'PMDR'
                 AND analysis.analysis_kind = 'BACTERIOLOGICAL'
                 AND NOT EXISTS (
                     SELECT 1 FROM ref_microb_catalog_entry AS ignored
                     WHERE ignored.ref_microb_catalog_entry_id = isolate.microorganism_id
                       AND ignored.description ~* 'Candida|Enterococcus|Stenotrophomonas|Acinetobacter'
                 )
           ) AS selected_prior_positive
    FROM organism_targets AS target
), expected AS (
    SELECT support.*,
           CASE WHEN evidence_status = 'ACCEPTED'
                THEN selected_prior_positive ELSE direct_positive END AS source_positive,
           CASE WHEN evidence_status = 'ACCEPTED'
                THEN stored_candidate_positive ELSE direct_positive END AS stored_positive
    FROM support
)
SELECT marker_code,
       COUNT(*) AS current_organism_rows,
       COUNT(*) FILTER (WHERE exported_positive = 1) AS exported_positive,
       COUNT(*) FILTER (WHERE stored_positive) AS positive_from_stored_derivation,
       COUNT(*) FILTER (WHERE source_positive) AS positive_from_selected_sources,
       COUNT(*) FILTER (
           WHERE evidence_status = 'ACCEPTED'
             AND selected_prior_positive AND NOT stored_candidate_positive
       ) AS prior_positive_missing_from_candidates,
       COUNT(*) FILTER (
           WHERE evidence_status = 'ACCEPTED'
             AND stored_candidate_positive AND NOT selected_prior_positive
       ) AS candidate_positive_missing_from_current_sources,
       COUNT(*) FILTER (
           WHERE stored_positive AND exported_positive = 0
       ) AS stored_positive_exported_as_zero,
       COUNT(*) FILTER (
           WHERE NOT stored_positive AND exported_positive = 1
       ) AS exported_positive_without_stored_support,
       COUNT(*) FILTER (
           WHERE global_positive = 1 AND NOT source_positive
       ) AS global_positive_without_this_organism_support,
       COUNT(*) FILTER (WHERE exported_rows IS NULL) AS organism_missing_from_export,
       COUNT(*) FILTER (WHERE exported_rows > 1) AS organism_with_multiple_export_rows
FROM expected
GROUP BY marker_code
ORDER BY CASE marker_code WHEN 'OXA' THEN 1 WHEN 'VIM' THEN 2
                         WHEN 'KPC' THEN 3 ELSE 4 END;

ROLLBACK;
