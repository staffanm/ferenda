# Deploying accommodanda to ferenda-vps

`ferenda-vps` (Hetzner, `ferenda.lagen.nu` / 46.62.219.41) runs the **standalone
rebuilt site** — accommodanda only, none of the legacy lagen.nu stack. Three
containers plus a cert sidecar, all defined in the repo-root `docker-compose.yml`
under the `prod` profile:

| Service        | Image                              | User    | Role |
|----------------|------------------------------------|---------|------|
| `opensearch`   | opensearchproject/opensearch:2.9.0 | uid 1000| full-text index (loopback-only :9200) |
| `accommodanda` | built from `docker/accommodanda/`  | uid 1000| uvicorn serve + the in-container `lagen` CLI |
| `nginx`        | built from `docker/vps/nginx/`     | uid 101 | public front, 80→8080 / 443→8443, proxies `accommodanda:8000` |
| `certbot`      | certbot/certbot:v3.1.0             | root¹   | Let's Encrypt renewal for ferenda.lagen.nu |

¹ certbot runs as root inside its own container (it must write `/etc/letsencrypt`);
everything else on the box — the daemon aside — is unprivileged.

## Disk layout

- **Fixed 80 GB disk** (`/`): the deploy checkout and the corpus minus downloads.
  - `/srv/ferenda/ferenda`     — the git checkout (compose project root)
  - `/srv/ferenda/lagen-wiki`  — the git-backed wiki content repo (`WIKI_ROOT`)
  - `/srv/ferenda/site/data`   — the corpus `data_root` (artifacts, catalog, generated, dumps)
  - `/srv/ferenda/config.yml`, `/srv/ferenda/.env` — secrets, bind-mounted (never in git or the image)
  - `/srv/ferenda/logs`        — nightly-cron logs
- **Mounted 100 GB volume** (`/mnt/HC_Volume_106236756`): the bulky `downloaded/`
  tree. `/srv/ferenda/site/data/downloaded` is a **symlink** to
  `/mnt/HC_Volume_106236756/downloaded`; the compose file bind-mounts that same
  absolute path into the container so the symlink resolves there too.

Everything under `/srv/ferenda` and the `downloaded` tree is owned by the
unprivileged **`ferenda`** user (uid/gid 1000), which is in the `docker` group.

## One-time host bootstrap (as root)

Already done on the current box, recorded here for a rebuild:

```sh
# docker engine + compose plugin
curl -fsSL https://get.docker.com | sh
# unprivileged deploy user, uid/gid pinned to 1000 (matches the image's USER)
groupadd -g 1000 ferenda
useradd -m -u 1000 -g 1000 -s /bin/bash ferenda
usermod -aG docker ferenda
# opensearch needs a high mmap count, persistently
echo 'vm.max_map_count=262144' > /etc/sysctl.d/99-opensearch.conf
sysctl --system
mkdir -p /srv/ferenda && chown ferenda:ferenda /srv/ferenda
# the mounted volume's download tree, owned by the deploy user
mkdir -p /mnt/HC_Volume_106236756/downloaded
chown -R ferenda:ferenda /mnt/HC_Volume_106236756/downloaded
```

## Corpus bootstrap (rsync from dev)

The box is seeded by rsync from dev rather than a from-scratch relate/generate
(see the `vps-deploy-bootstrap-by-rsync` note). Split across the two disks:

```sh
# everything except downloaded/ -> fixed disk
rsync -aH --partial --exclude='/downloaded' \
  site/data/ ferenda-vps:/srv/ferenda/site/data/
# downloaded/ -> mounted volume
rsync -aH --partial \
  site/data/downloaded/ ferenda-vps:/mnt/HC_Volume_106236756/downloaded/
```

(The current bootstrap rsync'd into `/root/ferenda/site` first; it was then
`mv`'d to `/srv/ferenda/site` — a same-filesystem rename — and chowned to
`ferenda`.) After seeding, ensure ownership:

```sh
chown -R ferenda:ferenda /srv/ferenda/site
```

## Checkout, secrets, first build (as `ferenda`)

```sh
sudo -iu ferenda
cd /srv/ferenda
git clone https://github.com/staffanm/ferenda.git
git clone https://github.com/staffanm/lagen-wiki.git
# secrets from dev -- do NOT commit these; scp them in:
#   scp config.yml ferenda-vps:/srv/ferenda/config.yml
#   scp .env       ferenda-vps:/srv/ferenda/.env
# config.yml must NOT set a `data_root` key (the container's data_root is the
# bind-mounted /app/site/data); leave it unset/commented.
cd /srv/ferenda/ferenda
docker compose build accommodanda
```

## First cert, then bring the stack up

nginx won't start without the cert, so issue it standalone first:

```sh
cd /srv/ferenda/ferenda
./tools/vps/issue-cert.sh                 # certbot --standalone on :80, one-off
docker compose --profile prod up -d       # opensearch + app + nginx + certbot
```

Verify:

```sh
docker compose --profile prod ps
curl -fsS https://ferenda.lagen.nu/ -o /dev/null -w '%{http_code}\n'
```

## Continuous deploy (GitHub Actions, self-hosted runner)

Pushes to `modernization` trigger `.github/workflows/deploy.yml`, which runs on a
**self-hosted runner installed on the VPS**. It fast-forwards
`/srv/ferenda/ferenda`, rebuilds the image, `up -d`s, and runs
`lagen all rebuild --ignore-code-changes`.

