/* Self-hosted, cookie-less Matomo page tracking.

   * The tracker URL is same-origin (/matomo/, an nginx block on this host
     proxying the Matomo container) rather than //lagen.nu/matomo/. A
     cross-origin tracker is what browser tracking protection exists to block,
     and Matomo falls back to POST for long payloads, which cross-origin would
     need CORS on Matomo's side.
   * The site id is looked up by hostname, so only a host we have actually
     registered in Matomo reports anything: a dev serve on localhost, a staging
     copy or a mirror stays silent instead of writing into prod's numbers.
     Each deployed hostname must have an explicit site id here.

   Kept first in the script.js bundle deliberately: the bundle is one
   concatenated script, so an uncaught error anywhere in it stops everything
   after -- the ping should not be downstream of the reading chrome. */
(function () {
  var SITES = {"ferenda.lagen.nu": 2};        // hostname -> Matomo site id
  var site = SITES[location.hostname];
  if (!site) return;
  var u = "/matomo/";
  var _paq = window._paq = window._paq || [];
  /* no cookie, no browser fingerprint: a visit is counted, a visitor is not
     followed -- the privacy stance lagen.nu ships with */
  _paq.push(["disableCookies"]);
  _paq.push(["disableBrowserFeatureDetection"]);
  /* A /samling bookmark keeps its complete document recipe in the fragment.
     Fragments never reach HTTP, and analytics must not copy one out through
     its browser API. Anchors are navigation state on every other page too. */
  _paq.push(["setCustomUrl", location.pathname + location.search]);
  _paq.push(["trackPageView"]);
  _paq.push(["setTrackerUrl", u + "matomo.php"]);
  _paq.push(["setSiteId", String(site)]);
  var g = document.createElement("script"),
      s = document.getElementsByTagName("script")[0];
  g.async = true;
  g.defer = true;
  g.src = u + "matomo.js";
  s.parentNode.insertBefore(g, s);
})();
