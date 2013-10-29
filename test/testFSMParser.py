# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import sys, os
from ferenda.compat import unittest
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

import codecs
import re
import tempfile

import six

from ferenda import elements
from ferenda.testutil import file_parametrize
from ferenda.compat import patch

# SUT
from ferenda import FSMParser, TextReader
from ferenda.fsmparser import Peekable
from ferenda.errors import FSMStateError



class TestPeekable(unittest.TestCase):
    def test_peekable(self):
        pk = Peekable(range(4))
        self.assertEqual(pk.peek(),0)
        self.assertEqual(pk.next(),0)
        self.assertEqual(pk.peek(),1)
        self.assertEqual(pk.next(),1)
        self.assertEqual(pk.next(),2)
        self.assertEqual(pk.next(),3)
        with self.assertRaises(StopIteration):
            self.assertEqual(pk.peek())
        with self.assertRaises(StopIteration):
            self.assertEqual(pk.next())

        # test __iter__
        pk = Peekable(range(4))
        self.assertEqual([0,1,2,3], list(pk))


class Parse(unittest.TestCase):
    def run_test_file(self, filename, debug=False):
        # some basic recognizers and constructors to parse a simple
        # structured plaintext format.
        #
        # RECOGNIZERS
        def is_header(parser):
            suspect = parser.reader.peek()
            return (len(suspect) > 100 and not suspect.endswith("."))

        def is_section(parser):
            (ordinal,title) = analyze_sectionstart(parser.reader.peek())
            return section_segments_count(ordinal) == 1

        def is_subsection(parser):
            (ordinal,title) = analyze_sectionstart(parser.reader.peek())
            return section_segments_count(ordinal) == 2

        def is_subsubsection(parser):
            (ordinal,title) = analyze_sectionstart(parser.reader.peek())
            return section_segments_count(ordinal) == 3

        def is_preformatted(parser):
            return "   " in parser.reader.peek()

        def is_definition(parser):
            return False

        def is_description(parser):
            return False

        def is_li_decimal(parser):
            listtype = analyze_listitem(parser.reader.peek())[0]
            return listtype in ('decimal','decimal-leading-zero')

        def is_li_alpha(parser):
            listtype = analyze_listitem(parser.reader.peek())[0]
            return listtype in ('lower-alpha','upper-alpha')

        def is_li_roman(parser):
            listtype = analyze_listitem(parser.reader.peek())[0]
            return listtype in ('lower-roman','upper-roman')

        def is_unordereditem(parser):
            listtype = analyze_listitem(parser.reader.peek())[0]
            return listtype in ('disc','circle','square','dash')

        def is_state_a(parser):
            return parser.reader.peek().startswith("State A:")

        def is_state_b(parser):
            return parser.reader.peek().startswith("State B:")

        def is_state_c(parser):
            return parser.reader.peek().startswith("State C:")
        
        def is_paragraph(parser):
            # c.f. test/files/fsmparser/invalid.txt
            return len(parser.reader.peek()) > 6

        # MAGIC
        def sublist_or_parent(symbol,state_stack):
            constructor = False
            newstate = None
            if symbol == is_li_alpha and "ol-alpha" not in state_stack: # maybe only check state_stack[-2]
                constructor = make_ol_alpha
                newstate = "ol-alpha"
            elif symbol == is_li_roman and "ol-roman" not in state_stack:
                constructor = make_ol_roman
                newstate = "ol-roman"
            elif symbol == is_li_decimal and "ol-decimal" not in state_stack:
                constructor = make_ol_roman
                newstate = "ol-roman"
            else:
                pass
            return (constructor,newstate)
        
        # CONSTRUCTORS
        def make_body(parser):
            parser._debug("Hello")
            b = elements.Body()
            return parser.make_children(b)
        setattr(make_body,'newstate','body')
        
        def make_section(parser):
            (secnumber, title) = analyze_sectionstart(parser.reader.next())
            s = elements.Section(ordinal=secnumber,title=title)
            return parser.make_children(s)
        setattr(make_section,'newstate','section')

        def make_subsection(parser):
            (secnumber, title) = analyze_sectionstart(parser.reader.next())
            s = elements.Subsection(ordinal=secnumber,title=title)
            return parser.make_children(s)
        setattr(make_subsection,'newstate','subsection')

        def make_subsubsection(parser):
            (secnumber, title) = analyze_sectionstart(parser.reader.next())
            s = elements.Subsubsection(ordinal=secnumber,title=title)
            return parser.make_children(s)
        setattr(make_subsubsection,'newstate','subsubsection')

        def make_paragraph(parser):
            return elements.Paragraph([parser.reader.next().strip()])

        def make_preformatted(parser):
            return elements.Preformatted([parser.reader.next()])

