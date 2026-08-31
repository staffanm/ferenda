#!/bin/sh
# Writes the Cloudflare credentials file from LAGEN_CLOUDFLARE_TOKEN (renamed
# from .env's CLOUDFLARE_API_KEY in docker-compose.yml -- that exact name is
# auto-detected by the `cloudflare` Python client as a legacy Global-API-Key
# credential and collides with the token here), then renews on a loop. No
# --webroot flag here: certbot applies a command-line authenticator to every
# lineage on `renew`, which would force HTTP-01 onto the DNS-01 wildcard
# lineage and fail it. Each lineage's own authenticator, recorded at its
# `certonly` time, is used instead.
set -eu

umask 077
printf 'dns_cloudflare_api_token = %s\n' "$LAGEN_CLOUDFLARE_TOKEN" > /etc/letsencrypt/cloudflare.ini

trap exit TERM
while :; do
    certbot renew --quiet --deploy-hook /usr/local/bin/deploy-hook.sh
    sleep 12h &
    wait $!
done
