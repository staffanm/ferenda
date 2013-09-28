# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os,sys
from ferenda.compat import unittest

import codecs
from ferenda.textreader import TextReader

PREFIX = os.path.dirname(__file__)+"/files/textreader"
    
class Basic(unittest.TestCase):
    def setUp(self):
        self.f = TextReader(PREFIX + "/LICENSE.txt",linesep=TextReader.UNIX)

    def testReadline(self):
        self.assertEqual(self.f.readline(),
                         'A. HISTORY OF THE SOFTWARE')

        self.assertEqual(self.f.readline(),
                         '==========================')
        self.f.seek(0)

    def testIterateFile(self):
        self.assertEqual(self.f.bof(), True)
        self.assertEqual(self.f.eof(), False)
        for line in self.f:
            pass
        self.assertEqual(self.f.bof(), False)
        self.assertEqual(self.f.eof(), True)
        self.f.seek(0)
        
    def testReadparagraph(self):
        l = self.f.readparagraph()
        self.assertEqual(l, 'A. HISTORY OF THE SOFTWARE'+self.f.linesep+'==========================')
        l = self.f.readparagraph()
        self.assertEqual(l, 'Python was created in the early 1990s by Guido van Rossum at Stichting'+self.f.linesep+
                         'Mathematisch Centrum (CWI, see http://www.cwi.nl) in the Netherlands'+self.f.linesep+
                         'as a successor of a language called ABC.  Guido remains Python\'s'+self.f.linesep+
                         'principal author, although it includes many contributions from others.')
        self.f.cuepast("to make these releases possible.") # next paragraph is separated by three newlines
        t = self.f.readparagraph()[:23]
        self.assertEqual(t,"B. TERMS AND CONDITIONS")
        self.f.seek(0)

    def testReadChunk(self):
        l = self.f.readchunk('(')
        l = self.f.readchunk(')')
        self.assertEqual(l,'CWI, see http://www.cwi.nl')
        self.f.seek(0)

    def testPeekLine(self):
        l = self.f.peekline()
        self.assertEqual(l, 'A. HISTORY OF THE SOFTWARE')
        l = self.f.peekline(4)
        self.assertEqual(l, 'Python was created in the early 1990s by Guido van Rossum at Stichting')
        self.f.seek(0)
        
    def testPeekParagraph(self):
        l = self.f.peekparagraph()
        self.assertEqual(l, 'A. HISTORY OF THE SOFTWARE'+self.f.linesep+'==========================')
        l = self.f.peekparagraph(2)
        self.assertEqual(l, 'Python was created in the early 1990s by Guido van Rossum at Stichting'+self.f.linesep+
                         'Mathematisch Centrum (CWI, see http://www.cwi.nl) in the Netherlands'+self.f.linesep+
                         'as a successor of a language called ABC.  Guido remains Python\'s'+self.f.linesep+
                         'principal author, although it includes many contributions from others.')
        self.f.seek(0)

    

    def testPrevLine(self):
        self.f.readparagraph()
        self.f.readparagraph()
        self.assertEqual(self.f.prevline(3), # first two newlines, then the actual previous line (does this make sense?)
                         'principal author, although it includes many contributions from others.')
        self.assertEqual(self.f.prevline(6),
                         'Python was created in the early 1990s by Guido van Rossum at Stichting')
        self.f.seek(0)

    def testCue(self):
        self.f.cue("Guido")
        self.assertEqual(self.f.readline(),
                          'Guido van Rossum at Stichting')
        self.f.seek(0)

    def testCuePast(self):
        self.f.cuepast("Guido")
        self.assertEqual(self.f.readline(),
                          ' van Rossum at Stichting')
        self.f.seek(0)

    def testReadTo(self):
        self.assertEqual(self.f.readto("SOFTWARE"),
                          'A. HISTORY OF THE ')


# run all basic tests again, but this time initialised from a unicode buffer
class Ustring(Basic):
    def setUp(self):
        with codecs.open(PREFIX + "/LICENSE.txt",encoding='ascii') as fp:
            data = fp.read()
        self.f = TextReader(string=data,linesep=TextReader.UNIX)


