#!/bin/sh
# Writes the Cloudflare credentials file from CLOUDFLARE_API_KEY (compose's
# env_file: .env), then renews on a loop. No --webroot flag here: certbot
# applies a command-line authenticator to every lineage on `renew`, which
# would force HTTP-01 onto the DNS-01 wildcard lineage and fail it. Each
# lineage's own authenticator, recorded at its `certonly` time, is used
# instead.
set -eu

umask 077
printf 'dns_cloudflare_api_token = %s\n' "$CLOUDFLARE_API_KEY" > /etc/letsencrypt/cloudflare.ini

trap exit TERM
while :; do
    certbot renew --quiet --deploy-hook /usr/local/bin/deploy-hook.sh
    sleep 12h &
    wait $!
done
