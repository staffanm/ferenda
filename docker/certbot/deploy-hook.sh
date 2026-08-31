#!/bin/sh
# Runs once per renewed lineage. lagen.nu keeps its existing target directory
# (nginx's ssl_certificate paths for lagen.nu/ferenda.lagen.nu do not change);
# every other lineage -- the wildcard cert -- goes into certificates/wildcard/.
set -eu

name=$(basename "$RENEWED_LINEAGE")
dest=/usr/share/nginx/certificates
[ "$name" = lagen.nu ] || dest="$dest/wildcard"

mkdir -p "$dest"
cp "$RENEWED_LINEAGE/fullchain.pem" "$RENEWED_LINEAGE/privkey.pem" "$dest/"
