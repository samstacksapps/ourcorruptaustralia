-- repeat_winner (weak)
-- A tenderer wins 40 per cent or more of a council's tenders in a financial year with at
-- least four tenders.
WITH tender_list AS (
    SELECT DISTINCT council_id, tender_ref, fy(coalesce(awarded_date, closed_date)) AS fy
    FROM tenders
),
totals AS (
    SELECT council_id, fy, count(*) AS n FROM tender_list GROUP BY council_id, fy HAVING count(*) >= $min_tenders
),
wins AS (
    SELECT t.council_id, fy(coalesce(t.awarded_date, t.closed_date)) AS fy, t.entity_id,
           count(DISTINCT t.tender_ref) AS wins, sum(t.contract_amount) AS value, list(t.ev ORDER BY t.ev.date) AS evs
    FROM tenders_ev t
    WHERE t.is_winner AND t.entity_id IS NOT NULL
    GROUP BY t.council_id, fy(coalesce(t.awarded_date, t.closed_date)), t.entity_id
)
SELECT w.entity_id, w.council_id AS body_id, 'repeat_winner' AS flag, $weight AS score,
       to_json(w.evs) AS evidence_json, w.value AS money,
       to_json(struct_pack(wins := w.wins, n := tot.n, fy := fy_label(w.fy), share := w.wins / tot.n)) AS facts
FROM wins w
JOIN totals tot ON tot.council_id = w.council_id AND tot.fy = w.fy
WHERE w.wins / tot.n >= $min_share
