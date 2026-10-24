-- gift_from_payee (strong)
-- A gift giver is a payee or tenderer of the same council within twelve months either
-- side of the gift.
WITH counterpart AS (
    SELECT council_id, entity_id, paid_date AS d, payee_norm AS norm, ev, 'payee' AS kind
    FROM payments_ev
    UNION ALL
    SELECT council_id, entity_id, coalesce(awarded_date, closed_date), tenderer_norm, ev,
           CASE WHEN is_winner THEN 'tender winner' ELSE 'tenderer' END
    FROM tenders_ev
),
hits AS (
    SELECT g.row_id AS gift_id, g.council_id, g.entity_id, g.ev AS gift_ev, g.giver_raw, g.value,
           g.received_date, g.recipient_name, g.recipient_role, c.ev AS c_ev, c.kind, c.d,
           strong_basis(g.giver_norm, lg.method, c.norm, lc.method) AS basis
    FROM gifts_ev g
    JOIN counterpart c
      ON c.council_id = g.council_id AND c.entity_id = g.entity_id
     AND c.d BETWEEN g.received_date - to_months($window_months) AND g.received_date + to_months($window_months)
    JOIN entity_links lg ON lg.name_norm = g.giver_norm
    JOIN entity_links lc ON lc.name_norm = c.norm
    WHERE g.entity_id IS NOT NULL
)
SELECT entity_id, council_id AS body_id, 'gift_from_payee' AS flag, $weight AS score,
       to_json(list_concat([any_value(gift_ev)], list(c_ev ORDER BY d))) AS evidence_json,
       any_value(value) AS money,
       best_basis(list(basis)) AS match_basis,
       to_json(struct_pack(recipient := any_value(recipient_name), role := any_value(recipient_role),
                           value := any_value(value), received := CAST(any_value(received_date) AS VARCHAR),
                           giver := any_value(giver_raw),
                           kinds := array_to_string(list_sort(list_distinct(list(kind))), ' and '))) AS facts
FROM hits
GROUP BY gift_id, entity_id, council_id
