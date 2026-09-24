#!/bin/bash
# The schema stamp is read from the migrations, not from a constant.
#
# 23 Sep: every bundle said TBDatabaseSchemaVersion 18 while the source
# migrated to v22, so switch-app.sh compared 18 to the 22 on disk and refused
# to switch in either direction. This proves the stamp follows the source.
set -euo pipefail
cd "$(dirname "$0")/.."

. scripts/lib/schema-version.sh

# This checkout: the highest registered migration, and never the old constant
# unless the source genuinely has no migration past it.
HIGHEST=$(grep -oE 'registerMigration\("v[0-9]+' Sources/TranquilityCore/QueueStore.swift \
  | grep -oE '[0-9]+$' | sort -n | tail -1)
GOT=$(tb_source_schema_version)
[ "$GOT" = "$HIGHEST" ] || { echo "✗ schema version read $GOT, source registers v$HIGHEST" >&2; exit 1; }
[ "$GOT" -ge 22 ] || { echo "✗ schema version $GOT is below the v22 this test was written against" >&2; exit 1; }

# A fixture tree: the function reads whatever migrations are there.
FIXTURE=$(mktemp -d "${TMPDIR:-/tmp}/tb-schema-version.XXXXXX")
trap 'rm -rf "$FIXTURE"' EXIT INT TERM
mkdir -p "$FIXTURE/Sources/TranquilityCore"
cat > "$FIXTURE/Sources/TranquilityCore/QueueStore.swift" <<'SWIFT'
        m.registerMigration("v9_old") { db in }
        m.registerMigration("v31_newest") { db in }
        m.registerMigration("v30_second") { db in }
SWIFT
GOT=$(TB_SOURCE_ROOT="$FIXTURE" tb_source_schema_version)
[ "$GOT" = "31" ] || { echo "✗ fixture read $GOT, wanted 31 (numeric, not lexical)" >&2; exit 1; }

# No source at all keeps the old constant rather than stamping nothing.
GOT=$(TB_SOURCE_ROOT="$FIXTURE/absent" tb_source_schema_version)
[ "$GOT" = "18" ] || { echo "✗ missing source read $GOT, wanted the 18 fallback" >&2; exit 1; }

# And bundle.sh (upstream #623) stamps the same number: run its own expression
# against this checkout, without building anything.
STAMP=$(sed -nE 's/.*registerMigration\("v([0-9]+)_.*/\1/p' Sources/TranquilityCore/QueueStore.swift | sort -n | tail -1)
grep -q 'registerMigration' scripts/bundle.sh \
  || { echo "✗ bundle.sh no longer stamps the schema from the migrations" >&2; exit 1; }
[ "$STAMP" = "$HIGHEST" ] || { echo "✗ bundle.sh would stamp $STAMP, the source registers v$HIGHEST" >&2; exit 1; }

echo "✓ schema stamp follows the source (v$HIGHEST)"
