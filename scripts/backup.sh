#!/bin/bash
set -e

BACKUP_DIR="${BACKUP_DIR:-/backups/humetric}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/humetric_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

pg_dump "${DATABASE_URL}" | gzip > "$BACKUP_FILE"
echo "Backup created: $BACKUP_FILE"

find "$BACKUP_DIR" -name "humetric_*.sql.gz" -mtime "+$RETENTION_DAYS" -delete
echo "Cleaned backups older than $RETENTION_DAYS days"
