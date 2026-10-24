-- campaign_funder_wins (medium)
-- A candidate gift giver wins a tender, receives a grant, or has a planning matter decided
-- at that council within two years of the election.
WITH outcomes AS (
    SELECT council_id, entity_id, coalesce(awarded_date, closed_date) AS d, ev, 'won a tender' AS kind
    FROM tenders_ev WHERE is_winner
    UNION ALL
    SELECT council_id, entity_id, paid_date, ev, 'received a grant'
    FROM payments_ev WHERE regexp_matches(coalesce(description, ''), $grant_keywords)
    UNION ALL
    SELECT council_id, entity_id, d, ev, 'had a planning matter decided'
    FROM (SELECT council_id, unnest(entity_ids) AS entity_id, meeting_date AS d, ev, item_title FROM items_ev)
    WHERE regexp_matches(item_title, $planning_keywords)
),
hits AS (
    SELECT g.row_id AS gift_id, g.council_id, g.entity_id, g.ev AS g_ev, g.value, g.candidate, g.election_date,
           o.ev AS o_ev, o.kind, o.d
    FROM candidate_gifts_ev g
    JOIN outcomes o
      ON o.council_id = g.council_id AND o.entity_id = g.entity_id
     AND o.d >= g.election_date AND o.d <= g.election_date + to_months($window_months)
    WHERE g.entity_id IS NOT NULL
)
SELECT entity_id, council_id AS body_id, 'campaign_funder_wins' AS flag, $weight AS score,
       to_json(list_concat([any_value(g_ev)], list(o_ev ORDER BY d))) AS evidence_json,
       sum(o_ev.amount) AS money,
       to_json(struct_pack(value := any_value(value), candidate := any_value(candidate),
                           election := CAST(any_value(election_date) AS VARCHAR),
                           outcomes := array_to_string(list_sort(list_distinct(list(kind))), ', '))) AS facts
FROM hits
GROUP BY gift_id, entity_id, council_id
