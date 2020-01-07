# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

from collections import Counter, defaultdict
from io import BytesIO
from urllib.parse import urlencode
import ast
import difflib
import filecmp
import math
import os
import re

import lxml.html
from bs4 import BeautifulSoup
from rdflib import URIRef, Literal
from cached_property import cached_property

from ferenda import util, errors
from ferenda import PDFReader
from ferenda.elements import Body
from ferenda.decorators import action
from . import RPUBL, RINFOEX
from .elements import Meta
from .fixedlayoutsource import FixedLayoutSource, FixedLayoutStore, FixedLayoutHandler

class KKVHandler(FixedLayoutHandler):
    # this is a simplified version of MyndFskrHandler.get_pathfunc
    def get_pathfunc(self, environ, basefile, params, contenttype, suffix):
        if basefile and suffix == "png":
            params["dir"] = "downloaded"
            params["page"] = str(int(environ["PATH_INFO"].split("/sid")[1][:-4])-1)
            params["format"] = suffix
        return super(FixedLayoutHandler, self).get_pathfunc(environ, basefile, params,
                                                         contenttype, suffix)
            

class KKV(FixedLayoutSource):
    """Hanterar konkurrensverkets databas över upphandlingsmål. 

Dokumenten härstammar alltså inte från konkurrensverket, men det är
den myndighet som samlar, strukturerar och tillgängliggör dem.
"""

    alias = "kkv"
    storage_policy = "dir"
    start_url = "http://www.konkurrensverket.se/domar/DomarKKV/domar.asp"
    document_url_regex = ".*/arende.asp\?id=(?P<basefile>\d+)"
    document_url_template = "http://www.konkurrensverket.se/domar/DomarKKV/arende.asp?id=%(basefile)s"
    source_encoding = "iso-8859-1"
    download_iterlinks = False
    download_accept_404 = True
    download_accept_400 = True
    download_archive = False
    rdf_type = RPUBL.VagledandeDomstolsavgorande  # FIXME: Not all are Vägledande...
    xslt_template = "xsl/dom.xsl" # FIXME: don't we have a better template?
    requesthandler_class = KKVHandler

    _default_creator_predicate = RINFOEX.domstol

    identifiers = {}
    
    @classmethod
    def get_default_options(cls):
        opts = super(KKV, cls).get_default_options()
        opts['cssfiles'].append('css/pdfview.css')
        opts['jsfiles'].append('js/pdfviewer.js')
        return opts
        
    def __init__(self, config=None, **kwargs):
        super(KKV, self).__init__(config, **kwargs)
        self.vectors = self.load_vectors()

    @cached_property
    def parse_options(self):
        # we use a file with python literals rather than json because
        # comments
        if self.resourceloader.exists("options/%s.py"  % self.urispace_segment):
            with self.resourceloader.open("options/%s.py" % self.urispace_segment) as fp:
                return ast.literal_eval(fp.read())
        else:
            return {}

    def get_parse_options(self, basefile):
        return defaultdict(lambda: None, self.parse_options.get(basefile, {}))

    re_words = re.compile(r'\w+')
    def load_vectors(self):
        # for resourcename in self.resourceloader.listresources("extra/*txt"):
        res = {}
        for resourcename in ("fr-05.txt", "formular-9.txt", "dv-3109-1-b-lou.txt", "formular-1.txt", "dv-3109-d.txt", "dv-3109-1-a-lou.txt", "dv-3109-1-a.txt"):
            with self.resourceloader.openfp("examples/" + resourcename) as fp:
                pages = fp.read().split("\x08")
                for idx, page in enumerate(pages):
                    res[(resourcename, idx)] = Counter(self.re_words.findall(page.lower()))
        return res

    def cos_distance(self, vector1, vector2):
     intersection = set(vector1.keys()) & set(vector2.keys())
     numerator = sum([vector1[x] * vector2[x] for x in intersection])
     sum1 = sum([vector1[x]**2 for x in vector1.keys()])
     sum2 = sum([vector2[x]**2 for x in vector2.keys()])
     denominator = math.sqrt(sum1) * math.sqrt(sum2)
     if not denominator:
         return 0.0
     else:
         return float(numerator) / denominator

    # For now we use a simpler basefile-to-uri mapping through these
    # implementations of canonical_uri and coin_uri
    def canonical_uri(self, basefile):
        return "%s%s/%s" % (self.config.url, self.alias, basefile)

    def coin_uri(self, resource, basefile):
        return self.canonical_uri(basefile)
    
    def basefile_from_uri(self, uri):
        basefile_segment = -2 if re.search('/sid\d+.png$',uri) else -1
        return uri.split("/")[basefile_segment].split("?")[0]

    def download_get_first_page(self):
        resp = self.session.get(self.start_url)
        tree = lxml.html.document_fromstring(resp.text)
        tree.make_links_absolute(self.start_url, resolve_base_href=True)
        form = tree.forms[1]
        form.fields['beslutsdatumfrom'] = '2000-01-01'
        # form.fields['beslutsdatumfrom'] = '2018-09-01'
        action = form.action
        parameters = form.form_values()
        # self.log.debug("First Params (%s): %s" % (action, dict(parameters)))
        res = self.session.post(action, data=dict(parameters))
        return res

    def download_is_different(self, existing, new):
        return not filecmp.cmp(new, existing, shallow=False)


    def download_single(self, basefile, url=None):
        headnote = self.store.downloaded_path(basefile, attachment="headnote.html")
        if url is None:
            url = self.remote_url(basefile)
        new = self.download_if_needed(url, basefile, filename=headnote, archive=self.download_archive)
        soup = BeautifulSoup(util.readfile(headnote, encoding=self.source_encoding), "lxml")
        beslut = soup.find("a", text=re.compile("\w*Beslut\w*"))
        if not beslut:
            self.log.warning("%s: %s contains no PDF link" % (basefile, url))
            outfile = self.store.downloaded_path(basefile)
            util.writefile(outfile, "")
            os.utime(outfile, (0,0)) # set the atime,mtime to start of epoch so that subsequent attempts to download doesn't return an unwarranted 304
            return True
        url = beslut.get("href")
        assert url
        return super(KKV, self).download_single(basefile, url)


    def download_get_basefiles(self, source):
        page = 1
        done = False

        while not done:
            # soup = BeautifulSoup(source, "lxml")
            # links = soup.find_all("a", href=re.compile("arende\.asp"))
            # self.log.debug("Links on this page: %s" % ", ".join([x.text for x in links]))
            tree = lxml.html.document_fromstring(source)
            tree.make_links_absolute(self.start_url, resolve_base_href=True)
            self.downloaded_iterlinks = True
            for res in super(KKV, self).download_get_basefiles(tree.iterlinks()):
                yield res
            self.download_iterlinks = False
            done = True
            linktext = str(page+1)
            for element in tree.findall(".//a"):
                if element.text == linktext and element.get("href").startswith("javascript:"):
                    done = False
                    page += 1
                    form = tree.forms[1]
                    form.fields['showpage'] = str(page)
                    action = form.action
                    parameters = form.form_values()
                    self.log.debug("Downloading page %s" % page)
                    # self.log.debug("Params (%s): %s" % (action, dict(parameters)))
                    res = self.session.post(action, data=dict(parameters))
                    source = res.text
                    break

