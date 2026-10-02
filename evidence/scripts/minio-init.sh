#!/bin/sh
# Creates bucket + non-root user for STS AssumeRole (MinIO refuses AssumeRole for root).
set -e
until mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1; do
  echo "waiting for minio..."; sleep 2
done
mc mb --ignore-existing "local/$MINIO_BUCKET"
cat > /tmp/policy.json <<POL
{ "Version": "2012-10-17",
  "Statement": [ { "Effect": "Allow", "Action": ["s3:*"],
    "Resource": ["arn:aws:s3:::$MINIO_BUCKET", "arn:aws:s3:::$MINIO_BUCKET/*"] } ] }
POL
mc admin policy create local dji-bucket-rw /tmp/policy.json || true
mc admin user add local "$MINIO_STS_USER" "$MINIO_STS_PASSWORD"
mc admin policy attach local dji-bucket-rw --user "$MINIO_STS_USER" || true

# Mapping tool: result and basemap buckets, own user (read media, read/write mapping buckets).
# CORS: MinIO CE has no bucket CORS; origins are set server-wide via MINIO_API_CORS_ALLOW_ORIGIN
# (docker-compose.yml). MinIO answers preflights for Range and exposes Content-Range.
mc mb --ignore-existing "local/$MAPPING_RESULTS_BUCKET"
mc mb --ignore-existing "local/$MAPPING_BASEMAPS_BUCKET"
cat > /tmp/mapping-policy.json <<POL
{ "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": ["s3:GetObject", "s3:ListBucket"],
      "Resource": ["arn:aws:s3:::$MINIO_BUCKET", "arn:aws:s3:::$MINIO_BUCKET/*"] },
    { "Effect": "Allow", "Action": ["s3:*"],
      "Resource": ["arn:aws:s3:::$MAPPING_RESULTS_BUCKET", "arn:aws:s3:::$MAPPING_RESULTS_BUCKET/*",
                   "arn:aws:s3:::$MAPPING_BASEMAPS_BUCKET", "arn:aws:s3:::$MAPPING_BASEMAPS_BUCKET/*"] } ] }
POL
mc admin policy create local mapping-rw /tmp/mapping-policy.json || true
mc admin user add local "$MAPPING_MINIO_USER" "$MAPPING_MINIO_PASSWORD"
mc admin policy attach local mapping-rw --user "$MAPPING_MINIO_USER" || true

# Farming Guide: own bucket for zone maps/exports, read access to the mapping results (index COGs)
mc mb --ignore-existing "local/$FARMING_BUCKET"
cat > /tmp/farming-policy.json <<POL
{ "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": ["s3:GetObject", "s3:ListBucket"],
      "Resource": ["arn:aws:s3:::$MAPPING_RESULTS_BUCKET", "arn:aws:s3:::$MAPPING_RESULTS_BUCKET/*"] },
    { "Effect": "Allow", "Action": ["s3:*"],
      "Resource": ["arn:aws:s3:::$FARMING_BUCKET", "arn:aws:s3:::$FARMING_BUCKET/*"] } ] }
POL
mc admin policy create local farming-rw /tmp/farming-policy.json || true
mc admin user add local "$FARMING_MINIO_USER" "$FARMING_MINIO_PASSWORD"
mc admin policy attach local farming-rw --user "$FARMING_MINIO_USER" || true
# Photovoltaik Tool: own bucket for inspections, read access to the Pilot 2 uploads (thermal R-JPEGs)
mc mb --ignore-existing "local/$PV_BUCKET"
cat > /tmp/pv-policy.json <<POL
{ "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": ["s3:GetObject", "s3:ListBucket"],
      "Resource": ["arn:aws:s3:::$MINIO_BUCKET", "arn:aws:s3:::$MINIO_BUCKET/*"] },
    { "Effect": "Allow", "Action": ["s3:*"],
      "Resource": ["arn:aws:s3:::$PV_BUCKET", "arn:aws:s3:::$PV_BUCKET/*"] } ] }
POL
mc admin policy create local pv-rw /tmp/pv-policy.json || true
mc admin user add local "$PV_MINIO_USER" "$PV_MINIO_PASSWORD"
mc admin policy attach local pv-rw --user "$PV_MINIO_USER" || true
echo "minio-init done"
