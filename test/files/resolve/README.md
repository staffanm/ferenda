# Citation fixtures

`nja.json` and `families.json` mix real references with synthetic boundary cases.
Their absence expectations use an empty catalog and the fixed date 2026-09-14.
They do not claim that the real references are absent from lagen.nu.

`extraction.json` checks citations within prose across Swedish, EU and international
forms. It includes repeats, contextual provisions and malformed candidates.
Empty target lists preserve candidates that lack an interpretation.

`avtalslagen.json` freezes the published statute's complete structural tree.
It retains only `uri`, `type`, `id` and `children`, with text and metadata removed.
Source: [lagen.nu document API](https://lagen.nu/api/v1/document?uri=https%3A%2F%2Flagen.nu%2F1915%3A218), read 2026-09-14.
The four chapters and continuous paragraph numbering also appear in
[Riksdagens statute text](https://www.riksdagen.se/sv/dokument-och-lagar/dokument/svensk-forfattningssamling/lag-1915218-om-avtal-och-andra-rattshandlingar_sfs-1915-218/).