#    def downloaded_to_intermediate(self, basefile, attachment=None):
#        # the PDF file wasn't available. Let's try to just parse the metadata for now
#        if os.path.getsize(self.store.downloaded_path(basefile)) == 0:
#            fp = BytesIO(b"""<pdf2xml>
#            <page number="1" position="absolute" top="0" left="0" height="1029" width="701">
#	    <fontspec id="0" size="12" family="TimesNewRomanPSMT" color="#000000"/>
#            <text top="67" left="77" width="287" height="26" font="0">[Avg&#246;randetext saknas]</text>
#            </page>
#            </pdf2xml>""")
#            fp.name = "dummy.xml"
#            return fp
#        else:
#            return super(KKV, self).downloaded_to_intermediate(basefile, attachment)

    def extract_head(self, fp, basefile):
        data = util.readfile(self.store.downloaded_path(basefile, attachment="headnote.html"), encoding=self.source_encoding)
        return BeautifulSoup(data, "lxml")

    def infer_identifier(self, basefile):
        return self.identifiers[basefile]

    lblmap = {"Domstol:": "rinfoex:domstol",  # this ad-hoc predicate
                                              # keeps
                                              # attributes_to_resource
                                              # from converting the
                                              # string into a URI,
                                              # which we'd like to
                                              # avoid for now
              "Instans:": "rinfoex:instanstyp",
              "Målnummer:": "rpubl:malnummer",
              "Ärendemening:": "dcterms:title",
              "Beslutsdatum:": "rpubl:avgorandedatum",
              "Leverantör/Sökande:": "rinfoex:leverantor",
              "UM/UE:": "rinfoex:upphandlande",
              "Ärendetyp:": "rinfoex:arendetyp",
              "Avgörande:": "rinfoex:avgorande",
              "Kortreferat:": "dcterms:abstract"}
    def extract_metadata(self, rawhead, basefile):
        d = self.metadata_from_basefile(basefile)
        for row in rawhead.find("table", "tabellram").find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            lbl = cells[0].text.strip()
            value = cells[1].text.strip()
            if value and lbl and self.lblmap.get(lbl):
                assert lbl.endswith(":"), "invalid label %s" % lbl
                d[self.lblmap[lbl]] = value
        d["dcterms:issued"] = d["rpubl:avgorandedatum"]
        self.identifiers[basefile] = "%ss dom den %s i mål %s" % (d["rinfoex:domstol"],
                                                                  d["rpubl:avgorandedatum"],
                                                                  d["rpubl:malnummer"])
        beslut = rawhead.find("a", text=re.compile("\w*Beslut\w*"))
        if beslut:
            # assume that the href is a valid url
            d["prov:wasDerivedFrom"] = URIRef(beslut.get("href").replace(" ", "%20"))
            assert str(d["prov:wasDerivedFrom"]).startswith("http")

        self._remove_overklagandehanvisning = d["rinfoex:domstol"] != "Högsta förvaltningsdomstolen"
        return d

    def polish_metadata(self, attribs, basefile, infer_nodes=True):
        # expand :malnummer, :upphandlande, :leverantor into lists
        # since these can be of the form "7040-17, 7048--7050-17" or
        # "1. Uppsala kommun 2. UK Skolfastigheter AB 3. Uppsala
        # kommuns Fastighetsbolag m.fl."
        if "," in attribs["rpubl:malnummer"]:
            attribs["rpubl:malnummer"] = re.split(", *", attribs["rpubl:malnummer"])
        for k in "rinfoex:upphandlande", "rinfoex:leverantor":
            if attribs[k].startswith("1."):
                attribs[k] = [x.strip() for x in re.split("\d\. *", attribs[k]) if x]
        return super(KKV, self).polish_metadata(attribs, basefile, infer_nodes)
    

    def clean_name(self, name, count=1):
        if name is None:
            return None
        if count > 1:
            # eg 'Magnus Schultzberg Patricia Schömer' =>
            parts = name.split()
            res = []
            if len(parts) % 2 != 0:
                self.log.warning("Can't split %s into equal firstname,lastname tuples" % name)
                return None,
            for idx, part in enumerate(parts):
                if idx % 2 == 0:
                    first = part
                else:
                    res.append(self.clean_name("%s %s" % (first, part)))
            return res
            
        if "DV 3109" in name:
            self.log.warning("Can't clean name %s, mis-identified name" % name)
            return None
        if name.startswith("holmgrenhansson"): #specialcase to handle the following regex check
            name = "H" + name[1:]
        newname = self.clean_line(name, "full")
        if newname is None:
            self.log.warning("Can't clean name %s, doesn't look remotely like a name" % name)
        return newname

    def clean_line(self, line, mode="light"):
        if mode == "light":
            # just remove all leanding and trailing non-alpha
            regex = r"^\W*(.*?)\W*$"
        else:
            # remove leading and trailing non-alpha until the first uppercase letter
            regex = r"^[^A-ZÅÄÖ]*([A-ZÅÄÖ].*?)[^a-zåäöA-ZÅÄÖ]*$"
        m = re.match(regex, line)
        if m:
            return m.group(1)
        else:
            return None

    def is_overklagandehanvisning(self, page, pageidx):
        pagevector = Counter(self.re_words.findall(page.as_plaintext().lower()))
        for vectorid in self.vectors:
            if vectorid[1] != 0:  # for now, only consider the 1st page of all example vectors
                continue
            cosdist = self.cos_distance(pagevector, self.vectors[vectorid])
            self.log.debug("is_overklagandehanvisning: page %s has %s similarity with %r" % (pageidx+1, cosdist, vectorid))
            if cosdist > 0.9:
                return True
        
    def is_overklagandehanvisning_old(self, page):
        # FIXME: we should look at the entirety of the text
        # and compare its distance (edit distance, some sort
        # of vector distance?) to a standard
        # överklagandehänvisning appendix

        # only look at the top 1/4 of the page
        pgnum = False
        malnum = False
        hanvisning = False
        for textbox in page.boundingbox(0, 0, page.height/4, page.width):
            textbox = str(textbox).strip()
            if textbox in ("HUR MAN ÖVERKLAGAR - PRÖVNINGSTILLSTÅND",
                           "Hur man överklagar FR-05",
                           "HUR MAN ÖVERKLAGAR"): # KamR
                hanvisning = True
            # avoid false positives for the last page of the
            # real verdict by checking for indicators that
            # we're still within the real verdict
            if re.match("Sida \d+$", textbox):
                pgnum = True
            if re.match("\d+\d{2}$", textbox):
                malnum = True
        return hanvisning and not (pgnum or malnum)

    def detect_ombud(self, sokande):
        ombud = False
        for line in sokande:
            if line.startswith("Ombud:"):
                ombud = True
            if ombud and ("firman " in line or "byrå " in line or  "AB" in line or "KB" in line or "HB" in line):
                return self.normalize_ombud(self.clean_name(line))

    def detect_domare(self, trailing):
        titles = ("förvaltningsrättsfiskal", "kammarrättsråd", "lagman","rådman", "chefsrådman")
        modifiers = ("tf. ", "fd. ", "t.f. ", "f.d. ", "tf ", "fd ")
        domare = False
        # first strategy: Whatever line is followed by a known title
        for line in reversed(trailing):
            line = self.clean_line(line)
            if not line:
                continue
            if line.lower().startswith(modifiers):
                line = line.split(" ", 1)[1]
            parts = line.lower().split(" ") 
            if all(part in titles for part in parts):
                domare = len(parts) # a true value
            elif domare:
                # clean_name might return a string or a list. If the
                # latter, just keep the first one for now
                ret = self.clean_name(line, domare)
                if isinstance(ret, list):
                    return ret[0]
                else:
                    return ret
        # second strategy: Whatever line is followed by the föredragande
        for line in reversed(trailing):
            line = self.clean_line(line)
            if not line:
                continue
            if line.endswith("har föredragit målet") or line.startswith("Föredragande har varit "):
                domare = True # next line will contain what we want
            elif domare:
                return self.clean_name(line)

        # third strategy: If only referent is given, try to detect the
        # non-titled name and assume that one. c.f 29529, eg
        #
        # Mikael Ocklind      Sonja Huldén
        #                     referent
        #
        # => Mikael Ocklind
        domare = False
        for line in reversed(trailing):
            line = self.clean_line(line)
            if line.lower() in ("referent"):
                # next one will contain names
                domare = True
            elif domare:
                # should be at least two names
                if len(line.split()) < 4:
                    self.log.warning("%s doesn't contain two+ names" % line)
                    return None
                else:
                    return self.clean_name(line, 2)[0]
        
        # fourth strategy: "Rådmannen Magnus Isgren har fattat beslutet. Föredragande jurist har varit"
        for line in trailing:
            m = re.match(r"(Rådmannen|Förvaltningsrättsfiskalen) (.*?) har (fattat beslutet|avgjort målet)", line)
            if m:
                return self.clean_name(m.group(2))

    def detect_klagandetyp(self, contact):
        # returns "myndighet", "leverantör", or None
        for line in contact:
            if line.endswith("kommun"):
                return "myndighet"
            elif line.endswith(" AB"):
                return "leverantör"
        return None


    def find_headsection(self, page, heading, startswith=False, bbheight=0.75):
        result = []
        started = False
        textboxiter = page.boundingbox(0, 0, page.height*bbheight, page.width) if bbheight < 1 else page
        for textbox in textboxiter:
            strtextbox = str(textbox).strip()
            if not started:
                if startswith:
                    strtextbox = strtextbox[:len(heading)]
                # adjust fuzziness aspect depending on how confident the OCR process is
                if hasattr(textbox, 'confidence'):
                    # transform confidence into cutoff with
                    # compression so that confidence 0 => cutoff .5,
                    # confidence 50 => cutoff .75, confidence 100 =>
                    # cutoff 1)
                    cutoff = (textbox.confidence/2+50)/100
                    f = difflib.get_close_matches(strtextbox, [heading], 1, cutoff)
                    if f and f[0] == heading:
                        started = True
                        if strtextbox != heading:
                            self.log.warning("Accepting %r instead of %r (confidence %.2f, cutoff %.3f)" % (strtextbox, heading, textbox.confidence, cutoff))
                elif strtextbox == heading:
                    started = True
            else:
                # when we find the next headsection, we're done
                if strtextbox.isupper() and len(strtextbox) > 4:
                    return result
                else:
                    result.append(strtextbox)
        if result:
            self.log.debug("Possible non-finished headsection %s: %s...%s" % (heading, result[0], result[-1]))
            return result

    def get_parser(self, basefile, sanitized, initialstate=None, parseconfig="default"):
        def kkv_parser(pdfreader):
            assert isinstance(pdfreader, PDFReader), "Unexpected: %s is not PDFReader" % type(pdfreader)
            if self._remove_overklagandehanvisning and len(pdfreader) > 1 : # eg not for HFD verdicts or one-pagers (eg avskrivningar)
                # check if we have annotated the correct idx for this basefile
                if self.get_parse_options(basefile)['overklagandeidx']:
                    idx = self.get_parse_options(basefile)['overklagandeidx']
                else:
                    # start by remove overklagandehanvisning and all
                    # subsequent pages
                    for idx, page in enumerate(pdfreader):
                        if self.is_overklagandehanvisning(page, idx):
                            break
                if idx:
                    # sanity check: should be max three pages left
                    if len(pdfreader) - idx <= 4:
                        self.log.info("%s: Page %s is överklagandehänvisning, skipping this and all following pages" % (basefile, idx+1))
                        pdfreader[:] = pdfreader[:idx]
                    else:
                        # more than four pages left -- probably an
                        # appendix (like the lower level court
                        # verdict) comes after. Let's just eliminate
                        # this specific page
                        self.log.info("%s: Page %s out of %s is överklagandehänvisning, skipping this page only" % (basefile, idx+1, len(pdfreader)))
                        pdfreader[:] = pdfreader[:idx] + pdfreader[idx+1:]
                else:
                    self.log.warning("%s: Couldn't find överklagandehänvisning" % basefile)

            # find crap
            kwargs = {}
            if self.get_parse_options(basefile)['bbheight']:
                kwargs['bbheight'] = self.get_parse_options(basefile)['bbheight']
            sokande = self.find_headsection(pdfreader[0], "SÖKANDE", **kwargs)
            if sokande:
                klagande = None
                # print(",".join(sokande))
                sokandeombud = self.detect_ombud(sokande)
                if sokandeombud:
                    self.log.info("Sökandeombud: " + sokandeombud)
                    pdfreader[0].insert(0, Meta([sokandeombud], predicate=RINFOEX.sokandeombud))
            else:
                klagande = self.find_headsection(pdfreader[0], "KLAGANDE", **kwargs)
                if klagande:
                    klagandeombud = self.detect_ombud(klagande)
                    if klagandeombud:
                        self.log.info("Klagandeombud: " + klagandeombud)
                        pdfreader[0].insert(0, Meta([klagandeombud], predicate=RINFOEX.klagandeombud))
                    klagandetyp = self.detect_klagandetyp(klagande)
                    self.log.info("Klagandetyp: %s" % klagandetyp)
                    if klagandetyp:
                        pdfreader[0].insert(0, Meta([klagandetyp], predicate=RINFOEX.klagandetyp))

            motpart = self.find_headsection(pdfreader[0], "MOTPART", **kwargs)
            if motpart:
                # print(",".join(motpart))
                motpartsombud = self.detect_ombud(motpart)
                if motpartsombud:
                    self.log.info("Motpartsombud: " + motpartsombud)
                    pdfreader[0].insert(0, Meta([motpartsombud], predicate=RINFOEX.motpartsombud))

            lastidx = -1
            while not re.search("\w\w+", pdfreader[lastidx].as_plaintext()):
                lastidx -= 1
            # FIXME: Find better heuristic for HFD
            trailing = self.find_headsection(pdfreader[lastidx], "HUR MAN ÖVERKLAGAR", startswith=True, bbheight=1)
            if trailing:
                domare = self.detect_domare(trailing)
                if not domare:
                    self.log.warning("%s: Can't detect domare in '%s'" % (basefile, ", ".join(trailing)))
                else:
                    self.log.info("Domare: %s" % domare)
                    pdfreader[0].insert(0, Meta([domare], predicate=RINFOEX.domare))

            import json
            tmp = json.dumps({"basefile": basefile, "trailing": trailing, "klagande": klagande, "sokande": sokande, "motpart": motpart}) + "\n"
            with open("tmp.txt", "a") as fp:
                fp.write(tmp)
            return pdfreader
        return kkv_parser


    @action
    def parsetest(self, testfile, outfile, basefile=None):
        """Run only the extraction parts on a specially prepared textfile"""
        import json
        d = Counter()
        o = Counter()
        totalcnt = 0
        trailcnt = 0
        domarecnt = 0
        with open(testfile) as fp:
            with open(outfile, "w") as ofp:
                for line in fp:
                    totalcnt += 1
                    data = json.loads(line)
                    if basefile and basefile != data['basefile']:
                        continue
                    if data['trailing']:
                        trailcnt += 1
                    domare = self.detect_domare(data['trailing']) if data['trailing'] else None
                    klagandeombud = self.detect_ombud(data['klagande']) if data['klagande'] else None
                    klagandetyp = self.detect_klagandetyp(data['klagande']) if data['klagande'] else None
                    sokandeombud = self.detect_ombud(data['sokande']) if data['sokande'] else None
                    motpartsombud = self.detect_ombud(data['motpart']) if data['motpart'] else None
                    outdata = json.dumps({'basefile': data['basefile'],
                                          'domare': domare,
                                          'klagandeombud': klagandeombud,
                                          'klagandetyp': klagandetyp,
                                          'sokandeombud': sokandeombud,
                                          'motpartsombud': motpartsombud})
                    ofp.write(outdata+"\n")
                    if basefile:
                        print(outdata)
                    else:
                        if domare:
                            domarecnt += 1
                            d[domare] += 1
                        if klagandeombud:
                            o[klagandeombud] += 1
                        if sokandeombud:
                            o[sokandeombud] += 1
                        if motpartsombud:
                            o[motpartsombud] += 1
        if not basefile:
            from pprint import pprint
            pprint(d.most_common())
            pprint(o.most_common())
            print("Total: %s, trailing: %s, domare: %s" % (totalcnt, trailcnt, domarecnt))

    def normalize_ombud(self, ombud):
        # 'Ombud: Advokat Anders Nilsson, Advokatfirman Lindahl KB' -> 'Advokatfirman Lindahl KB'
        if ombud.startswith("Ombud: ") and ", " in ombud:
            ombud = ombud.split(", ", 1)[1]
        # remove parts of a company name that often gets reported inconsistently
        # Advokatfirman Glimstedt Jönköping AB -> Glimstedt
        locations = ("Sverige", "Stockholm", "Göteborg", "Malmö", "Jönköping", "Helsingborg", "Växjö")
        remove = ("Ombud:", "Advokatfirma", "Advokatfirman", "Advokatbyrå", "Advokatbyrån", "AB", "HB", "KB", "i", "KONKURRENSVERKET") + locations
        parts = [part for part in ombud.split() if part not in remove]
        return " ".join(parts)

    def postprocess_doc(self, doc):
        super(KKV, self).postprocess_doc(doc)
        if getattr(doc.body, 'tagname', None) != "body":
            doc.body.tagname = "body"
        doc.body.uri = doc.uri
        page = doc.body[0]
        for node in page:
            if isinstance(node, Meta):
                doc.meta.add((URIRef(doc.uri), node.predicate, Literal(node[0])))
                page.remove(node)
        d = doc.meta.value(URIRef(doc.uri), RPUBL.avgorandedatum)

    def create_external_resources(self, doc):
        # avoid flyspeck size fonts from the tesseracted material
        for spec in doc.body.fontspec.values():
            if spec['size'] < 11:
                spec['size'] = 11
        return super(KKV, self).create_external_resources(doc)
