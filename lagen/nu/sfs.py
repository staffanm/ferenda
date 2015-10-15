# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import os
import re
import shutil
from datetime import datetime

from rdflib import URIRef
from rdflib.namespace import DCTERMS, OWL
from ferenda.sources.legal.se import RPUBL, RINFOEX

from ferenda import decorators, util
from ferenda import TextReader, DocumentEntry, Describer
from ferenda.sources.legal.se import SFS as OrigSFS
from ferenda.sources.legal.se.elements import (Kapitel, Paragraf, Rubrik,
                                               Stycke, Listelement,
                                               Overgangsbestammelse, Bilaga,
                                               Avdelning)
from . import SameAs


class SFS(OrigSFS, SameAs):
    # consider moving facets() and tabs() from OrigSFS to this
    ordinalpredicates = {
        Kapitel: "rpubl:kapitelnummer",
        Paragraf: "rpubl:paragrafnummer",
        Rubrik: "rinfoex:rubriknummer",
        Stycke: "rinfoex:styckenummer",
        Listelement: "rinfoex:punktnummer",
        Overgangsbestammelse: "rinfoex:andringsforfattningnummer",
        Bilaga: "rinfoex:bilaganummer",
        Avdelning: "rinfoex:avdelningnummer"
    }

    def _makeimages(self):
        # FIXME: make sure a suitable font exists
        font = "Helvetica"

        def makeimage(basename, label):
            filename = "res/img/sfs/%s.png" % basename
            if not os.path.exists(filename):
                util.ensure_dir(filename)
                self.log.info("Creating img %s with label %s" %
                              (filename, label))
                cmd = 'convert -background transparent -fill Grey -font %s -pointsize 10 -size 44x14 -gravity East label:"%s " %s' % (font, label, filename)
                util.runcmd(cmd)
            return filename
        ret = []
        for i in range(1, 150):
            for j in ('', 'a', 'b'):
                ret.append(makeimage("K%d%s" % (i, j), "%d%s kap." % (i, j)))
        for i in range(1, 100):
            ret.append(makeimage("S%d" % i, "%d st." % i))
        return ret

    def infer_metadata(self, resource, basefile):
        sameas_uri = self.sameas_minter.space.coin_uri(resource)
        resource.add(OWL.sameAs, URIRef(sameas_uri))
        # then find each rpubl:konsolideringsunderlag, and create
        # owl:sameas for them as well
        for subresource in resource.objects(RPUBL.konsolideringsunderlag):
            uri = self.sameas_minter.space.coin_uri(subresource)
            subresource.add(OWL.sameAs, URIRef(uri))
        desc = Describer(resource.graph, resource.identifier)
        de = DocumentEntry(self.store.documententry_path(basefile))
        if de.orig_updated:
            desc.value(RINFOEX.senastHamtad, de.orig_updated)
        if de.orig_checked:
            desc.value(RINFOEX.senastKontrollerad, de.orig_checked)
        v = self.commondata.value(resource.identifier,
                                  DCTERMS.alternate, any=True)
        if v:
            desc.value(DCTERMS.alternate, v)


    @decorators.action
    def importarchive(self, archivedir):
        """Imports downloaded data from an archive from legacy lagen.nu data.

        In particular, creates proper archive storage for older
        versions of each text.

        """
#        import tarfile
#        if not tarfile.is_tarfile(archivefile):
#            self.log.error("%s is not a readable tarfile" % archivefile)
#            return
#        t = tarfile.open(archivefile)
#        for ti in t.getmembers():
#            if not ti.isfile():
#                continue
#            f = ti.name
        current = archived = 0
        for f in util.list_dirs(archivedir, ".html"):
            if not f.startswith("downloaded/sfs"):  # sfst or sfsr
                continue
            for regex in self.templ:
                m = re.match(regex, f)
                if not m:
                    continue
                if "vcheck" in m.groupdict():  # silently ignore
                    break
                basefile = "%s:%s" % (m.group("byear"), m.group("bnum"))

                # need to look at the file to find out its version
                # text = t.extractfile(f).read(4000).decode("latin-1")
                text = open(f).read(4000).decode("latin-1")
                reader = TextReader(string=text)
                updated_to = self._find_uppdaterad_tom(basefile,
                                                       reader=reader)

                if "vyear" in m.groupdict():  # this file is marked as
                                              # an archival version
                    archived += 1
                    version = updated_to

                    if m.group("vyear") == "first":
                        pass
                    else:
                        exp = "%s:%s" % (m.group("vyear"), m.group("vnum"))
                        if version != exp:
                            self.log.warning("%s: Expected %s, found %s" %
                                             (f, exp, version))
                else:
                    version = None
                    current += 1
                    de = DocumentEntry()
                    de.basefile = basefile
                    de.id = self.canonical_uri(basefile, updated_to)
                    # fudge timestamps best as we can
                    de.orig_created = datetime.fromtimestamp(os.path.getctime(f))
                    de.orig_updated = datetime.fromtimestamp(os.path.getmtime(f))
                    de.orig_updated = datetime.now()
                    de.orig_url = self.document_url_template % locals()
                    de.published = datetime.now()
                    de.url = self.generated_url(basefile)
                    de.title = "SFS %s" % basefile
                    # de.set_content()
                    # de.set_link()
                    de.save(self.store.documententry_path(basefile))
                    
                # this yields more reasonable basefiles, but they are not
                # backwards compatible -- skip them for now
                # basefile = basefile.replace("_", "").replace(".", "")

                if "type" in m.groupdict() and m.group("type") == "sfsr":
                    dest = self.store.register_path(basefile)
                    current -= 1  # to offset the previous increment
                else:
                    dest = self.store.downloaded_path(basefile, version)

                self.log.debug("%s: extracting %s to %s" % (basefile, f, dest))

                #with openmethod(basefile, mode="wb", version=version) as fp:
                #    fp.write(t.extractfile(f).read())
                util.ensure_dir(dest)
                shutil.copy2(f, dest)
                break
                    
            else:
                self.log.warning("Couldn't process %s" % f)


        self.log.info("Extracted %s current versions and %s archived versions" % (current, archived))
    

            
