-- interest_matches_payee (strong)
-- An entity declared in a person's interest return is a payee, tenderer or related party
-- of that person's council. (Council grant recipients are payees; federal grants arrive
-- in phase 6.)
WITH counterpart AS (
    SELECT council_id, entity_id, payee_norm AS norm, ev, 'payee' AS kind, amount AS paid
    FROM payments_ev
    UNION ALL
    SELECT council_id, entity_id, tenderer_norm, ev,
           CASE WHEN is_winner THEN 'tender winner' ELSE 'tenderer' END, 0
    FROM tenders_ev
    UNION ALL
    SELECT council_id, entity_id, counterparty_norm, ev, 'related party', 0
    FROM related_parties_ev
),
hits AS (
    SELECT i.row_id AS interest_id, i.council_id, i.entity_id, i.ev AS i_ev, i.person, i.role,
           i.interest_type, i.entity_raw, i.return_type, i.return_date, c.ev AS c_ev, c.kind, c.paid,
           strong_basis(i.entity_norm, li.method, c.norm, lc.method) AS basis
    FROM interests_ev i
    JOIN counterpart c ON c.council_id = i.council_id AND c.entity_id = i.entity_id
    JOIN entity_links li ON li.name_norm = i.entity_norm
    JOIN entity_links lc ON lc.name_norm = c.norm
    WHERE i.entity_id IS NOT NULL
)
SELECT entity_id, council_id AS body_id, 'interest_matches_payee' AS flag, $weight AS score,
       to_json(list_concat([any_value(i_ev)], list(c_ev ORDER BY c_ev.date))) AS evidence_json,
       sum(paid) AS money,
       best_basis(list(basis)) AS match_basis,
       to_json(struct_pack(person := any_value(person), role := any_value(role),
                           interest_type := any_value(interest_type), entity := any_value(entity_raw),
                           return_type := any_value(return_type), return_date := CAST(any_value(return_date) AS VARCHAR),
                           kinds := array_to_string(list_sort(list_distinct(list(kind))), ', '),
                           paid := sum(paid))) AS facts
FROM hits
GROUP BY interest_id, entity_id, council_id
