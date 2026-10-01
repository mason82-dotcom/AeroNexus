-- onprem: device dictionary rows for Matrice 4 series + DJI RC Plus 2 Enterprise
-- Runs after 01_cloud_sample.sql (docker-entrypoint-initdb.d, alphabetical order, first start only)
USE `cloud_sample`;

INSERT INTO `manage_device_dictionary` (`id`, `domain`, `device_type`, `sub_type`, `device_name`, `device_desc`)
VALUES
  (32, 0, 99,  0, 'Matrice 4E',        NULL),
  (33, 0, 99,  1, 'Matrice 4T',        NULL),
  (34, 1, 88,  0, 'Matrice 4E Camera', NULL),
  (35, 1, 89,  0, 'Matrice 4T Camera', NULL),
  (36, 2, 174, 0, 'DJI RC Plus 2',     'Remote control for Matrice 4 series');

UPDATE `manage_device_dictionary` SET `device_name` = 'DJI RC Pro Enterprise'
 WHERE `domain` = 2 AND `device_type` = 144;
