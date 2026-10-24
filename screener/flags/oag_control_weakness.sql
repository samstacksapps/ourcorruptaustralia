-- oag_control_weakness (context, multiplies the council's other flags)
-- The Auditor General named the council for purchasing, payment or payroll control
-- weaknesses in the last two reports.
WITH recent AS (
    SELECT report_year FROM (
        SELECT report_year, dense_rank() OVER (ORDER BY report_year DESC) AS rk
        FROM (SELECT DISTINCT report_year FROM audit_findings)
    ) WHERE rk <= $last_n_reports
)
SELECT CAST(NULL AS VARCHAR) AS entity_id, f.council_id AS body_id, 'oag_control_weakness' AS flag,
       $weight AS score, to_json(list(f.ev ORDER BY f.report_year)) AS evidence_json,
       CAST(NULL AS DOUBLE) AS money,
       to_json(struct_pack(areas := lower(array_to_string(list_sort(list_distinct(list(f.area))), ', ')),
                           reports := array_to_string(list_sort(list_distinct(list(f.report_title))), '; '))) AS facts
FROM audit_findings_ev f
WHERE f.report_year IN (SELECT report_year FROM recent)
  AND regexp_matches(f.area || ' ' || f.finding, $area_keywords)
GROUP BY f.council_id
