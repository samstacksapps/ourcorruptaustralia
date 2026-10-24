-- just_under_limit (medium)
-- Three or more payments to one payee within twelve months, each between 90 and 100 per
-- cent of an approval limit in the delegations register, or of the tender threshold.
WITH limits AS (
    SELECT council_id, role AS limit_name, limit_amount FROM delegations
    UNION ALL
    SELECT council_id, 'tender threshold', $tender_threshold FROM councils
),
near AS (
    SELECT p.council_id, p.entity_id, p.paid_date, p.amount, p.ev, l.limit_name, l.limit_amount
    FROM payments_ev p
    JOIN limits l ON l.council_id = p.council_id
    WHERE p.entity_id IS NOT NULL
      AND p.amount >= l.limit_amount * $lower_pct
      AND p.amount <= l.limit_amount * $upper_pct
),
windows AS (
    SELECT a.council_id, a.entity_id, a.limit_name, a.limit_amount, a.paid_date AS start_date,
           count(*) AS n, sum(b.amount) AS total, list(b.ev ORDER BY b.paid_date) AS evs
    FROM near a
    JOIN near b
      ON b.council_id = a.council_id AND b.entity_id = a.entity_id AND b.limit_name = a.limit_name
     AND b.paid_date >= a.paid_date AND b.paid_date < a.paid_date + to_months($window_months)
    GROUP BY a.council_id, a.entity_id, a.limit_name, a.limit_amount, a.paid_date
    HAVING count(*) >= $min_count
),
best AS (
    SELECT *, row_number() OVER (PARTITION BY council_id, entity_id, limit_name ORDER BY n DESC, start_date) AS rn
    FROM windows
)
SELECT entity_id, council_id AS body_id, 'just_under_limit' AS flag, $weight AS score,
       to_json(evs) AS evidence_json, total AS money,
       to_json(struct_pack(n := n, limit_name := limit_name, limit_amount := limit_amount,
                           start := CAST(start_date AS VARCHAR), window_months := $window_months)) AS facts
FROM best
WHERE rn = 1
