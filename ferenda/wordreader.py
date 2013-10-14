# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from tempfile import mktemp
import logging
import os
import textwrap
import xml.etree.cElementTree as ET
import zipfile
from datetime import datetime
from time import mktime

from ferenda import errors, util


class WordReader(object):

    """Reads .docx and .doc-files (the latter with support from `antiword
    <http://www.winfield.demon.nl/>`_) and presents a slightly easier
    API for dealing with them.

    """

    log = logging.getLogger(__name__)

    def read(self, wordfile, intermediatefile):
        """Converts the word file to a more easily parsed format.

        :param wordfile: Path to original docfile
        :param intermediatefile: Where to store the more parseable file
        :returns: name of parseable file, filetype (either "doc" or "docx")
        :rtype: tuple

        """
        filetype = "docx" if wordfile.endswith("docx") else "doc"

        # Parsing is a two step process: First extract some version of
        # the text from the binary blob (either through running
        # antiword for old-style doc documents, or by unzipping
        # document.xml, for new-style docx documents)
        if not os.path.exists(intermediatefile):
            if filetype == "docx":
                self.word_to_ooxml(wordfile, intermediatefile)
            else:
                try:
                    self.word_to_docbook(wordfile, intermediatefile)
                except errors.ExternalCommandError:
                    # Some .doc files are .docx with wrong suffix
                    self.log.info("%s: Retrying as OOXML" % wordfile)
                    self.word_to_ooxml(wordfile, intermediatefile)
                    filetype = "docx"
        else:
            # FIXME: sniff the intermediatefile to see if its a
            # docbook or a OOXML file
            pass
        return (intermediatefile, filetype)

    def word_to_docbook(self, indoc, outdoc):
        """Convert a old Word document (.doc) to a pseudo-docbook file through antiword."""
        tmpfile = mktemp()
        indoc = os.path.normpath(indoc)
        wrapper = textwrap.TextWrapper(break_long_words=False,
                                       width=72)

        util.ensure_dir(outdoc)
        if " " in indoc:
            indoc = '"%s"' % indoc
        cmd = "antiword -x db %s > %s" % (indoc, tmpfile)
        self.log.debug("Executing %s" % cmd)
        (ret, stdout, stderr) = util.runcmd(cmd)

        if ret != 0:
            self.log.error("Docbook conversion failed: %s" % stderr)
            raise errors.ExternalCommandError(
                "Docbook conversion failed: %s" % stderr.strip())

        tree = ET.parse(tmpfile)
        for element in tree.getiterator():
            if element.text and element.text.strip() != "":
                replacement = ""
                for p in element.text.split("\n"):
                    if p:
                        replacement += wrapper.fill(p) + "\n\n"

                element.text = replacement.strip()

        tree.write(outdoc, encoding="utf-8")
        os.unlink(tmpfile)

    def word_to_ooxml(self, indoc, outdoc):
        """Extracts the raw OOXML file from a modern Word document (.docx)."""
        name = "word/document.xml"
        zipf = zipfile.ZipFile(indoc, "r")
        assert name in zipf.namelist(), "No %s in zipfile %s" % (name, indoc)
        data = zipf.read(name)
        util.ensure_dir(outdoc)
        with open(outdoc, "wb") as fp:
            fp.write(data)

        # FIXME: We need to reimplement this old function (which ran
        # tidy on the outfile) with an internal lxml based thingy
        # util.indent_xml_file(outdoc)
        zi = zipf.getinfo(name)
        dt = datetime(*zi.date_time)
        ts = mktime(dt.timetuple())
        os.utime(outdoc, (ts, ts))

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