class Codecs:
    def testUTF(self):
        f = TextReader(PREFIX + "/test/test_doctest4.txt", "utf-8")
        f.cue("u'f")
        self.assertEqual(f.read(5),
                          "u'f\u00f6\u00f6") 
        f.cue("u'b")
        self.assertEqual(f.read(5),
                          "u'b\u0105r")

    def testISO(self):
        f = TextReader(PREFIX + "/test/test_shlex.py", "iso-8859-1")
        f.cue(';|-|)|')
        f.readline()
        self.assertEqual(f.read(5),
                          "\u00e1\u00e9\u00ed\u00f3\u00fa")

    def testKOI8(self):
        f = TextReader(PREFIX + "/test/test_pep263.py", "koi8-r")
        f.cue('u"')
        self.assertEqual(f.read(7),
                          'u"\u041f\u0438\u0442\u043e\u043d')

class Processing(unittest.TestCase):
    def setUp(self):
        self.f = TextReader(PREFIX + "/LICENSE.txt",linesep=TextReader.UNIX)

    def testStrip(self):
        self.f.autostrip = True
        self.assertEqual(self.f.peekline(28),
                          'Release         Derived     Year        Owner       GPL-')
        self.f.autostrip = False
        self.assertEqual(self.f.peekline(28),
                          '    Release         Derived     Year        Owner       GPL-')
        self.f.seek(0)

    def testDewrap(self):
        self.f.autodewrap = True
        self.assertEqual(self.f.readparagraph(),
                          'A. HISTORY OF THE SOFTWARE ==========================')
        self.f.seek(0)
        self.f.autodewrap = False
        self.assertEqual(self.f.readparagraph(),
                          'A. HISTORY OF THE SOFTWARE'+self.f.linesep+'==========================')
        self.f.seek(0)
        

    def testDehyphenate(self):
        pass

    def testExpandtabs(self):
        pass

    def testReadTable(self):
        # Should this even be in the Processing test suite?
        pass
    

class Customiterator(unittest.TestCase):
    def setUp(self):
        self.f = TextReader(PREFIX + "/LICENSE.txt",linesep=TextReader.UNIX)

    def testIterateParagraph(self):
        cnt = 0
        for p in self.f.getiterator(self.f.readchunk,self.f.linesep*2):
            cnt += 1

        self.assertEqual(cnt, 44) 
        

class Subreaders(unittest.TestCase):
    def setUp(self):
        self.f = TextReader(PREFIX + "/test_base64.py",linesep=TextReader.UNIX)

    def testPage1(self):
        p = self.f.getreader(self.f.readpage)
        # print "p.maxpos: %s" % p.maxpos
        self.assertEqual(p.readline(),
                         'import unittest')
        self.assertRaises(IOError, p.peekline, 32) # we shouldn't be able to read ahead to page 2
        self.assertRaises(IOError, p.cue, 'LegacyBase64TestCase') # not by this method either
        self.f.seek(0)


    def testPage2(self):
        self.f.readpage() 
        p = self.f.getreader(self.f.readpage)
        p.readline()
        self.assertEqual(p.readline(),
                         'class LegacyBase64TestCase(unittest.TestCase):')

        self.assertRaises(IOError,p.prevline, 4) # we shouldn't be able to read backwards to page 1

        self.f.seek(0)
    


class Edgecases(unittest.TestCase):
    def setUp(self):
        self.f = TextReader(PREFIX + "/LICENSE.txt",linesep=TextReader.UNIX)

    def testPeekPastEOF(self):
        self.assertRaises(IOError,
                          self.f.peekline, 4711)

    def testPrevPastBOF(self):
        self.assertRaises(IOError,
                          self.f.prevline, 4711)

    def testReadPastEOF(self):
        self.assertEqual(len(self.f.read(1)), 1)
        self.f.read(sys.maxsize) # read past end of file - no license text is THAT big
        self.assertNotEqual(self.f.currpos, sys.maxsize+1)
        self.assertEqual(len(self.f.read(1)), 0) # no more to read
        self.assertEqual(len(self.f.readline()), 0)
        self.f.seek(0)
        
    def testReadlineUntilEOF(self):
        for line in self.f:
            prev = line
            pass
        self.assertEqual(prev,
                         'OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.')
        self.assertEqual(self.f.readline(), '')

    def testSearchInVain(self):
        self.assertRaises(IOError,
                          self.f.cue, 'I am a little teapot')
        self.f.seek(0)

class Fileops(unittest.TestCase):

    def testClose(self):
        f = open("textreader.tmp","w")
        f.write("foo")
        f.close()
        r = TextReader("textreader.tmp")
        # make sure TextReader isn't keeping the file open
        os.rename("textreader.tmp", "textreader2.tmp")
        os.unlink("textreader2.tmp")
        self.assertEqual("foo",r.readline())
