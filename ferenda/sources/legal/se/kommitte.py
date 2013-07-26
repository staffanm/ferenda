# -*- coding: utf-8 -*-
from ferenda import DocumentRepository
import re


class Kommitte(DocumentRepository):
    module_dir = "komm"
    start_url = "http://62.95.69.15/cgi-bin/thw?${HTML}=komm_lst&${OOHTML}=komm_doc&${SNHTML}=komm_err&${MAXPAGE}=26&${TRIPSHOW}=format%3DTHW&${BASE}=KOMM"
    source_encoding = "iso-8859-1"

    document_url = "http://62.95.69.15/cgi-bin/thw?${HTML}=komm_lst&${OOHTML}=komm_doc&${TRIPSHOW}=format=THW&${APPL}=KOMM&${BASE}=KOMM&BET=%s"

    # this is almost identical to DirTrips. Can we refactor similar
    # things to a TripsRepository base class?
    re_basefile = re.compile(r'(\w+ \d{4}:\w+)', re.UNICODE)

    def download_everything(self, usecache=False):
        self.log.info("Starting at %s" % self.start_url)
        self.browser.open(self.start_url)
        done = False
        pagecnt = 1
        while not done:
            self.log.info('Result page #%s' % pagecnt)
            for link in self.browser.links(text_regex=self.re_basefile):
                basefile = self.re_basefile.search(link.text).group(1)
                if not isinstance(basefile, str):
                    basefile = str(basefile, encoding=self.source_encoding)
                url = self.document_url % urllib.parse.quote(link.text)
                self.download_single(basefile, usecache=usecache, url=url)
            try:
                self.browser.follow_link(text='Fler poster')
                pagecnt += 1
            except LinkNotFoundError:
                self.log.info(
                    'No next page link found, this was the last page')
                done = True

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

if __name__ == "__main__":
    Kommitte.run()
