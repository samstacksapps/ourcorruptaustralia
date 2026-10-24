-- new_abn_payee (medium)
-- Payee matched to an ABN whose status-from date is within twelve months before the first
-- payment, and payments in the twelve months from the first total more than $20,000.
WITH first_pay AS (
    SELECT council_id, entity_id, min(paid_date) AS first_paid
    FROM payments
    WHERE entity_id IS NOT NULL
    GROUP BY council_id, entity_id
),
paid AS (
    SELECT f.council_id, f.entity_id, f.first_paid,
           sum(p.amount) AS total, count(*) AS n, list(p.ev ORDER BY p.paid_date) AS evs
    FROM first_pay f
    JOIN payments_ev p
      ON p.council_id = f.council_id AND p.entity_id = f.entity_id
     AND p.paid_date < f.first_paid + to_months($window_months)
    GROUP BY f.council_id, f.entity_id, f.first_paid
),
abn_record AS (
    SELECT abn, status_from, any_value(ev ORDER BY row_id) AS ev
    FROM abn_ev
    GROUP BY abn, status_from
)
SELECT paid.entity_id, paid.council_id AS body_id, 'new_abn_payee' AS flag, $weight AS score,
       to_json(list_concat([a.ev], paid.evs)) AS evidence_json, paid.total AS money,
       to_json(struct_pack(abn := e.abn, status_from := CAST(a.status_from AS VARCHAR),
                           first_paid := CAST(paid.first_paid AS VARCHAR),
                           days := date_diff('day', a.status_from, paid.first_paid),
                           total := paid.total, n := paid.n)) AS facts
FROM paid
JOIN entities e ON e.entity_id = paid.entity_id
JOIN abn_record a ON a.abn = e.abn
WHERE a.status_from <= paid.first_paid
  AND a.status_from > paid.first_paid - to_months($months_before)
  AND paid.total > $min_total
