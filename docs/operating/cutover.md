# The September cutover

Two lagen.nu sites are live on one host. Today the hostnames are:

| Hostname | Serves |
|---|---|
| `lagen.nu` | `ferenda-legacy`, the old application |
| `ferenda.lagen.nu` | `ferenda`, the rebuilt site |

After the cutover they are:

| Hostname | Serves |
|---|---|
| `lagen.nu` | `ferenda`, the rebuilt site |
| `old.lagen.nu` | `ferenda-legacy`, the old application |
| `ferenda.lagen.nu` | a permanent redirect to the same path on `lagen.nu` |

Both applications keep running. Only which hostname reaches which one moves, so
the switch is an nginx edit and a reload. Nothing is rebuilt, nothing is
restarted, and no data moves.

## First: moving the host onto this branch

The hostname switch assumes the host already runs this branch's
`docker-compose.yml`. Getting there is a separate one-time operation, and it
does move things on disk. Do it well before the cutover day, and verify the
site afterwards.

The layout this file expects:

| Path | Holds |
|---|---|
| `~/wds/ferenda` | this branch — the compose project AND the image build context |
| `~/wds/ferenda-legacy` | the `legacy` branch, bind-mounted into `ferenda-legacy` |
| `/mnt/forstor/ferenda` | the corpus (`downloaded/`, `artifact/`, `generated/`, `dumps/`) |
| `/mnt/data/ferenda` | `catalog.sqlite` on the fast local disk |

Four things move, and each is a rename of what is there today:

1. `~/wds/ferenda` (the old compose project, a `master` checkout) becomes
   `~/wds/ferenda-legacy`, then is reset to the `legacy` branch.
2. `~/wds/accommodanda` becomes `~/wds/ferenda`. Keeping the directory named
   `ferenda` keeps the compose project name, so the existing volumes
   (`ferenda_opensearch-data`, `ferenda_db`, …) stay addressable by name.
3. `/mnt/data/accommodanda` becomes `/mnt/data/ferenda`. **A `/mnt/data/ferenda`
   already exists**: a 92 MB checkout from 2020. Move it aside first, or the
   rename fails and the catalog mount silently binds the wrong directory.
4. `/mnt/forstor/accommodanda` becomes `/mnt/forstor/ferenda`. A rename edits
   the parent directory, and `/mnt/forstor` is `root:lagen-nfs` mode 775, so
   the deploy user needs `lagen-nfs` (gid 3362): `sudo usermod -aG lagen-nfs
   staffan`, then a new login, or `sg lagen-nfs -c '…'` in the current one.
   `sudo` alone does not work — the export is root_squashed
   (`maproot="nobody":"nobody"`), so root has *fewer* rights there than the
   deploy user.

Three things on the host name a service or a path that this migration changes,
and each keeps running on the old name until it is edited:

- the crontab — five jobs run `docker compose exec -T ferenda build …`, which is
  the *legacy* application. They become `ferenda-legacy`.
- `~/bin/keepwarm.sh` and `~/bin/mcptail.sh` — both name the
  `ferenda-accommodanda-1` container, now `ferenda`.
- the untracked `sync-up` script in the repo's `.bin/` on the dev box — it named
  `/mnt/forstor/accommodanda` and `/mnt/data/accommodanda`, now
  `/mnt/forstor/ferenda` and `/mnt/data/ferenda`. Done 2026-08-26.

Stop the stack for the renames, and start it again with
`docker compose --profile prod up -d`. The service names changed, so compose
recreates the containers; the volumes and the bind-mounted data do not move.

## Before the day

1. **DNS.** Point `old.lagen.nu` at this host. Wait until it resolves
   everywhere; the certificate step needs it.
2. **Certificate.** Add the new name to the existing SAN certificate. Run the
   `certonly` command in `docker-compose.yml`'s `certbot` service comment with
   `-d old.lagen.nu` appended to the `-d` list. Certbot expands the certificate
   in place, so nginx's `ssl_certificate` paths do not change.
3. **Check the redirect target.** Every `ferenda.lagen.nu` path must have a
   working `lagen.nu` twin. They are the same application, so this holds by
   construction, but spot-check `/api/v1/`, `/dumps/` and a document URL.
4. **No regenerate is needed for the host name.** A generated page names no
   host of its own: its links are root-relative, and the `<base>` a subdomain
   landing needs is added by a head script from the page's `rel=canonical`,
   which already names `lagen.nu`. (Until the switch, that makes a subdomain
   landing's links lead to the legacy site; the subdomains go live with the
   switch.)

## The switch

Six edits and a reload. Every line to change is marked `CUTOVER` in its file.

1. `docker/nginx/default.conf` — the legacy vhost. Two `server_name` lines:
   `lagen.nu` becomes `old.lagen.nu`.
2. `docker/nginx/ferenda.lagen.nu.conf` — the rebuilt site. Two `server_name`
   lines: `ferenda.lagen.nu` becomes `lagen.nu`.
3. The same file, at its foot: uncomment the two-server redirect block. It
   answers `ferenda.lagen.nu` with `301` to `lagen.nu`.
4. The same file: delete the three `X-Robots-Tag "noindex, nofollow"` lines
   (server scope, `/dumps/`, the Matomo block). They kept the preview out of
   the index; left in place they take `lagen.nu` out of it within days.
5. `docker/nginx/default.conf`: uncomment the `X-Robots-Tag` line. The legacy
   application at `old.lagen.nu` is a duplicate of the whole site and must not
   be indexed under its new name. (A `Disallow: /` in its robots.txt would
   only stop the crawl; a disallowed URL someone links to is still indexed
   by reference, and a crawler that may not fetch a page never sees a noindex.)
6. `docker/nginx/subdomains.conf`: the three marked `302` targets become
   `lagen.nu`.
7. Reload:

   ```sh
   docker compose exec nginx nginx -t
   docker compose exec nginx nginx -s reload
   ```

`nginx -t` first. A `server_name` typo makes nginx refuse the reload, which is
the safe failure; a config that parses but points the wrong way is not.

## Rolling back

Put the comment markers and the header lines back and reload again. The
rollback is the same edits in reverse and takes as long as the reload. Nothing else changed, so
there is nothing else to undo.

## Keeping the redirect

Keep the `ferenda.lagen.nu` redirect indefinitely. The hostname was public for
months, so search engines and other people's links point at it, and only the
`301` moves them.

## Afterwards

Search engines need two things from the switch day:

- `https://lagen.nu/sitemap.xml` (written by every full generate) submitted
  in Search Console, so the sources the legacy site never had -- EU law,
  Europadomstolen, föreskrifter -- are found in weeks rather than months.
- A Search Console property for `ferenda.lagen.nu`, to watch its indexed URLs
  drain through the `301`. Legacy URLs the rebuilt site does not answer are
  redirected or answered `410` in `docker/nginx/ferenda.lagen.nu.conf`; a new
  `404` spike in the coverage report names one that block missed.


The legacy site stays reachable at `old.lagen.nu` for as long as it is useful.
When it is retired, remove the `ferenda-legacy`, `fuseki` and `mediawiki`
services from `docker-compose.yml`, delete `docker/nginx/default.conf`, and
drop `old.lagen.nu` from the certificate and from DNS.
