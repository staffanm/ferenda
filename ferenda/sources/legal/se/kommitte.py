# -*- coding: utf-8 -*-
import re

from . import SwedishLegalStore
from . import Trips


class KommitteStore(SwedishLegalStore):

    def basefile_to_pathfrag(self, basefile):
        # "Ju 2012:01"    => "Ju/2012/01"
        return basefile.replace(" ", "/").replace(":", "/")

    def pathfrag_to_basefile(self, pathfrag):
        # "Ju/2012/01"    => "2012:152"
        return pathfrag.replace("/", " ", 1).replace("/", ":")


class Kommitte(Trips):
    documentstore_class = KommitteStore
    alias = "komm"
    app = "komm"
    base = "KOMM"
    download_params = [{'maxpage': 101, 'app': app, 'base': base}]
    basefile_regex = "(?P<basefile>\w+ \d{4}:\w+)$"

    re_basefile = re.compile(r'(\w+ \d{4}:\w+)', re.UNICODE)

    def parse_from_soup(self, soup, basefile):
        pre = soup.findAll("pre")[-1]
        text = ''.join(pre.findAll(text=True))
        print(text)


# End result something like this
#
# <http://rinfo.lagrummet.se/komm/a/1991:03> a :Kommittebeskrivning
#         dct:identifier "A 1991:03" ;
#         :tillkalladAr "1991" ;
#         :lopnummer "03";
#         :kommittestatus "Avslutad";
#         :avslutadAr "1993";
#         :departement <http://rinfo.lagrummet.se/publ/org/Arbetsmarknadsdepartementet>;
#         :kommittedirektiv <http://rinfo.lagrummet.se/publ/dir/1991:75> ,
#                           <http://rinfo.lagrummet.se/publ/dir/1992:33> ,
#         :betankanden <http://rinfo.lagrummet.se/publ/bet/sou/1993:81> .
#
# <http://rinfo.lagrummet.se/publ/bet/sou/1993:81> dct:title "Översyn av arbetsmiljölagen";
