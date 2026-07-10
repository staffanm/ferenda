# Unprivileged nginx for ferenda-vps: the master process runs as uid 101, not
# root -- nothing here needs root. It listens on 8080/8443; the compose file
# maps the privileged host ports 80/443 onto them.
#
# STOCK image, no modules: nginx serves the generated/ tree's precompressed `.br`
# files as-is (Content-Encoding: br) purely by streaming bytes -- it never
# compresses or decompresses, so ngx_brotli is not needed (see ferenda.conf).
#
# Cert issuance/renewal lives in the `certbot` sidecar (docker-compose.yml);
# nginx only READS the live cert + the ACME webroot, both shared volumes.
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY options-ssl-nginx.conf /etc/ssl-options/options-nginx-ssl.conf
COPY ssl-dhparams.pem /etc/ssl-options/ssl-dhparams.pem
# headers stamped on every directly-served precompressed response (see ferenda.conf)
COPY brotli-headers.conf /etc/nginx/brotli-headers.conf
# Replace the image's default vhost with ours (single site, port 8080/8443).
COPY ferenda.conf /etc/nginx/conf.d/default.conf
