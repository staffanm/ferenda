#!/usr/bin/env bash
# Fetch the Apache POI jar stack (+ runtime deps) into vendor/poi/.
#
# The legacy DV Word path (accommodanda/dv/word.py) reads binary .doc (HWPF)
# and .docx (XWPF) through POI via jpype. The jars are not committed; run
# this once after checkout. Idempotent — already-present jars are skipped.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$REPO_ROOT/vendor/poi"
BASE="https://repo1.maven.org/maven2"

# groupPath:artifact:version  (groupPath uses / between segments)
JARS=(
  "org/apache/poi:poi:5.4.1"
  "org/apache/poi:poi-scratchpad:5.4.1"
  "org/apache/poi:poi-ooxml:5.4.1"
  "org/apache/poi:poi-ooxml-lite:5.4.1"
  "org/apache/commons:commons-collections4:4.4"
  "org/apache/commons:commons-math3:3.6.1"
  "org/apache/commons:commons-compress:1.27.1"
  "org/apache/commons:commons-lang3:3.17.0"
  "commons-io:commons-io:2.18.0"
  "commons-codec:commons-codec:1.17.1"
  "org/apache/logging/log4j:log4j-api:2.24.3"
  "org/apache/xmlbeans:xmlbeans:5.3.0"
  "com/zaxxer:SparseBitSet:1.3"
)

mkdir -p "$DEST"
for entry in "${JARS[@]}"; do
  IFS=":" read -r group artifact version <<< "$entry"
  jar="$artifact-$version.jar"
  if [[ -f "$DEST/$jar" ]]; then
    echo "have  $jar"
    continue
  fi
  echo "fetch $jar"
  curl -fsSL -o "$DEST/$jar" "$BASE/$group/$artifact/$version/$jar"
done

echo "POI jars in $DEST:"
ls -1 "$DEST"/*.jar | sed 's#.*/#  #'
