# Kända luckor i remisser-korpuset

Remissvar som `download.py` medvetet *inte* lagrar. Var och en är en väntande
kodändring, inte ett övergående fel: de loggas som `(retried next run)`, men
nästa körning möter samma sak och hoppar över dem igen. Listan är avstämd mot
hela `lagen all all`-körningen 2026-07-31.

Ett fel som självläker (0-bytes-svar, en timeout) hör inte hemma här — det här
är de fall där hämtningen är korrekt i att vägra, och där något i koden måste
ändras innan dokumenten kan komma in.

## 1. Remisser vars remitterade dokument saknar identitetsregel (8 st)

Remissen pekar på ett dokument på regeringen.se, men `lib.regeringen` kan inte
översätta URL:en till ett basefile, så remissen får ingen ärendeidentitet och
inga svar hämtas. Åtgärd: en identitetsregel per URL-form i `lib/regeringen.py`.

| Remiss (slug på regeringen.se) | Remitterat dokument (länk i remissen) |
| --- | --- |
| `remiss-av-sou-2023-27-kamerabevakning-for-ett-battre-djurskydd` (2024/01) | `/statens-offentliga-utredningar/2023/06/sou-202327/` |
| `remiss-av-elvagsutredningens-betankande-regler-for-statliga-elvagar` (2021/09) | `/statens-offentliga-utredningar/2021/09/sou-202173/` |
| `remiss-av-forslag-till-uppdaterad-forordning-om-miljo--och-trafiksakerhetskrav-for-myndigheters-bilar-och-bilresor` (2019/12) | `/forordningsmotiv/2019/12/forordning-om-miljo--och-trafiksakerhetskrav-for-myndigheters-bilar/` |
| `sou-201773-en-gemensam-bild-av-bostadsbyggnadsbehovet` (2017/11) | `/statens-offentliga-utredningar/2017/09/sou-201773/` |
| `remiss-av-energikommissionens-betankande-kraftsamling-for-framtidens-energi` (2017/01) | `/statens-offentliga-utredningar/2017/01/sou-20172/` |
| `remiss-av-delbetankande-fran-miljomalsberedningen-med-forslag-om-en-klimat--och-luftvardsstrategi-for-sverige` (2016/06) | `/statens-offentliga-utredningar/2016/06/en-klimat--och-luftvardsstrategi-for-sverige/` |
| `remiss-av-delbetankande-fran-miljomalsberedningen-med-forslag-om-ett-klimatpolitiskt-ramverk-inklusive-langsiktigt-klimatmal` (2016/03) | `/statens-offentliga-utredningar/2016/03/sou-201621/` |
| `remiss-av-ds-201551-avgiftsfrihet-for-viss-screening-inom-halso--och-sjukvarden` (2015/11) | `/skrivelse/2015/11/skr.-201551` |

Två former återkommer och förklarar de flesta raderna:

* **SOU-numret utan kolon i slugen** — `sou-202327`, `sou-202173`, `sou-201773`,
  `sou-20172`, `sou-201621`. Här finns numret i URL:en men i en form regeln inte
  läser. Det är den enskilt största gruppen (5 av 8) och rimligen en regel.
* **Slugen namnger dokumentet i stället för att numrera det** —
  `en-klimat--och-luftvardsstrategi-for-sverige`,
  `forordning-om-miljo--och-trafiksakerhetskrav-for-myndigheters-bilar`. Här
  finns inget nummer att läsa ur URL:en alls; identiteten måste hämtas från
  dokumentsidan.

Den sista raden är dessutom feletiketterad i remissen: den heter
"Remiss av Ds 2015:51" men länkar en **skrivelse** (`skr. 2015:51`). En
identitetsregel måste följa länken, inte remissens rubrik.

## 2. Remisser med kolliderande organisationsslugar (6 st)

Två svars-PDF:er från samma organisation på samma remiss ger samma slug, alltså
samma filnamn. Hämtningen vägrar hellre än låter den ena tyst skriva över den
andra — se `_walk` i `download.py`. Följden är att **inga** svar på dessa sex
remisser lagras, inte bara de kolliderande.

| Remiss | Kolliderande organisation(er) |
| --- | --- |
| `sou/2019:45` | energimyndigheten |
| `sou/2019:16` | stralsakerhetsmyndigheten |
| `sou/2020:4` | svenskt-naringsliv, vastra-gotalandsregionen |
| `pm/Ju2019/00509/L7` | svenska-kyrkan |
| `sou/2018:91` | sparbankernas-riksforbund |
| `sou/2018:57` | stockholms-lans-landsting |

Åtgärd: en disambiguering i slugen när samma organisation svarar två gånger (ett
löpnummer på den andra förekomsten), så båda svaren får var sitt filnamn. Det
kräver att slugen förblir stabil för de tusentals svar som *inte* kolliderar —
suffixet får bara läggas på dubbletten, aldrig på den första.

## Inte luckor

Följande loggas i samma körning men läker av sig själva och hör inte hit:

* **0-bytes-svar** — regeringen.se svarade med tom kropp för fyra svar
  (`ds/2019:4/stiftelsen-allmanna-barnhuset`,
  `sou/2018:44/upphandlingsmyndigheten`, `sou/2018:77/solna-kommun`,
  `pm/KN2025/01294`-bilagan). Ingen post skrivs, så nästa körning hämtar om.
* **Word-dokument lagrade som `.pdf`** — fyra svar; hanteras sedan 2026-07-31 av
  `_body_text` i `parse.py`, som läser filens magiska bytes och skickar Word
  vidare till `lib.poi`.
* **Trasig PDF utan läsbar korsreferenstabell** — ett svar
  (`sou/2020:58/stockholms-universitet-juridiska-fakulteten`); repareras sedan
  2026-07-31 av `lib.pdftext.repair_pdf`.
* **Permanent trasiga PDF:er** — se `BROKEN_PDFS` i `parse.py`, som bär sin egen
  bevisning per post.
