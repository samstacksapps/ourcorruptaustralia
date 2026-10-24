-- related_party_not_declared (medium)
-- A counterparty in the related party note matches no entity in any interest return of
-- that council. Any link, fuzzy included, counts as declared, so a loose match suppresses
-- the flag rather than raising it.
SELECT r.entity_id, r.council_id AS body_id, 'related_party_not_declared' AS flag, $weight AS score,
       to_json([r.ev]) AS evidence_json, r.amount AS money,
       to_json(struct_pack(fy := fy_label(r.fy), amount := r.amount, nature := r.nature)) AS facts
FROM related_parties_ev r
WHERE r.entity_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM interests i WHERE i.council_id = r.council_id AND i.entity_id = r.entity_id
  )
