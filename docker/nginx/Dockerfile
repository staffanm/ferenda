# andreilhicas/nginx-letsencrypt was frozen on nginx 1.15.9 / Alpine 3.9 (2019)
# and bakes in certbot 0.30.2, which is ACME-API-incompatible with current
# Let's Encrypt (it doesn't set the Content-Type header LE's boulder server
# now requires on POST-as-GET requests -- every renewal attempt fails).
# Plain, current nginx here; cert issuance/renewal moved to the `certbot`
# sidecar service (docker-compose.yml), an actively maintained official image.
# ssl-dhparams.pem/options-ssl-nginx.conf are the same certbot-generated
# security defaults the old image shipped, just no longer certbot-managed.
FROM nginx:1.27-alpine
COPY options-ssl-nginx.conf /etc/ssl-options/options-nginx-ssl.conf
COPY ssl-dhparams.pem /etc/ssl-options/ssl-dhparams.pem
COPY default.conf /etc/nginx/conf.d/default.conf
COPY ferenda.lagen.nu.conf /etc/nginx/conf.d/ferenda.lagen.nu.conf
