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
echo "minio-init done"
