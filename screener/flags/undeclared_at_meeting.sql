-- undeclared_at_meeting (strong)
-- An agenda item mentions an entity that matches a councillor's declared interest, and no
-- disclosure by that councillor is recorded for that item. People are matched across the
-- minutes and the returns by surname, so a near-miss suppresses the flag rather than raising it.
WITH mentions AS (
    SELECT row_id AS item_id, council_id, meeting_date, item_ref, item_title, ev AS item_ev,
           unnest(entities_norm) AS norm, unnest(entity_ids) AS entity_id
    FROM items_ev
),
hits AS (
    SELECT m.item_id, m.council_id, m.entity_id, m.meeting_date, m.item_ref, m.item_title, m.item_ev,
           i.person, i.person_key, i.interest_type, i.entity_raw, i.ev AS i_ev,
           strong_basis(m.norm, lm.method, i.entity_norm, li.method) AS basis
    FROM mentions m
    JOIN interests_ev i
      ON i.council_id = m.council_id AND i.entity_id = m.entity_id
     AND regexp_matches(i.role, $councillor_roles)
     AND i.return_date <= m.meeting_date
     AND i.return_date > m.meeting_date - to_months($return_valid_months)
    JOIN entity_links lm ON lm.name_norm = m.norm
    JOIN entity_links li ON li.name_norm = i.entity_norm
    WHERE m.entity_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM disclosures d
          WHERE d.council_id = m.council_id AND d.meeting_date = m.meeting_date
            AND d.item_ref = m.item_ref AND d.person_key = i.person_key
      )
)
SELECT entity_id, council_id AS body_id, 'undeclared_at_meeting' AS flag, $weight AS score,
       to_json(list_concat([any_value(item_ev)], list(i_ev ORDER BY i_ev.date))) AS evidence_json,
       CAST(NULL AS DOUBLE) AS money,
       best_basis(list(basis)) AS match_basis,
       to_json(struct_pack(item_ref := any_value(item_ref), meeting_date := CAST(any_value(meeting_date) AS VARCHAR),
                           entity := any_value(entity_raw), person := any_value(person),
                           interest_type := any_value(interest_type))) AS facts
FROM hits
GROUP BY item_id, person_key, entity_id, council_id
