-- 0007: a _V photo is "visible" (frame of a thermal shot) only on thermal drones; Mavic 3E & co. take plain
-- RGB photos with that suffix (app/ingest.py refine_category). Flight set kinds stay valid: a set of only
-- visible/rgb photos is "rgb" either way.
UPDATE media_ingest i
  JOIN media_meta m ON m.file_id = i.file_id
   SET i.category = 'rgb'
 WHERE i.category = 'visible'
   AND JSON_UNQUOTE(JSON_EXTRACT(m.meta, '$.model')) IS NOT NULL
   AND JSON_UNQUOTE(JSON_EXTRACT(m.meta, '$.model')) NOT IN ('M3T', 'M3TD', 'M4T', 'M4TD', 'M30T');
