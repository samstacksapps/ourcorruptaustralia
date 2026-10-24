-- unmatched_payee (weak)
-- A payee with more than $20,000 in a year that matches no ABN and no tenderer.
WITH spend AS (
    SELECT council_id, entity_id, fy,
           sum(amount) AS total, count(*) AS n, list(ev ORDER BY paid_date) AS evs
    FROM payments_ev
    WHERE entity_id IS NOT NULL
    GROUP BY council_id, entity_id, fy
)
SELECT s.entity_id, s.council_id AS body_id, 'unmatched_payee' AS flag, $weight AS score,
       to_json(s.evs) AS evidence_json, s.total AS money,
       to_json(struct_pack(total := s.total, n := s.n, fy := fy_label(s.fy))) AS facts
FROM spend s
JOIN entities e ON e.entity_id = s.entity_id
WHERE s.total > $min_total
  AND e.abn IS NULL
  AND NOT EXISTS (SELECT 1 FROM tenders t WHERE t.entity_id = s.entity_id)
