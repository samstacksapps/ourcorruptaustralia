-- post_award_overrun (medium)
-- Payments to a tender winner in the 24 months after award exceed 130 per cent of the
-- contract amount, with no later tender won by it, and no variation item naming it, in
-- that window.
WITH awards AS (
    SELECT row_id, council_id, entity_id, tender_ref, awarded_date, contract_amount, ev
    FROM tenders_ev
    WHERE is_winner AND entity_id IS NOT NULL AND contract_amount > 0 AND awarded_date IS NOT NULL
),
paid AS (
    SELECT a.row_id AS award_id, a.council_id, a.entity_id,
           any_value(a.tender_ref) AS tender_ref, any_value(a.awarded_date) AS awarded_date,
           any_value(a.contract_amount) AS contract, any_value(a.ev) AS award_ev,
           sum(p.amount) AS total, count(*) AS n, list(p.ev ORDER BY p.paid_date) AS evs
    FROM awards a
    JOIN payments_ev p
      ON p.council_id = a.council_id AND p.entity_id = a.entity_id
     AND p.paid_date > a.awarded_date AND p.paid_date <= a.awarded_date + to_months($window_months)
    GROUP BY a.row_id, a.council_id, a.entity_id
)
SELECT entity_id, council_id AS body_id, 'post_award_overrun' AS flag, $weight AS score,
       to_json(list_concat([award_ev], evs)) AS evidence_json, total AS money,
       to_json(struct_pack(tender_ref := tender_ref, awarded := CAST(awarded_date AS VARCHAR), contract := contract,
                           total := total, pct := 100 * total / contract, window_months := $window_months)) AS facts
FROM paid
WHERE total > contract * $overrun_pct
  AND NOT EXISTS (
      SELECT 1 FROM tenders t
      WHERE t.council_id = paid.council_id AND t.entity_id = paid.entity_id AND t.is_winner
        AND t.awarded_date > paid.awarded_date
        AND t.awarded_date <= paid.awarded_date + to_months($window_months)
  )
  AND NOT EXISTS (
      SELECT 1 FROM items it
      WHERE it.council_id = paid.council_id AND list_contains(it.entity_ids, paid.entity_id)
        AND it.meeting_date > paid.awarded_date
        AND it.meeting_date <= paid.awarded_date + to_months($window_months)
        AND regexp_matches(it.item_title, $variation_keywords)
  )
