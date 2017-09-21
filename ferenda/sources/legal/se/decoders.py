# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import re

from lxml import etree

from ferenda.pdfreader import BaseTextDecoder, Textelement
from ferenda import errors

class OffsetDecoder1d(BaseTextDecoder):

    low_offset = 0x1d
    high_offset = 0x7a
    unmapped = []
    def __init__(self, dummy=None):
        """Decoder for most PDFs with custom encoding coming from
        Regeringskansliet.
        
        Basic ASCII characters are coded in the same order as ascii,
        but with a 0x1d offset.

        """
        self.map = self.encodingmap(self.low_offset, self.high_offset, self.unmapped)
        self.re_xmlcharref = re.compile("&#\d+;")

    def encodingmap(self, low_offset, high_offset,unmapped):
        customencoding_map = {}
        for i in range(0x20, 0x7e):
            customencoding_map[i - low_offset] = i

        for i in unmapped:
            customencoding_map[i] = i

        # assume that the rest is coded using windows-1252 but with a 0x7a
        # offset. We have no basis for this assumption.
        for i in range(0x80, 0xff):
            if i - high_offset in customencoding_map:
                # print("would have mapped %s to %s but %s was already in customencoding_map" %
                #       (chr(i - 0x7a), chr(i), chr(customencoding_map[i - 0x7a])))
                pass
            else:
                customencoding_map[i - high_offset] = i
        return customencoding_map

    def decode_string(self, s, encoding_map):
        s = self.re_xmlcharref.sub(lambda m: chr(int(m.group(0)[2:-1])), s)
        return s.translate(encoding_map)

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
                textbox[idx] = Textelement(self.decode_string(subpart, self.map),
                                           tag=subpart.tag)
        return textbox

    def fontspec(self, fontspec):
        # Fonts in Propositioner get handled wierdly by pdf2xml --
        # sometimes they come out as "Times New Roman,Italic",
        # sometimes they come out as "TimesNewRomanPS-ItalicMT". Might
        # be caused by differences in the tool chain that creates the
        # PDFs.  Sizes seem to be consistent though. This maps them to
        # be more consistent. NOTE: This might be totally unneccesary
        # now that we use PDFAnalyzer to determine likely fonts for
        # headers etc.
        if 'family' in fontspec:
            # Times New Roman => TimesNewRomanPSMT
            # Times New Roman,Italic => TimesNewRomanPS-ItalicMT
            if fontspec['family'] == "Times New Roman":
                fontspec['family'] = "TimesNewRomanPSMT"
            if fontspec['family'] == "Times New Roman,Italic":
                fontspec['family'] = "TimesNewRomanPS-ItalicMT"
            # Not 100% sure abt these last two
            if fontspec['family'] == "Times New Roman,Bold":
                fontspec['family'] = "TimesNewRomanPS-BoldMT"
            if fontspec['family'] == "Times New Roman,BoldItalic":
                fontspec['family'] = "TimesNewRomanPS-BoldItalicMT"
            # only found in sou 2003:129 -- uses totally different
            # family name for superscripts, but in reality is same
            # font.
            if fontspec['family'] == "TTA1o00":  
                fontspec['family'] = "TT5Eo00"
        return fontspec
        
        

