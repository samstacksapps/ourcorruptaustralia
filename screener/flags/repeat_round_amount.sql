-- repeat_round_amount (weak)
-- Same payee, same amount to the nearest $100, four or more times in twelve months, where
-- the description does not say lease, rent, licence, insurance, loan, subscription or retainer.
WITH p AS (
    SELECT council_id, entity_id, paid_date, amount, ev,
           round(amount / $round_to) * $round_to AS rounded
    FROM payments_ev
    WHERE entity_id IS NOT NULL
      AND NOT regexp_matches(coalesce(description, ''), $exempt_keywords)
),
windows AS (
    SELECT a.council_id, a.entity_id, a.rounded, a.paid_date AS start_date,
           count(*) AS n, sum(b.amount) AS total, list(b.ev ORDER BY b.paid_date) AS evs
    FROM p a
    JOIN p b
      ON b.council_id = a.council_id AND b.entity_id = a.entity_id AND b.rounded = a.rounded
     AND b.paid_date >= a.paid_date AND b.paid_date < a.paid_date + to_months($window_months)
    GROUP BY a.council_id, a.entity_id, a.rounded, a.paid_date
    HAVING count(*) >= $min_count
),
best AS (
    SELECT *, row_number() OVER (PARTITION BY council_id, entity_id, rounded ORDER BY n DESC, start_date) AS rn
    FROM windows
)
SELECT entity_id, council_id AS body_id, 'repeat_round_amount' AS flag, $weight AS score,
       to_json(evs) AS evidence_json, total AS money,
       to_json(struct_pack(n := n, rounded := rounded, start := CAST(start_date AS VARCHAR),
                           window_months := $window_months)) AS facts
FROM best
WHERE rn = 1
