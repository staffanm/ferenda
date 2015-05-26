# _simpleparseFallback.py

# Mimic the simpleparse interface (the very few parts we're using)
# but call external python 2.7 processes behind the scene.
import sys
import os
import re
import hashlib
import logging
import tempfile
import shutil

# 3rdparty libs
from six import unichr as chr

# my libs
from ferenda import util

external_simpleparse_state = None
python_exe = os.environ.get("FERENDA_PYTHON2_FALLBACK",
                                "python2.7")

def _setup_state():
    state = tempfile.mkdtemp()
    buildtagger_script = state + os.sep + "buildtagger.py"
    util.writefile(buildtagger_script, """import sys,os
if sys.version_info >= (3,0,0):
    raise OSError("This is python %s, not python 2.6 or 2.7!" % sys.version_info)
declaration = sys.argv[1] # md5 sum of the entire content of declaration
production = sys.argv[2]  # short production name
picklefile = "%s-%s.pickle" % (declaration, production)
from simpleparse.parser import Parser
from simpleparse.stt.TextTools.TextTools import tag
import cPickle as pickle

with open(declaration,"rb") as fp:
    p = Parser(fp.read(), production)
t = p.buildTagger(production)
with open(picklefile,"wb") as fp:
    pickle.dump(t,fp)""")

        tagstring_script = state + os.sep + "tagstring.py"
        util.writefile(tagstring_script, """import sys, os
if sys.version_info >= (3,0,0):
    raise OSError("This is python %s, not python 2.6 or 2.7!" % sys.version_info)
pickled_tagger = sys.argv[1] # what buildtagger.py returned -- full path
full_text_path = sys.argv[2]
text_checksum = sys.argv[3] # md5 sum of text, just the filename
picklefile = "%s-%s.pickle" % (pickled_tagger, text_checksum)

from simpleparse.stt.TextTools.TextTools import tag

import cPickle as pickle

with open(pickled_tagger) as fp:
    t = pickle.load(fp)
with open(full_text_path, "rb") as fp:
    text = fp.read()
tagged = tag(text, t, 0, len(text))
with open(picklefile,"wb") as fp:
    pickle.dump(tagged,fp)
        """)
    return state

# print("(__boot__): calling _setup_state to setup external_simpleparse_state")
external_simpleparse_state = _setup_state()

class Parser(object):
    def __init__(self, declaration, root='root', prebuilts=(), definitionSources=[]):
        global external_simpleparse_state
        # 2. dump declaration to a tmpfile read by the script
        c = hashlib.md5()
        c.update(declaration)
        self.declaration_md5 = c.hexdigest()
        if not external_simpleparse_state:
            # print("__init__: calling _setup_state to setup external_simpleparse_state")
            external_simpleparse_state = _setup_state()
        declaration_filename = "%s/%s" % (external_simpleparse_state,
                                          self.declaration_md5)
        with open(declaration_filename, "wb") as fp:
            fp.write(declaration)

    def __del__(self):
        global external_simpleparse_state
        if external_simpleparse_state and os.path.exists(external_simpleparse_state):
            shutil.rmtree(external_simpleparse_state)
            # print("__del__: setting external_simpleparse_state to None")
            external_simpleparse_state = None
    def buildTagger(self, production=None, processor=None):
        pickled_tagger = "%s/%s-%s.pickle" % (external_simpleparse_state,
                                              self.declaration_md5,
                                              production)
        if not os.path.exists(pickled_tagger):
            #    3. call the script with python 27 and production
            cmdline = "%s %s %s/%s %s" % (python_exe,
                                          external_simpleparse_state +
                                          os.sep + "buildtagger.py",
                                          external_simpleparse_state,
                                          self.declaration_md5,
                                          production)
            util.runcmd(cmdline, require_success=True)
            #    4. the script builds tagtable and dumps it to a pickle file
            assert os.path.exists(pickled_tagger)
        return pickled_tagger  # filename instead of tagtable struct

def tag(text, tagtable, sliceleft, sliceright):
    global external_simpleparse_state
    # print("tag: external_simpleparse_state is %s" % external_simpleparse_state)
    if external_simpleparse_state is None:
        external_simpleparse_state = _setup_state()
    c = hashlib.md5()
    c.update(text)
    text_checksum = c.hexdigest()
    pickled_tagger = tagtable  # remember, not a real tagtable struct
    pickled_tagged = "%s-%s.pickle" % (pickled_tagger, text_checksum)

    if not os.path.exists(pickled_tagged):
        # 2. Dump text as string
        full_text_path = "%s/%s.txt" % (os.path.dirname(pickled_tagger),
                                        text_checksum)
        util.ensure_dir(full_text_path)
        with open(full_text_path, "wb") as fp:
            fp.write(text)
            # 3. call script (that loads the pickled tagtable + string
            # file, saves tagged text as pickle)
        util.runcmd("%s %s %s %s %s" %
                    (python_exe,
                     external_simpleparse_state + os.sep + "tagstring.py",
                     pickled_tagger,
                     full_text_path,
                     text_checksum),
                     require_success=True)
    # 4. load tagged text pickle
    with open(pickled_tagged, "rb") as fp:
        res = pickle.load(fp)
    return res