class OffsetDecoder20(OffsetDecoder1d):
    """Alternate decoder for some PDFs (really only Prop. 1997/98:44
    discovered so far). Custom fields here have characters generally
    shifted 0x20, spaces are as-is, etc. It's also common with
    Textelements that are partially encoded (usually text rendered in
    bold is encoded, but normal text is unencoded -- for some reason
    pdftohtml renders this as a single textelement).

    """

    low_offset = 0x20
    high_offset = 0x40
    unmapped = [0x20]
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
                textbox[0] = Textelement(self.decode_string(orig[:boundary], self.map), tag="b")
                textbox.insert(1, Textelement(orig[boundary:], tag=None))
                # Find the id for the "real" non-bold font. I think
                # that in every known case the fontid should simply be
                # the default font (id=0). Maybe we could hardcode
                # that right away, like we hardcode the font family
                # name right now.
                newfontid = self.find_fontid(fontspecs, "Times-Roman", textbox.font.size)
                expected_length = 2
            else:
                textbox[0] = Textelement(self.decode_string(textbox[0], self.map), tag=textbox[0].tag)
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
            if newfontid != textbox.fontid:
                # invalidate the cached property
                del textbox.__dict__['font']
                textbox.fontid = newfontid
        else:
            textbox = super(OffsetDecoder20, self).__call__(textbox, fontspecs)
            # again, if one or more textelements have an "i" tag, the
            # font for the entire textbox probably shouldn't be
            # specced as an italic ("Kursiv")
            if textbox.font.family == "Times.New.Roman.Kursiv0104" and "i" in [x.tag for x in textbox]:
                newfontid = self.find_fontid(fontspecs, "Times-Roman", textbox.font.size)
                # invalidate the cached property
                del textbox.__dict__['font']
                textbox.fontid = newfontid
        return textbox

    def find_fontid(self, fontspecs, family, size):
        for fontid, fontspec in fontspecs.items():
            if fontspec['family'] == family and fontspec['size'] == size:
                return fontid
        else:
             raise KeyError("No fontspec matching (%s, %s) found" % (family, size))

# this decoder has a special analyze_font(fontid, sample) method that
# is called with a selection of textboxes using that font, and records
# through language detection whether that font uses encoding or not.
from langdetect import detect
from langdetect.lang_detect_exception import LangDetectException

class DetectingDecoder(OffsetDecoder1d):
    def __init__(self, dummy=None):
        super(DetectingDecoder, self).__init__(dummy)
        self.encodingmaps = {}
        
    def analyze_font(self, fontid, samples):
        sampletext = ""

        # very involved way of getting a representative sample, since
        # an encoded font can be partially unencoded...
        for textbox in samples:
            decode_all = not('i' in [getattr(x, 'tag', None) for x in textbox])
            if decode_all:
                sampletext += etree.tostring(textbox, method="text",
                                             encoding="utf-8").decode("utf-8")
            else:
                for subpart in textbox:
                    if (isinstance(subpart, etree._Element) and
                        (decode_all or subpart.tag == 'i')):
                        if subpart.text: # it might be None, for eg "<i><b>text is in child instead</b></i>"
                            sampletext += subpart.text

        for low_offset, high_offset, unmapped in ((0,0, []),
                                                  (0x1d, 0x7a, []),
                                                  (0x20, 0x40, [0x20])):
            if low_offset and high_offset:
                encodingmap = self.encodingmap(low_offset, high_offset, unmapped)
                decoded_sample = self.decode_string(sampletext, encodingmap)
            else:
                encodingmap = None
                decoded_sample = sampletext
            try:
                lang = detect(decoded_sample)
                if lang == 'sv':
                    self.encodingmaps[int(fontid)] = encodingmap
                    return low_offset # used for diagnostic logging
            except LangDetectException:
                pass
        raise errors.PDFDecodeError("cannot detect how to decode font %s using %r" %
                                    (fontid, sampletext))


    def __call__(self, textbox, fontspecs):
        if 'encoding' not in fontspecs[textbox.fontid]:  # only for some testcases
            return textbox
        if (fontspecs[textbox.fontid]['encoding'] != "Custom" or
            self.encodingmaps.get(textbox.fontid) is None):
            return textbox
        # NOTE: This weird checking for occurrences of 'i'
        # tags is needed for functionalSources.
        # TestPropRegeringen.test_parse_1999_2000_17 to pass
        # (and matches encoding usage in practice)
        decode_all = not('i' in [getattr(x, 'tag', None) for x in textbox])
        for idx, subpart in enumerate(textbox):
            if (isinstance(subpart, Textelement) and
                (decode_all or subpart.tag == 'i')):
                textbox[idx] = Textelement(self.decode_string(subpart, self.encodingmaps[textbox.fontid]),
                                           tag=subpart.tag)
        return textbox
