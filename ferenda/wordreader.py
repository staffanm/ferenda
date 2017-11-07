# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

from datetime import datetime
from tempfile import mktemp
from time import mktime
import codecs
import logging
import os
import textwrap
import xml.etree.cElementTree as ET
from lxml import etree
import zipfile
from io import BytesIO

import bs4

from ferenda import errors, util
from ferenda import ResourceLoader

class WordReader(object):

    """Reads .docx and .doc-files (the latter with support from `antiword
    <http://www.winfield.demon.nl/>`_) and converts them to a XML form
    that is slightly easier to deal with.

    """

    log = logging.getLogger(__name__)

    def read(self, wordfile, intermediatefp, simplify=True):
        """Converts the word file to a more easily parsed format.

        :param wordfile: Path to original docfile
        :param intermediatefp: An open filehandle to write the more parseable file to
        :returns: filetype (either "doc" or "docx")
        :rtype: str

        """
        # guess at filetype. note that file suffixes are not always truthful!
        filetype = "docx" if wordfile.endswith("docx") else "doc"

        # Parsing is a two step process: First extract some version of
        # the text from the binary blob (either through running
        # antiword for old-style doc documents, or by unzipping
        # document.xml, for new-style docx documents)
        if "r" in intermediatefp.mode:
            # sniff the intermediate to see if its a
            # docbook or a OOXML file
            start = intermediatefp.read(1024)
            intermediatefp.seek(0)
            if 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"':
                filetype = "docx" 
        else:
            if filetype == "docx":
                self.word_to_ooxml(wordfile, intermediatefp, simplify)
            else:
                try:
                    self.word_to_docbook(wordfile, intermediatefp)
                except errors.ExternalCommandError as e:
                    if "not a Word Document" in str(e):
                        # Some .doc files are .docx with wrong suffix
                        self.log.info("%s: Retrying as OOXML" % wordfile)
                        self.word_to_ooxml(wordfile, intermediatefp, simplify)
                        filetype = "docx"
                    else:
                        raise e
        return filetype

    def word_to_docbook(self, indoc, outfp):
        """Convert a old Word document (.doc) to a pseudo-docbook file through antiword."""
        tmpfile = mktemp()
        indoc = os.path.normpath(indoc)
        wrapper = textwrap.TextWrapper(break_long_words=False,
                                       width=72)
        if " " in indoc:
            indoc = '"%s"' % indoc
        cmd = "antiword -x db %s > %s" % (indoc, tmpfile)
        # make sure HOME is set even on win32 -- antiword seems to require it?
        if 'HOME' not in os.environ and 'USERPROFILE' in os.environ:
            os.environ['HOME'] = os.environ['USERPROFILE']

        self.log.debug("Executing %s" % cmd)
        (ret, stdout, stderr) = util.runcmd(cmd)

        if ret != 0:
            self.log.error("Docbook conversion failed: %s" % stderr)
            raise errors.ExternalCommandError(
                "Docbook conversion failed: %s" % stderr.strip())

        # wrap long lines in the docbook output. Maybe should be configurable?
        tree = ET.parse(tmpfile)
        if hasattr(tree, 'iter'):
            iterator = tree.iter()
        else:
            # Python 2.6 way -- results in a PendingDeprecationWarning
            # on newer pythons.
            iterator = tree.getiterator() 
        for element in iterator:
            if element.text and element.text.strip() != "":
                replacement = ""
                for p in element.text.split("\n"):
                    if p:
                        replacement += wrapper.fill(p) + "\n\n"

                element.text = replacement.strip()
        tree.write(outfp, encoding="utf-8")
        os.unlink(tmpfile)

    def word_to_ooxml(self, indoc, outfp, simplify):
        """Extracts the raw OOXML file from a modern Word document (.docx)."""
        name = "word/document.xml"
        zipf = zipfile.ZipFile(indoc, "r")
        assert name in zipf.namelist(), "No %s in zipfile %s" % (name, indoc)
        data = zipf.read(name)
        if simplify:
            data = self._merge_ooxml(self._simplify_ooxml(data)).encode("utf-8")
        outfp.write(data)
        zi = zipf.getinfo(name)
        dt = datetime(*zi.date_time)
        ts = mktime(dt.timetuple())
        outfp.utime = ts

    def _simplify_ooxml(self, data, pretty_print=True):
        # simplify the horrendous mess that is OOXML through
        # simplify-ooxml.xsl. Returns a formatted XML stream as a
        # bytestring.

        # in some rare cases, the value \xc2\x81 (utf-8 for
        # control char) is used where "Å" (\xc3\x85) should be
        # used. 
        if b"\xc2\x81" in data:
            self.log.warning("Working around control char x81 in text data")
            data = data.replace(b"\xc2\x81", b"\xc3\x85")
        intree = etree.parse(BytesIO(data))
            # intree = etree.parse(fp)
        if not hasattr(self, 'ooxml_transform'):
            fp = ResourceLoader().openfp("xsl/simplify-ooxml.xsl")
            self.ooxml_transform = etree.XSLT(etree.parse(fp))
        fp.close()
        resulttree = self.ooxml_transform(intree)
        return etree.tostring(
            resulttree,
            pretty_print=pretty_print,
            encoding="utf-8")

    def _merge_ooxml(self, data):
        # this is a similar step to _simplify_ooxml, but merges w:p
        # elements in a BeautifulSoup tree. This step probably should
        # be performed through XSL and be put in _simplify_ooxml as
        # well.
        # The soup now contains a simplified version of OOXML where
        # lot's of nonessential tags has been stripped. However, the
        # central w:p tag often contains unneccessarily splitted
        # subtags (eg "<w:t>Avgörand</w:t>...<w:t>a</w:t>...
        # <w:t>tum</w:t>"). Attempt to join these
        #
        # FIXME: This could be a part of simplify_ooxml instead.
        soup = bs4.BeautifulSoup(data, "lxml")
        for p in soup.find_all("w:p", limit=2147483647):
            current_r = None
            for r in p.find_all("w:r", limit=2147483647):
                # find out if formatting instructions (bold, italic)
                # are identical
                if current_r and current_r.find("w:rpr") == r.find("w:rpr"):
                    # ok, merge
                    ts = list(current_r.find_all("w:t", limit=2147483647))
                    assert len(ts) == 1, "w:r should not contain exactly one w:t"
                    ns = ts[0].string
                    ns.replace_with(str(ns) + r.find("w:t").string)
                    r.decompose()
                else:
                    current_r = r
        # make sure output is pretty-printed
        return soup.find("w:document").prettify()


