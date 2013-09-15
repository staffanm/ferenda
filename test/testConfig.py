#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import sys
import os
from datetime import datetime
import doctest
if sys.version_info < (2,7,0):
    import unittest2 as unittest
else:
    import unittest
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

import six
from six import text_type as str

from ferenda import util
from ferenda.layeredconfig import LayeredConfig

class Main(unittest.TestCase):
    def setUp(self):
        with open("ferenda.ini","w") as fp:
            fp.write("""
[__root__]
datadir = mydata
processes = 4
loglevel = INFO
forceparse = True
jsfiles = ['default.js','modernizr.js']

[mymodule]
loglevel = DEBUG
forceparse=False
jsfiles = ['pdfviewer.js','zepto.js']
lastrun = 2012-09-18 15:41:00
""")

    def tearDown(self):
        if os.path.exists("ferenda.ini"):
            os.unlink("ferenda.ini")
    
    def test_defaults(self):
        cfg = LayeredConfig(defaults={'datadir':'mydata',
                                      'processes':4,
                                      'loglevel':'INFO',
                                      'forceparse':True,
                                      'jsfiles':['default.js','modernizr.js']
                                      })
        
        self.assertEqual(cfg.datadir,'mydata')
        self.assertIs(type(cfg.datadir),six.text_type)
        self.assertEqual(cfg.processes,4)
        self.assertIs(type(cfg.processes),int)
        self.assertEqual(cfg.loglevel,'INFO')
        self.assertIs(type(cfg.loglevel),six.text_type)
        self.assertEqual(cfg.forceparse,True)
        self.assertIs(type(cfg.forceparse),bool)
        self.assertEqual(cfg.jsfiles,['default.js','modernizr.js'])
        self.assertIs(type(cfg.jsfiles),list)
        
    def test_defaults_subsections(self):
        # this tests the following datatypes:
        # str, int, bool, list, datetime -- should cover most cases
        cfg = LayeredConfig(defaults={'datadir':'mydata',
                                      'processes':4,
                                      'loglevel':'INFO',
                                      'forceparse':True,
                                      'jsfiles':['default.js','modernizr.js'],
                                      'mymodule':{'loglevel':'DEBUG',
                                                  'forceparse':False,
                                                  'jsfiles':['pdfviewer.js','zepto.js'],
                                                  'lastrun':datetime(2012,9,18,15,41,0),
                                                  'arbitrary': {
                                                      'nesting': {
                                                          'depth':'works'
                                                      }
                                                  }
                                              }
                                      })
        self.assertEqual(cfg.datadir,'mydata')
        with self.assertRaises(AttributeError):
            cfg.mymodule.datadir
        self.assertEqual(cfg.processes,4)
        with self.assertRaises(AttributeError):
            cfg.mymodule.processes
        self.assertEqual(cfg.loglevel,'INFO')
        self.assertEqual(cfg.mymodule.loglevel,'DEBUG')
        self.assertEqual(cfg.forceparse,True)
        self.assertEqual(cfg.mymodule.forceparse,False)
        self.assertEqual(cfg.jsfiles,['default.js','modernizr.js'])
        self.assertEqual(cfg.mymodule.jsfiles,['pdfviewer.js','zepto.js'])
        self.assertEqual(cfg.mymodule.arbitrary.nesting.depth, 'works')
        with self.assertRaises(AttributeError):
            cfg.lastrun
        self.assertEqual(cfg.mymodule.lastrun,datetime(2012,9,18,15,41,0))
                                      

    def test_inifile(self):
        with open("ferenda.ini","w") as fp:
            fp.write("""
[__root__]
datadir = mydata
processes = 4
forceparse = True
jsfiles = ['default.js','modernizr.js']
""")
        cfg = LayeredConfig(inifile="ferenda.ini")
        self.assertEqual(cfg.datadir,'mydata')
        self.assertIs(type(cfg.datadir),str)
        self.assertEqual(cfg.processes,'4')
        self.assertIs(type(cfg.processes),str)
        self.assertEqual(cfg.forceparse,'True')
        self.assertIs(type(cfg.forceparse),str)
        self.assertEqual(cfg.jsfiles,"['default.js','modernizr.js']")
        self.assertIs(type(cfg.jsfiles),str)


    def test_inifile_subsections(self):
        cfg = LayeredConfig(inifile="ferenda.ini")

        self.assertEqual(cfg.datadir,'mydata')
        with self.assertRaises(AttributeError):
            cfg.mymodule.datadir
        self.assertEqual(cfg.processes,'4')
        with self.assertRaises(AttributeError):
            cfg.mymodule.processes
        self.assertEqual(cfg.loglevel,'INFO')
        self.assertEqual(cfg.mymodule.loglevel,'DEBUG')
        self.assertEqual(cfg.forceparse,'True')
        self.assertEqual(cfg.mymodule.forceparse,'False')
        self.assertEqual(cfg.jsfiles,"['default.js','modernizr.js']")
        self.assertEqual(cfg.mymodule.jsfiles,"['pdfviewer.js','zepto.js']")
        with self.assertRaises(AttributeError):
            cfg.lastrun
        self.assertEqual(cfg.mymodule.lastrun,"2012-09-18 15:41:00")


    def test_commandline(self):
        cmdline = ['--datadir=mydata',
                   '--processes=4',
                   '--loglevel=INFO',
                   '--forceparse=True', # results in string, not bool - compare to --implicitboolean below
                   '--jsfiles=default.js',
                   '--jsfiles=modernizr.js',
                   '--implicitboolean']
        cfg = LayeredConfig(commandline=cmdline)
        self.assertEqual(cfg.datadir,'mydata')
        self.assertIs(type(cfg.datadir),str)
        self.assertEqual(cfg.processes,'4')
        self.assertIs(type(cfg.processes),str)
        self.assertEqual(cfg.forceparse,'True')
        self.assertIs(type(cfg.forceparse),str)
        self.assertEqual(cfg.jsfiles,['default.js','modernizr.js'])
        self.assertIs(type(cfg.jsfiles),list)
        self.assertTrue(cfg.implicitboolean)
        self.assertIs(type(cfg.implicitboolean),bool)
        
    def test_commandline_subsections(self):
        cmdline = ['--datadir=mydata',
                   '--processes=4',
                   '--loglevel=INFO',
                   '--forceparse=True',
                   '--jsfiles=default.js',
                   '--jsfiles=modernizr.js',
                   '--mymodule-loglevel=DEBUG', 
                   '--mymodule-forceparse=False',
                   '--mymodule-jsfiles=pdfviewer.js',
                   '--mymodule-jsfiles=zepto.js',
                   '--mymodule-lastrun=2012-09-18 15:41:00',
                   '--mymodule-arbitrary-nesting-depth=works']

        cfg = LayeredConfig(commandline=cmdline)
        self.assertEqual(cfg.datadir,'mydata')
        with self.assertRaises(AttributeError):
            cfg.mymodule.datadir
        self.assertEqual(cfg.processes,'4')
        with self.assertRaises(AttributeError):
            cfg.mymodule.processes
        self.assertEqual(cfg.loglevel,'INFO')
        self.assertEqual(cfg.mymodule.loglevel,'DEBUG')
        self.assertEqual(cfg.forceparse,'True')
        self.assertEqual(cfg.mymodule.forceparse,'False')
        self.assertEqual(cfg.jsfiles,['default.js','modernizr.js'])
        self.assertEqual(cfg.mymodule.jsfiles,['pdfviewer.js','zepto.js'])
        self.assertEqual(cfg.mymodule.arbitrary.nesting.depth, 'works')
        with self.assertRaises(AttributeError):
            cfg.lastrun
        self.assertEqual(cfg.mymodule.lastrun,"2012-09-18 15:41:00")

    def test_typed_inifile(self):
        types = {'datadir':str,
                 'processes':int,
                 'forceparse':bool,
                 'jsfiles':list, 
                 'mymodule':{'forceparse':bool,
                             'lastrun':datetime}}
        
        cfg = LayeredConfig(defaults=types,inifile="ferenda.ini")
        # cfg = LayeredConfig(inifile="ferenda.ini")
        self.assertEqual(cfg.datadir,'mydata')
        self.assertIs(type(cfg.datadir),str)
        self.assertEqual(cfg.processes,4)
        self.assertIs(type(cfg.processes),int)
        self.assertEqual(cfg.forceparse,True)
        self.assertIs(type(cfg.forceparse),bool)
        self.assertEqual(cfg.jsfiles,['default.js','modernizr.js'])
        self.assertIs(type(cfg.jsfiles),list)
        self.assertEqual(cfg.mymodule.forceparse,False)
        self.assertIs(type(cfg.mymodule.forceparse),bool)
        self.assertEqual(cfg.mymodule.lastrun,datetime(2012,9,18,15,41,0))
        self.assertIs(type(cfg.mymodule.lastrun),datetime)

        
    def test_typed_commandline(self):
        types = {'datadir':str,
                 'processes':int,
                 'forceparse':bool,
                 'jsfiles':list, 
                 'mymodule':{'forceparse':bool,
                             'lastrun':datetime}
                 }

        cmdline = ['--datadir=mydata',
                   '--processes=4',
                   '--forceparse=True',
                   '--jsfiles=default.js',
                   '--jsfiles=modernizr.js',
                   '--mymodule-forceparse=False',
                   '--mymodule-lastrun=2012-09-18 15:41:00']
        cfg = LayeredConfig(defaults=types,commandline=cmdline)
        self.assertEqual(cfg.datadir,'mydata')
        self.assertIs(type(cfg.datadir),str)
        self.assertEqual(cfg.processes,4)
        self.assertIs(type(cfg.processes),int)
        self.assertEqual(cfg.forceparse,True)
        self.assertIs(type(cfg.forceparse),bool)
        self.assertEqual(cfg.jsfiles,['default.js','modernizr.js'])
        self.assertIs(type(cfg.jsfiles),list)
        self.assertEqual(cfg.mymodule.forceparse,False)
        self.assertIs(type(cfg.mymodule.forceparse),bool)
        self.assertEqual(cfg.mymodule.lastrun,datetime(2012,9,18,15,41,0))
        self.assertIs(type(cfg.mymodule.lastrun),datetime)


    def test_typed_commandline_cascade(self):
        # the test here is that _load_commandline must use _type_value property.
        defaults = {'forceparse':True,
                    'mymodule': {}}
        cmdline = ['--mymodule-forceparse=False']
        cfg = LayeredConfig(defaults=defaults, commandline=cmdline, cascade=True)
        subconfig = getattr(cfg, 'mymodule')
        self.assertIs(type(subconfig.forceparse), bool)
        self.assertEqual(subconfig.forceparse, False)

    def test_layered(self):
        defaults = {'loglevel':'ERROR'}
        cmdline = ['--loglevel=DEBUG']
        cfg = LayeredConfig(defaults=defaults)
        self.assertEqual(cfg.loglevel, 'ERROR')
        cfg = LayeredConfig(defaults=defaults,inifile="ferenda.ini")
        self.assertEqual(cfg.loglevel, 'INFO')
        cfg = LayeredConfig(defaults=defaults,inifile="ferenda.ini",commandline=cmdline)
        self.assertEqual(cfg.loglevel, 'DEBUG')

    def test_layered_subsections(self):
        defaults = {'force':False,
                    'datadir':'thisdata',
                    'loglevel':'INFO'}
        cmdline=['--mymodule-datadir=thatdata','--mymodule-force'] # 
        cfg = LayeredConfig(defaults=defaults,commandline=cmdline,cascade=True)
        self.assertEqual(cfg.mymodule.force, True)
        self.assertEqual(cfg.mymodule.datadir, 'thatdata')
        self.assertEqual(cfg.mymodule.loglevel, 'INFO')

        defaults = {'mymodule':defaults}
        cmdline=['--datadir=thatdata','--force'] # 
        cfg = LayeredConfig(defaults=defaults,commandline=cmdline,cascade=True)
        self.assertEqual(cfg.mymodule.force, True)
        self.assertEqual(cfg.mymodule.datadir, 'thatdata')
        self.assertEqual(cfg.mymodule.loglevel, 'INFO')


    def test_modified(self):
        defaults = {'lastdownload':None}
        cfg = LayeredConfig(defaults=defaults)
        now = datetime.now()
        cfg.lastdownload = now
        self.assertEqual(cfg.lastdownload,now)
        

    def test_modified_subsections(self):
        defaults = {'force':False,
                    'datadir':'thisdata',
                    'loglevel':'INFO'}
        cmdline=['--mymodule-datadir=thatdata','--mymodule-force'] # 
        cfg = LayeredConfig(defaults=defaults,inifile="ferenda.ini",commandline=cmdline,cascade=True)
        cfg.mymodule.loglevel = 'ERROR'

    def test_write_configfile(self):
        cfg = LayeredConfig(inifile="ferenda.ini")
        cfg.mymodule.lastrun = datetime(2013,9,18,15,41,0)
        LayeredConfig.write(cfg)
        want = """[__root__]
datadir = mydata
processes = 4
loglevel = INFO
forceparse = True
jsfiles = ['default.js','modernizr.js']

[mymodule]
loglevel = DEBUG
forceparse = False
jsfiles = ['pdfviewer.js','zepto.js']
lastrun = 2013-09-18 15:41:00

"""
        got = util.readfile("ferenda.ini")
        #if not isinstance(got, six.text_type):
        #    got = got.decode("utf-8")
        self.assertEqual(want,got)

    def test_write_noconfigfile(self):
        cfg = LayeredConfig(defaults={'lastrun': datetime(2012,9,18,15,41,0)})
        cfg.lastrun = datetime(2013,9,18,15,41,0)
        LayeredConfig.write(cfg)

from ferenda import layeredconfig
def load_tests(loader,tests,ignore):
    tests.addTests(doctest.DocTestSuite(layeredconfig))
    return tests
        
