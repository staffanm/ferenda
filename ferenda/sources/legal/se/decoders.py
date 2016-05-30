# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import re

from ferenda.pdfreader import BaseTextDecoder, Textelement


class OffsetDecoder1d(BaseTextDecoder):

    def __init__(self, dummy=None):
        """Decoder for most PDFs with custom encoding coming from
        Regeringskansliet.
        
        Basic ASCII characters are coded in the same order as ascii,
        but with a 0x1d offset.

        """
        self.map = self.encodingmap()
        self.re_xmlcharref = re.compile("&#\d+;")

    def encodingmap(self):
        customencoding_map = {}
        for i in range(0x20, 0x7e):
            customencoding_map[i - 0x1d] = i
            # assume that the rest is coded using windows-1252 but with a 0x7a
            # offset. We have no basis for this assumption.
            for i in range(0x80, 0xff):
                if i - 0x7a in customencoding_map:
                    # print("would have mapped %s to %s but %s was already in customencoding_map" %
                    #       (chr(i - 0x7a), chr(i), chr(customencoding_map[i - 0x7a])))
                    pass
                else:
                    customencoding_map[i - 0x7a] = i
        return customencoding_map

    def decode_string(self, s):
        s = self.re_xmlcharref.sub(lambda m: chr(int(m.group(0)[2:-1])), s)
        return s.translate(self.map)

    def __call__(self, textbox, fontspecs): 
        if 'encoding' not in fontspecs[textbox.fontid]:  # only for some testcases
            return textbox
        if fontspecs[textbox.fontid]['encoding'] != "Custom":
            return textbox
        # NOTE: This weird checking for occurrences of 'i'
        # tags is needed for functionalSources.
        # TestPropRegeringen.test_parse_1999_2000_17 to pass
        # (and matches encoding usage in practice)
        decode_all = not('i' in [getattr(x, 'tag', None) for x in textbox])
        for idx, subpart in enumerate(textbox):
            if (isinstance(subpart, Textelement) and
                (decode_all or subpart.tag == 'i')):
                textbox[idx] = Textelement(self.decode_string(subpart),
                                           tag=subpart.tag)
        return textbox

    def fontspecs(self, fontspecs):
        # Fonts in Propositioner get handled wierdly by pdf2xml --
        # sometimes they come out as "Times New Roman,Italic",
        # sometimes they come out as "TimesNewRomanPS-ItalicMT". Might
        # be caused by differences in the tool chain that creates the
        # PDFs.  Sizes seem to be consistent though. This maps them to
        # be more consistent. NOTE: This might be totally unneccesary
        # now that we use PDFAnalyzer to determine likely fonts for
        # headers etc.
        for key, val in fontspecs.items():
            if 'family' in val:
                # Times New Roman => TimesNewRomanPSMT
                # Times New Roman,Italic => TimesNewRomanPS-ItalicMT
                if val['family'] == "Times New Roman":
                    val['family'] = "TimesNewRomanPSMT"
                if val['family'] == "Times New Roman,Italic":
                    val['family'] = "TimesNewRomanPS-ItalicMT"
                # Not 100% sure abt these last two
                if val['family'] == "Times New Roman,Bold":
                    val['family'] = "TimesNewRomanPS-BoldMT"
                if val['family'] == "Times New Roman,BoldItalic":
                    val['family'] = "TimesNewRomanPS-BoldItalicMT"
        return fontspecs
        
        

