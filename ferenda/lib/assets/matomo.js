/* Self-hosted, cookie-less Matomo page tracking.

   * The tracker URL is same-origin (/matomo/, an nginx block on this host
     proxying the Matomo container) rather than //lagen.nu/matomo/. A
     cross-origin tracker is what browser tracking protection exists to block,
     and Matomo falls back to POST for long payloads, which cross-origin would
     need CORS on Matomo's side.
   * The site id is looked up by hostname, so only a host we have actually
     registered in Matomo reports anything: a dev serve on localhost, a staging
     copy or a mirror stays silent instead of writing into prod's numbers.
     Each deployed hostname must have an explicit site id here -- and that cuts
     both ways: after the 2026-09-05 cutover the pages answer on lagen.nu while
     the table still named ferenda.lagen.nu, so the lookup missed, the snippet
     returned before loading anything, and no reader page was counted at all.
     ferenda.lagen.nu is gone from the table because it no longer serves a page
     to run this in: its vhost is a 308 to lagen.nu.
   * The subdomain projections report as site 2 as well (Staffan's call,
     2026-09-08): <slug> under each of the four zones -- lagen.nu,
     direktivet.nu, forordningen.nu and its IDN twin xn--frordningen-rfb.nu --
     serves an act's own generated page (site/subdomains.py symlinks it),
     bundle included. They all serve that page at "/", so setCustomUrl below
     sends the whole origin: a bare pathname would merge every one of them into
     the apex's "/" row.

   Kept first in the script.js bundle deliberately: the bundle is one
   concatenated script, so an uncaught error anywhere in it stops everything
   after -- the ping should not be downstream of the reading chrome. */
(function () {
  var SITES = {"lagen.nu": 2};                 // hostname -> Matomo site id
  /* The definite-form subdomains, one Matomo site with the apex. The zone list
     is docker/nginx/subdomains.conf's own `server_name` regex. nginx gives an
     exact server_name priority over that regex, so the two hostnames below
     match the shape but are not subdomain pages: old.lagen.nu is the legacy
     site (site 1, its own snippet) and ferenda.lagen.nu is a 308. */
  var ZONES = /^[a-z0-9-]+\.(lagen|direktivet|forordningen|xn--frordningen-rfb)\.nu$/;
  var NOT_A_PAGE = {"old.lagen.nu": 1, "ferenda.lagen.nu": 1};
  var host = location.hostname;
  var site = SITES[host] ||
      (ZONES.test(host) && !NOT_A_PAGE[host] ? 2 : 0);
  if (!site) return;
  var u = "/matomo/";
  var _paq = window._paq = window._paq || [];
  /* no cookie, no browser fingerprint: a visit is counted, a visitor is not
     followed -- the privacy stance lagen.nu ships with */
  _paq.push(["disableCookies"]);
  _paq.push(["disableBrowserFeatureDetection"]);
  /* Origin included, so the subdomain pages -- every one of them served at
     "/" -- stay apart from each other and from the apex's own front page.
     A /samling bookmark keeps its complete document recipe in the fragment.
     Fragments never reach HTTP, and analytics must not copy one out through
     its browser API. Anchors are navigation state on every other page too. */
  _paq.push(["setCustomUrl",
             location.origin + location.pathname + location.search]);
  _paq.push(["trackPageView"]);
  _paq.push(["setTrackerUrl", u + "matomo.php"]);
  _paq.push(["setSiteId", String(site)]);
  /* Video starts. Matomo counts media only with the MediaAnalytics plugin,
     which is a paid add-on this install does not have, so each screencast on
     the /om/ pages reports one custom event instead -- Behaviour > Events,
     category "Video", action "Start", name the film's file name. `play` does
     not bubble, hence the capturing listener on the document, and it fires
     again after every pause, hence the flag: one start per element per page
     view. The push names `window._paq`, not the `_paq` above: the tracker
     drains that array on load and replaces the global with its own object, so
     a reference captured here stops being read the moment matomo.js arrives. */
  document.addEventListener("play", function (e) {
    var v = e.target;
    if (v.tagName !== "VIDEO" || v.dataset.matomoStarted) return;
    v.dataset.matomoStarted = "1";
    window._paq.push(["trackEvent", "Video", "Start",
                      (v.currentSrc || "").split("/").pop()]);
  }, true);
  var g = document.createElement("script"),
      s = document.getElementsByTagName("script")[0];
  g.async = true;
  g.defer = true;
  g.src = u + "matomo.js";
  s.parentNode.insertBefore(g, s);
})();
