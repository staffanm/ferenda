# Arbetsförmedlingen register fixtures

Fetched from Arbetsförmedlingen on 2026-10-17.

`affs-index.html` contains the search app and page ID from
<https://arbetsformedlingen.se/om-oss/var-verksamhet/styrning-och-resultat/forfattningssamling-affs>.
The app's initial state contains 10 AFFS records and one AMSFS record.

`affs-annulment.json` contains the complete response to the archive filter.
It has 32 AFFS records and 40 AMSFS records. The JSON whitespace is formatted.

The official app calls this public endpoint when the reader selects “Upphävda”:

```http
POST /appresource/4.306dde6d1690b54594a1efc/12.351bd4ea198ca9ea3fc952/getNewItems
Host: arbetsformedlingen.se
Content-Type: application/json

{"searchValue":"","filterList":"annulment","sortOrder":false}
```

The page ID and portlet ID come from the index HTML. They can change when
the site publishes a new app deployment.
