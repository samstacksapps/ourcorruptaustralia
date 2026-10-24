-- over_threshold_no_tender (medium)
-- A payee's total in a financial year exceeds the tender threshold and the payee won no
-- tender at that council that year or the year before. Payments whose description says
-- WALGA preferred supplier or State contract are left out of the total.
WITH spend AS (
    SELECT council_id, entity_id, fy,
           sum(amount) AS total, count(*) AS n, list(ev ORDER BY paid_date) AS evs
    FROM payments_ev
    WHERE entity_id IS NOT NULL
      AND NOT regexp_matches(coalesce(description, ''), $exempt_keywords)
    GROUP BY council_id, entity_id, fy
)
SELECT s.entity_id, s.council_id AS body_id, 'over_threshold_no_tender' AS flag, $weight AS score,
       to_json(s.evs) AS evidence_json, s.total AS money,
       to_json(struct_pack(total := s.total, n := s.n, fy := fy_label(s.fy), threshold := $tender_threshold)) AS facts
FROM spend s
WHERE s.total > $tender_threshold
  AND NOT EXISTS (
      SELECT 1 FROM tenders t
      WHERE t.council_id = s.council_id AND t.entity_id = s.entity_id AND t.is_winner
        AND fy(coalesce(t.awarded_date, t.closed_date)) BETWEEN s.fy - $lookback_years AND s.fy
  )
