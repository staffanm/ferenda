#!/usr/bin/env bash
# Fetch the Apache POI jar stack (+ runtime deps) into vendor/poi/.
#
# The DV Word input path (`ferenda/lib/poi.py`) reads binary .doc (HWPF)
# and .docx (XWPF) through POI via jpype. The jars are not committed; run
# this once after checkout. Idempotent — already-present jars are skipped.
#
# Every jar is pinned by SHA-256, checked before it is kept and again on a
# copy already on disk. The version alone is not a pin: the Docker build runs
# this script, so a bad answer from the repository, a cache poisoned in
# between, or a truncated download would otherwise go straight into the image.
# The digests were taken from repo1.maven.org and cross-checked against the
# .sha1 each artifact publishes beside it (2026-09-05).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST="$REPO_ROOT/vendor/poi"
BASE="https://repo1.maven.org/maven2"

# groupPath:artifact:version:sha256  (groupPath uses / between segments)
JARS=(
  "org/apache/poi:poi:5.4.1:da5abf42da4604c5a7bca38956af6e9d6f196d9b6d4cb7eabee4f480b580d505"
  "org/apache/poi:poi-scratchpad:5.4.1:6497ba15c1cba7062aa71661a8d776d321b1f998bb2bfa19b57d7e35606381f1"
  "org/apache/poi:poi-ooxml:5.4.1:fd200c9e6f74d704160a97e9d52041995ed87439454530001edd920688f19f53"
  "org/apache/poi:poi-ooxml-lite:5.4.1:dc590461efdfcd4f27e2a892737979ab5e30b4132a7adfc7c9e56447b71a45b0"
  "org/apache/commons:commons-collections4:4.4:1df8b9430b5c8ed143d7815e403e33ef5371b2400aadbe9bda0883762e0846d1"
  "org/apache/commons:commons-math3:3.6.1:1e56d7b058d28b65abd256b8458e3885b674c1d588fa43cd7d1cbb9c7ef2b308"
  "org/apache/commons:commons-compress:1.27.1:293d80f54b536b74095dcd7ea3cf0a29bbfc3402519281332495f4420d370d16"
  "org/apache/commons:commons-lang3:3.17.0:6ee731df5c8e5a2976a1ca023b6bb320ea8d3539fbe64c8a1d5cb765127c33b4"
  "commons-io:commons-io:2.18.0:f3ca0f8d63c40e23a56d54101c60d5edee136b42d84bfb85bc7963093109cf8b"
  "commons-codec:commons-codec:1.17.1:f9f6cb103f2ddc3c99a9d80ada2ae7bf0685111fd6bffccb72033d1da4e6ff23"
  "org/apache/logging/log4j:log4j-api:2.24.3:5b4a0a0cd0e751ded431c162442bdbdd53328d1f8bb2bae5fc1bbeee0f66d80f"
  "org/apache/xmlbeans:xmlbeans:5.3.0:6cc69da3b4d35b83c5e477cd4daba204e44109833e34af2b9a8a2c8788289917"
  "com/zaxxer:SparseBitSet:1.3:f76b85adb0c00721ae267b7cfde4da7f71d3121cc2160c9fc00c0c89f8c53c8a"
)

verify() {  # path expected-sha256 -> 0 when it matches
  [[ -f "$1" ]] && [[ "$(sha256sum "$1" | cut -d' ' -f1)" == "$2" ]]
}

for entry in "${JARS[@]}"; do
  IFS=":" read -r group artifact version sha <<< "$entry"
  jar="$artifact-$version.jar"
  if verify "$DEST/$jar" "$sha"; then
    echo "have  $jar"
    continue
  fi
  if [[ -f "$DEST/$jar" ]]; then
    echo "re-fetch $jar (on disk, wrong digest)" >&2
  else
    echo "fetch $jar"
  fi
  # to a temp name, so a jar that fails the check never becomes the real one
  tmp="$DEST/.$jar.part"
  mkdir -p "$DEST"
  curl -fsSL -o "$tmp" "$BASE/$group/$artifact/$version/$jar"
  if ! verify "$tmp" "$sha"; then
    echo "$jar: sha256 is $(sha256sum "$tmp" | cut -d' ' -f1), expected $sha" >&2
    rm -f "$tmp"
    exit 1
  fi
  mv "$tmp" "$DEST/$jar"
done

echo "POI jars in $DEST:"
ls -1 "$DEST"/*.jar | sed 's#.*/#  #'
