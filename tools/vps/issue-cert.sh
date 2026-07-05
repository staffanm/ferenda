#!/usr/bin/env bash
# One-off initial Let's Encrypt issuance for ferenda.lagen.nu, done with the
# --standalone challenge BEFORE nginx runs (nginx won't start without the cert,
# and can't answer the http-01 challenge without the cert -- so certbot binds
# :80 itself this once). After this succeeds, `docker compose --profile prod up`
# starts nginx with the cert present, and the certbot sidecar handles all future
# renewals over the webroot. See docs/deploy-vps.md.
#
# Idempotent: certbot no-ops if a valid cert already exists (drop --force unless
# you mean to reissue).
set -euo pipefail

DOMAIN=ferenda.lagen.nu
EMAIL=staffan.malmgren@gmail.com

cd /srv/ferenda/ferenda

# Nothing may hold :80 during standalone issuance. `stop` is idempotent (a
# not-yet-created nginx service is a no-op), so no guard is needed -- and we
# deliberately do NOT swallow errors here: a failing docker daemon must surface
# now, before certbot tries to bind :80.
docker compose --profile prod stop nginx

docker compose run --rm -p 80:80 --entrypoint certbot certbot \
  certonly --standalone \
  -d "$DOMAIN" \
  --email "$EMAIL" --agree-tos --no-eff-email \
  --non-interactive

echo "Cert issued into the letsencrypt volume. Now: docker compose --profile prod up -d"
