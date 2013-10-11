# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys, os

import six
from ferenda.compat import unittest, patch, call,  MagicMock
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

from tempfile import mkstemp

from ferenda import Devel

class Main(unittest.TestCase):
    def test_dumprdf(self):
        fileno, tmpfile = mkstemp()
        fp = os.fdopen(fileno, "w")
        fp.write("""<html xmlns="http://www.w3.org/1999/xhtml">
        <head about="http://example.org/doc">
           <title property="http://purl.org/dc/terms">Doc title</title>
        </head>
        <body>...</body>
        </html>""")
        fp.close()
        d = Devel()
        mock = MagicMock()
        builtins = "__builtin__" if six.PY2 else "builtins"
        with patch(builtins+'.print', mock):
            d.dumprdf(tmpfile, format="nt")
        self.assertTrue(mock.called)
        want = '<http://example.org/doc> <http://purl.org/dc/terms> "Doc title" .\n\n'
        mock.assert_has_calls([call(want)])
        
    
    def test_parsestring(self):
        d = Devel()
        with self.assertRaises(NotImplementedError):
            d.parsestring(None,None,None)
