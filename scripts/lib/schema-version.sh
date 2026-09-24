#!/bin/bash
# The database schema version a checkout's migrations reach.
#
# One place, read by bundle.sh when it stamps TBDatabaseSchemaVersion and by
# anyone who needs to compare a checkout to the database on disk. The number
# is the highest `v<N>_…` migration registered in QueueStore.swift, which is
# exactly what `grdb_migrations` will contain after that source has run.
#
# Sourced, not executed: `. scripts/lib/schema-version.sh` then call the
# function. `TB_SOURCE_ROOT` overrides the checkout, for tests.

tb_source_schema_version() {
  local root="${TB_SOURCE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
  local file="$root/Sources/TranquilityCore/QueueStore.swift"
  local n
  n=$(grep -oE 'registerMigration\("v[0-9]+' "$file" 2>/dev/null \
      | grep -oE '[0-9]+$' | sort -n | tail -1 || true)
  # A tree without the file (a tarball of something else) keeps the old
  # constant rather than stamping nothing.
  printf '%s\n' "${n:-18}"
}