class OffsetDecoder20(OffsetDecoder1d):
    """Alternate decoder for some PDFs (really only Prop. 1997/98:44
    discovered so far). Custom fields here have characters generally
    shifted 0x20, spaces are as-is, etc. It's also common with
    Textelements that are partially encoded (usually text rendered in
    bold is encoded, but normal text is unencoded -- for some reason
    pdftohtml renders this as a single textelement).

    """

    fixedleaders = ["(Skälen för r|R)egeringens (bedömning och förslag|bedömning|förslag):", "Remissinstanserna:"]
    
    def __init__(self, kommittenamn=None):
        super(OffsetDecoder20, self).__init__()
        self.reversemap = dict((v, k) for k, v in self.map.items())
        # remove some special regex chars from the backwards decoding
        # (eg. encoding) so that we can use regexes in
        # self.fixedleaders
        for c in '|()':
            self.reversemap[ord(c)] = ord(c)
        if kommittenamn:
            self.fixedleaders.append(kommittenamn + "s (bedömning och förslag|bedömning|förslag)")
        self.re_fixedleaders = re.compile("(%s)" % "|".join([self.encode_string(x) for x in self.fixedleaders]))

    def encode_string(self, s):
        s = s.translate(self.reversemap)
        newstring = ""
        for c in s:
            b = ord(c)
            if b < 0x20 and b not in (0x9, 0xa, 0xd):
                entity = "&#%s;" % b
                newstring += entity
            elif c in ("$"):
                newstring += "\\" + c
            else:
                newstring += c
        return newstring

    def encodingmap(self):
        customencoding_map = {}
        for i in range(0x20, 0x7e):
            customencoding_map[i - 0x20] = i

        # space is space
        customencoding_map[0x20] = 0x20

        # the rest is coded using windows-1252 but with a 0x40 offset.
        for i in range(0x80, 0xff):
            if i - 0x40 in customencoding_map:
                pass
            else:
                customencoding_map[i - 0x40] = i
        return customencoding_map


    def __call__(self, textbox, fontspecs):
        if fontspecs[textbox.fontid]['encoding'] != "Custom":
            return textbox
        if textbox.font.family == "Times.New.Roman.Fet0100":
            boundary = None
            # extra special hack for prop 1997/98:44 which has
            # textelements marked as having a font with custom
            # encoding, but where only the bolded part (which
            # isn't marked up...) is encoded, while the rest is
            # unencoded. The "g" is a encoded section sign, which
            # in these cases is the last encoded char.
            if (len(textbox[0].split(" ", 2)) == 3 and 
                textbox[0].split(" ", 2)[1] == "g"):
                boundary = textbox[0].index(" ", textbox[0].index(" ")+1)
            # a similar situation with paragraphs with leading bold
            # type, where the bold text is any of 3-4 fixed strings
            # (Note: the xml data doesn't contain any information
            # about the text being bold, or rather that the following
            # text is non-bold)
            else:
                m = self.re_fixedleaders.match(textbox[0])
                if m:
                    boundary = m.end()
            if boundary:
                orig = str(textbox[0])
                textbox[0] = Textelement(self.decode_string(orig[:boundary]), tag="b")
                textbox.insert(1, Textelement(orig[boundary:], tag=None))
                # Find the id for the "real" non-bold font. I think
                # that in every known case the fontid should simply be
                # the default font (id=0). Maybe we could hardcode
                # that right away, like we hardcode the font family
                # name right now.
                newfontid = self.find_fontid(fontspecs, "Times-Roman", textbox.font.size)
                expected_length = 2
            else:
                textbox[0] = Textelement(self.decode_string(textbox[0]), tag=textbox[0].tag)
                expected_length = 1
                newfontid = textbox.fontid
            if len(textbox) > expected_length: # the <text> element contained subelements
                # save and remove the 1-2 textelements we've processed
                decoded = textbox[:expected_length]
                textbox[:] = textbox[expected_length:]
                # do the default decoding
                textbox = super(OffsetDecoder20, self).__call__(textbox, fontspecs)
                # then add the previously procesed elements
                textbox[:] = decoded + textbox[:]
            textbox.fontid = newfontid
        else:
            textbox = super(OffsetDecoder20, self).__call__(textbox, fontspecs)
            # again, if one or more textelements have an "i" tag, the
            # font for the entire textbox probably shouldn't be
            # specced as an italic ("Kursiv")
            if textbox.font.family == "Times.New.Roman.Kursiv0104" and "i" in [x.tag for x in textbox]:
                textbox.fontid = self.find_fontid(fontspecs, "Times-Roman", textbox.font.size)
        return textbox

    def find_fontid(self, fontspecs, family, size):
        for fontid, fontspec in fontspecs.items():
            if fontspec['family'] == family and fontspec['size'] == size:
                return fontid
        else:
             raise KeyError("No fontspec matching (%s, %s) found" % (family, size))