# hard to test, hard to get working, will always be platform
# dependent, but saved here for posterity
#
#    def word_to_html(indoc, outhtml):
#        """Converts a word document (any version) to a HTML document by remote
#        controlling Microsoft Word to open and save the doc as HTML.
#
#        .. note::
#
#           This only works on a Win32 system with Office 2003 installed
#        """
#        indoc = os.path.join(os.getcwd(), indoc.replace("/", os.path.sep))
#        outhtml = os.path.join(os.getcwd(), outhtml.replace("/", os.path.sep))
#        display_indoc = indoc[len(os.getcwd()):].replace(os.path.sep, "/")
#        display_outhtml = outhtml[len(os.getcwd()):].replace(os.path.sep, "/")
#        ensure_dir(outhtml)
#        if not os.path.exists(indoc):
#            print(("indoc %s does not exists (seriously)" % indoc))
#        if os.path.exists(outhtml):
#            return
#        from win32com.client import Dispatch
#        import pywintypes
#        wordapp = Dispatch("Word.Application")
#        if wordapp is None:
#            print("Couldn't start word")
#            return
#        try:
#            wordapp.Documents.Open(indoc)
#            wordapp.Visible = False
#            doc = wordapp.ActiveDocument
#            doc.SaveAs(outhtml, 10)  # 10 = filtered HTML output
#            doc.Close()
#            doc = None
#            wordapp.Quit
#        except pywintypes.com_error as e:
#            print(("Warning: could not convert %s" % indoc))
#            print((e[2][2]))
#            errlog = open(outhtml + ".err.log", "w")
#            errlog.write("%s:\n%s" % (indoc, e))
