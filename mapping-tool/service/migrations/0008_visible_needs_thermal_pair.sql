-- 0008: on thermal drones a non-thermal photo is "visible" only when its shot has a thermal file, else "rgb"
-- (Matrice 4T mapping flights shoot only the wide camera: 1553 photos were "visible" without a pair).
-- Same statement as app/ingest.py PAIR_SQL, which keeps it true for later uploads.
UPDATE media_ingest v
  JOIN media_meta m ON m.file_id = v.file_id
  LEFT JOIN (SELECT DISTINCT workspace_id, shot_key FROM media_ingest
              WHERE category = 'thermal' AND shot_key IS NOT NULL) t
         ON t.workspace_id = v.workspace_id AND t.shot_key = v.shot_key
   SET v.category = IF(t.shot_key IS NULL, 'rgb', 'visible')
 WHERE v.category IN ('visible', 'rgb')
   AND v.shot_key IS NOT NULL
   AND JSON_UNQUOTE(JSON_EXTRACT(m.meta, '$.model')) IN ('M30T', 'M3T', 'M3TD', 'M4T', 'M4TD')
   AND v.category <> IF(t.shot_key IS NULL, 'rgb', 'visible');