The `--ignore-code-changes` is deliberate: a code push must **not** kick off a
reparse/regenerate on the prod host. The small box can't re-parse the ~100K
förarbeten within a deploy's time budget — a from-scratch reparse ran past
GitHub Actions' 6-hour job ceiling and was cancelled mid-`forarbete parse`. So
the deploy only folds in new *input data* incrementally and redeploys the image;
a code-driven rebuild is instead run on the fast dev box and pushed up (see
[Rebuild on dev, publish to prod](#rebuild-on-dev-publish-to-prod)).

Register the runner once (needs a token from GitHub — there's no `gh` on dev, so
do this from the repo's web UI: **Settings → Actions → Runners → New
self-hosted runner → Linux**, which shows the current download URL and a
one-time `--token`). As `ferenda`:

```sh
sudo -iu ferenda
mkdir -p /srv/ferenda/actions-runner && cd /srv/ferenda/actions-runner
# paste the two commands the runner page shows (curl the tarball, tar xz), then:
./config.sh --url https://github.com/staffanm/ferenda \
            --token <RUNNER_TOKEN> \
            --labels ferenda-vps \
            --name ferenda-vps --unattended
# install as a service so it survives reboots (needs root once):
sudo ./svc.sh install ferenda
sudo ./svc.sh start
```

The workflow keys on the `ferenda-vps` label (`runs-on: [self-hosted,
ferenda-vps]`). No repo secrets are needed — the runner is already on the box
with docker access and the checkout.

## Rebuild on dev, publish to prod

Because the deploy runs with `--ignore-code-changes` (above), a parser/render
code change reaches prod not through the deploy but through a rebuild on the
fast dev box, synced up:

```sh
tools/vps/download-data.sh     # pull the live corpus down to this checkout
lagen all rebuild              # reparse/regenerate with the new code (fast on dev)
sync-data                      # push the rebuilt corpus back up to prod
```

`download-data.sh` is the inverse of the dev→prod push, over the same two-disk
split as the [corpus bootstrap](#corpus-bootstrap-rsync-from-dev): artifact tree
+ `catalog.sqlite` + `generated/` from the fixed disk, `downloaded/` from the
mounted volume. It only adds/updates files locally (no `--delete`), and forwards
extra rsync flags — `tools/vps/download-data.sh --dry-run` previews the pull.

`sync-data` (the dev→prod push) is currently an untracked script in `~/.bin` on
the main dev box, not yet in this repo.

## Nightly full sync (cron)

`lagen all all` (download every source, then incremental rebuild) runs nightly
from the `ferenda` crontab:

```sh
sudo -iu ferenda
crontab -e
# add:
0 3 * * *  /srv/ferenda/ferenda/tools/vps/nightly.sh
# pick up renewed certs weekly (harmless if nothing changed):
30 4 * * 0 cd /srv/ferenda/ferenda && docker compose exec -T nginx nginx -s reload
```

Logs land in `/srv/ferenda/logs/nightly-YYYYMMDD.log`.

## Manual maintenance

```sh
cd /srv/ferenda/ferenda
docker compose exec accommodanda lagen all rebuild   # offline rebuild
docker compose exec accommodanda lagen all all       # download + rebuild
docker compose --profile prod logs -f accommodanda   # tail the app
docker compose --profile prod restart accommodanda   # restart serve
```

## Compression (artifact/ + generated/)

The `artifact/` and `generated/` trees are stored **precompressed with Brotli**
(`accommodanda/lib/compress`): a parsed artifact is written as `2018:585.json.br`
and a rendered page as `2018:585.html.br` — a single `.br` variant, no plain file
and no gzip companion (disk is the constraint on this box). On the text-heavy
JSON/HTML payload Brotli q11 lands around a third the size of gzip and
*decompresses faster*; the slow max-quality encode is paid once per build. The
whole scheme is transparent — every reader/writer goes through `lib/compress`,
which resolves a logical path (`…json`/`…html`) to whatever variant is on disk —
so nothing downstream (relate, index, dump, the API) knows or cares. Tiny files
(< 512 B: `robots.txt`, empty skip placeholders) stay plain. Toggle with the
`compress` config key / `FERENDA_COMPRESS` env var; tune the effort with
`compress_quality` (default 11).

The `.br` bytes are what a browser wants (`Content-Encoding: br`), so **nginx
serves them directly, as-is, with no recompression** — the deployed setup: the
`generated/` tree is bind-mounted read-only into the nginx container (`/srv/generated`),
and `ferenda.conf` streams each `<page>.html.br` with `Content-Encoding: br` to
brotli-accepting clients. This needs **no `ngx_brotli` module** — nginx never
compresses or decompresses, it just picks the `.br`, gates on `Accept-Encoding`,
and stamps the encoding + content type — so it runs on the **stock
`nginx-unprivileged` image**.

Anything without a matching `.br` on disk falls through to `accommodanda:8000`:
the transformed-namespace URLs (`/dom`, `/celex`, `/prop`, `/<fs>`, `/avg`) whose
flattened on-disk name differs from the URL, the `/sfs` browse indexes, the REST
API + editor, and any client that does not accept brotli (the app decompresses
identity bytes for it). A missing `generated/` mount therefore degrades to
proxy-everything — nothing 404s that the app could have served.

## Notes / caveats

- **LEGACY_ROOT is not mounted.** The frozen legacy corpora (the 410 GB soukb
  tree et al.) live on dev; the one-time `import-legacy` verbs are not part of
  the nightly. Their bodies are already baked into the rsync'd artifacts. If a
  legacy-imported source ever needs re-parsing on the box, mount the legacy tree
  and set `LEGACY_ROOT` first.
- **OpenSearch :9200 is loopback-only** and never on the public NIC; the app
  reaches it over the compose network. Keep the host firewall closed on
  9200/9600 regardless.
- **Renewed certs need an nginx reload** — the weekly cron above does it; certbot
  deliberately has no docker.sock access to reload nginx itself.
