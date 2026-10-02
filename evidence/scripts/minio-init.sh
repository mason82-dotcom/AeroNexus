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
echo "minio-init done"
