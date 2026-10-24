-- cover_bidding (medium)
-- A winner of three or more tenders where the same two or more losers lost to it every
-- time. Read as: some pair of losers appears together in at least three of its wins.
WITH win_losers AS (
    SELECT w.council_id, w.entity_id AS winner, w.tender_ref, l.entity_id AS loser, l.tenderer_raw AS loser_name
    FROM tenders w
    JOIN tenders l ON l.council_id = w.council_id AND l.tender_ref = w.tender_ref AND NOT l.is_winner
    WHERE w.is_winner AND w.entity_id IS NOT NULL AND l.entity_id IS NOT NULL AND l.entity_id <> w.entity_id
),
pairs AS (
    SELECT a.council_id, a.winner, a.loser AS l1, b.loser AS l2,
           any_value(a.loser_name) AS n1, any_value(b.loser_name) AS n2,
           list(DISTINCT a.tender_ref) AS refs
    FROM win_losers a
    JOIN win_losers b
      ON b.council_id = a.council_id AND b.winner = a.winner AND b.tender_ref = a.tender_ref AND a.loser < b.loser
    GROUP BY a.council_id, a.winner, a.loser, b.loser
    HAVING count(DISTINCT a.tender_ref) >= $min_wins
),
per_winner AS (
    SELECT council_id, winner,
           list_sort(list_distinct(flatten(list(refs)))) AS refs,
           list_sort(list_distinct(flatten(list([n1, n2])))) AS losers
    FROM pairs
    GROUP BY council_id, winner
    HAVING len(list_distinct(flatten(list([l1, l2])))) >= $min_common_losers
)
SELECT w.winner AS entity_id, w.council_id AS body_id, 'cover_bidding' AS flag, $weight AS score,
       to_json(list(t.ev ORDER BY t.ev.date, t.is_winner DESC)) AS evidence_json,
       sum(CASE WHEN t.is_winner THEN t.contract_amount END) AS money,
       to_json(struct_pack(n_wins := len(any_value(w.refs)), refs := array_to_string(any_value(w.refs), ', '),
                           losers := array_to_string(any_value(w.losers), ', '))) AS facts
FROM per_winner w
JOIN tenders_ev t ON t.council_id = w.council_id AND list_contains(w.refs, t.tender_ref)
GROUP BY w.winner, w.council_id