#        def make_unorderedlist(parser):
#            listtype = analyze_listitem(parser.reader.peek())[0]
#            assert ordinal is None
#            ul = elements.UnorderedList(type=listtype)
#            ul.append(parser.make_child(IN_UNORDEREDLIST)) # 1st element of list
#            return parser.make_children(ul)
#        setattr(make_unorderedlist,'newstate','unorderedlist')

        def make_ol_decimal(parser):
            return make_orderedlist(parser,"decimal","ol-decimal")
        setattr(make_ol_decimal,'newstate','ol-decimal')

        def make_ol_alpha(parser):
            return make_orderedlist(parser,"lower-alpha", "ol-alpha")
        setattr(make_ol_alpha,'newstate','ol-alpha')

        def make_ol_roman(parser):
            return make_orderedlist(parser,"lower-roman", "ol-roman")
        setattr(make_ol_roman,'newstate','ol-romal')

        def make_listitem(parser):
            chunk = parser.reader.next()
            (listtype,ordinal,separator,rest) = analyze_listitem(chunk)
            li = elements.ListItem(ordinal=ordinal)
            li.append(rest)
            return parser.make_children(li)
        setattr(make_listitem,'newstate','listitem')

        def make_state_a(parser):
            return elements.Paragraph([parser.reader.next().strip()],id="state-a")
        # setattr(make_state_a, 'newstate', 'state-a')

        def make_state_b(parser):
            return elements.Paragraph([parser.reader.next().strip()],id="state-b")
        # setattr(make_state_b, 'newstate', 'state-b')

        def make_state_c(parser):
            return elements.Paragraph([parser.reader.next().strip()],id="state-c")
        # setattr(make_state_c, 'newstate', 'state-c')
        
        # HELPERS
        def section_segments_count(s):
            return ((s is not None) and 
                    len(list(filter(None,s.split(".")))))

        def make_orderedlist(parser,listtype,childstate):
            listtype = analyze_listitem(parser.reader.peek())[0]
            ol = elements.OrderedList(type=listtype)
            ol.append(parser.make_child(make_listitem,"listitem"))
            return parser.make_children(ol)

        # matches
        # "1 Blahonga"
        # "1.2.3. This is a subsubsection"
        re_sectionstart = re.compile("^(\d[\.\d]*) +(.*[^\.])$").match
        def analyze_sectionstart(chunk):
            m = re_sectionstart(chunk)
            if m:
                return (m.group(1).rstrip("."), m.group(2).strip())
            else:
                return (None,chunk)

        def analyze_listitem(chunk):
            # returns: same as list-style-type in CSS2.1, sans
            # 'georgian', 'armenian' and 'greek', plus 'dashed'
            listtype = ordinal = separator = rest = None
            # match "1. Foo…" or "14) bar…" but not "4 This is a heading"
            m = re.match('^(\d+)([\.\)]) +',chunk)
            if m:
                if chunk.startswith("0"):
                    listtype="decimal-leading-zero"
                else:
                    listtype="decimal"
                (ordinal,separator) = m.groups()
                rest = chunk[m.end():]
                return (listtype,ordinal,separator,rest)

            # match "IX. Foo… or "vii) bar…" but not "vi is a sucky
            # editor" or "MMXIII is the current year"
            m = re.match('^([IVXivx]+)([\.\)]) +', chunk)
            if m:
                if chunk[0].islower():
                    listtype = 'lower-roman'
                else:
                    listtype = 'upper-roman'
                (ordinal,separator) = m.groups()
                rest = chunk[m.end():]
                return (listtype,ordinal,separator,rest)

            # match "a. Foo… or "z) bar…" but not "to. Next sentence…"
            m = re.match('^([A-Za-z])([\.\)]) +', chunk)
            if m:
                if chunk[0].islower():
                    listtype = 'lower-alpha'
                else:
                    listtype = 'upper-alpha'
                (ordinal,separator) = m.groups()
                rest = chunk[m.end():]
                return (listtype,ordinal,separator,rest)

            if chunk.startswith("* "):
                return ("disc",None,None,chunk)
            if chunk.startswith("- "):
                return ("dash",None,None,chunk)
                
            return (listtype,ordinal,separator,chunk) # None * 3

        
        # MAIN CODE
        p = FSMParser()
        p.set_recognizers(is_li_decimal,
                          is_li_roman, 
                          is_li_alpha,
                          is_header,
                          is_section,
                          is_subsection,
                          is_subsubsection,
                          is_preformatted,
                          is_definition,
                          is_description,
                          is_state_a,
                          is_state_b,
                          is_state_c,
                          is_paragraph)
        p.set_transitions({("body", is_paragraph): (make_paragraph, None),
                           ("body", is_section): (make_section,"section"),
                           ("body", is_state_a): (make_state_a, "state-a"),
                           ("state-a", is_state_b): (make_state_b, "state-b"),
                           ("state-b", is_state_c): (make_state_c, "state-c"),
                           ("state-c", is_section): (False, None),
                           ("section", is_paragraph): (make_paragraph, None),
                           ("section", is_subsection): (make_subsection, "subsection"),
                           ("subsection", is_paragraph): (make_paragraph,None),
                           ("subsection", is_subsection): (False,None),
                           ("subsection", is_state_a): (False,"body"), 
                           ("subsection", is_subsubsection): (make_subsubsection,"subsubsection"),
                           ("subsubsection", is_paragraph): (make_paragraph,None),
                           ("subsubsection", is_section): (False, None),
                           ("subsection", is_section): (False, None),
                           ("section", is_section): (False, None),
                           ("body", is_li_decimal): (make_ol_decimal, "ol-decimal"),
                           ("ol-decimal",is_li_decimal):(make_listitem,"listitem"),
                           ("ol-decimal",is_li_alpha):(make_ol_alpha,"ol-alpha"),
                           ("ol-alpha",is_li_alpha):(make_listitem,"listitem"),
                           ("ol-alpha",is_li_roman):(make_ol_roman,"ol-roman"),
                           ("ol-roman",is_li_roman):(make_listitem,"listitem"),
                           ("ol-roman",is_li_alpha):(False,None),
                           ("ol-alpha",is_li_decimal):(False,None),
                           ("listitem",is_li_alpha):sublist_or_parent, 
                           ("listitem",is_li_roman):sublist_or_parent, 
                           ("listitem",is_li_decimal):sublist_or_parent, 
                           })

        p.debug = debug

        tr=TextReader(filename,encoding="utf-8",linesep=TextReader.UNIX)
        p.initial_state = "body"
        p.initial_constructor = make_body
        b = p.parse(tr.getiterator(tr.readparagraph))
        return p, b

    def parametric_test(self, filename):
        resultfilename = filename.replace(".txt",".xml")
        debug = not os.path.exists(resultfilename)
        p, b = self.run_test_file(filename, debug)
        self.maxDiff = 4096
        if os.path.exists(resultfilename):
            with codecs.open(resultfilename,encoding="utf-8") as fp:
                result = fp.read().strip()
            # print(elements.serialize(b))
            if result != elements.serialize(b).strip():
                # re-run the parse but with debugging on
                print("============DEBUG OUTPUT================")
                p.debug = True
                tr=TextReader(filename,encoding="utf-8",linesep=TextReader.UNIX)
                b = p.parse(tr.getiterator(tr.readparagraph))
                print("===============RESULT===================")
                print(elements.serialize(b))
                self.fail("========See output above=======")
            else:
                self.assertEqual(result, elements.serialize(b).strip())
        else:
            print("\nResult:\n"+elements.serialize(b))
            self.fail()

    def test_no_recognizer(self):
        with self.assertRaises(FSMStateError):
            self.run_test_file("test/files/fsmparser/no-recognizer.tx")

    def test_no_transition(self):
        with self.assertRaises(FSMStateError):
            self.run_test_file("test/files/fsmparser/no-transition.tx")

    def test_debug(self):
        builtins = "__builtin__" if six.PY2 else "builtins"
        with patch(builtins+".print") as printmock:
            self.run_test_file("test/files/fsmparser/basic.txt", debug=True)
            self.assertTrue(printmock.called)

file_parametrize(Parse,"test/files/fsmparser",".txt")
