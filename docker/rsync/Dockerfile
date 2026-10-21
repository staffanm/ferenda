# Read-only rsync daemon exposing /data/artifact and /data/downloaded on TCP 873
# to authenticated reusers (individual username:password pairs in
# /etc/rsyncd.secrets). Runs unprivileged (nobody, 65534:65534) on port 1873
# inside the container so it needs no capabilities (`cap_drop: [ALL]`);
# docker-compose.yml publishes host port 873 -> 1873.
# Pinned by digest with the tag beside it, like the ferenda and nginx images.
FROM alpine:3.21@sha256:3c81aa9a3d770b316568f4499e30461a5cd3fbd7180bd89e28e34894c7845832
RUN apk add --no-cache rsync
COPY --chmod=0755 entrypoint.sh /usr/local/bin/entrypoint.sh
USER 65534:65534
EXPOSE 1873
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
