# -*- coding: utf-8 -*-
"""Utility functions for running various ferenda tasks from the
command line, including registering classes in the configuration
file. If you're using the :py:class:`~ferenda.DocumentRepository` API
directly in your code, you'll probably only need
:py:func:`makeresources`, :py:func:`frontpage` and possibly
:py:func:`setup_logger`. If you're using the ``ferenda-build.py``
tool, you don't need to directly call any of these methods --
``ferenda-build.py`` calls :py:func:`run`, which calls everything
else, for you.

"""
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
nativeint = int
from builtins import *
from future import standard_library
standard_library.install_aliases()
from future.utils import bytes_to_native_str

# stdlib
from collections import OrderedDict, Counter
from datetime import datetime
from io import StringIO
from logging import getLogger as getlog
from multiprocessing.managers import SyncManager, RemoteError
from queue import Queue
from time import sleep
from urllib.parse import urlsplit
# from wsgiref.simple_server import make_server
from contextlib import contextmanager
import argparse
import builtins
import cProfile
import codecs
import configparser
import copy
import inspect
import importlib
import io
import logging
import multiprocessing
import os
import pickle
import pstats
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import traceback
import warnings
try:
    BrokenPipeError
except NameError:
    import socket
    BrokenPipeError = socket.error

# 3rd party
import requests
import requests.exceptions
import lxml.etree
from layeredconfig import (LayeredConfig, Defaults, INIFile, Commandline,
                           Environment)
try:  # optional module
    from setproctitle import setproctitle, getproctitle
except ImportError:  # pragma: no cover
    def setproctitle(title): pass
    def getproctitle(): return ""
from werkzeug.serving import run_simple

# my modules
from ferenda import DocumentRepository  # needed for a doctest
from ferenda import Transformer, TripleStore, ResourceLoader, WSGIApp, Resources
from ferenda import errors, util
from ferenda.compat import MagicMock


DEFAULT_CONFIG = {'loglevel': 'DEBUG',
                  'logfile': True,
                  'processes': '1',
                  'datadir': 'data',
                  #'force': False,
                  #'refresh': False,
                  #'conditionalget': True,
                  #'useragent': 'ferenda-bot',
                  #'downloadmax': nativeint,
                  #'lastdownload': datetime,
                  'combineresources': False,
                  'staticsite': False,
                  'all': False,
                  'allversions': False,
                  'relate': True,
                  'download': True,
                  'tabs': True,
                  #'primaryfrontpage': False,
                  #'frontpagefeed': False,
                  'sitename': 'MySite',
                  'sitedescription': 'Just another Ferenda site',
                  'cssfiles': ['css/ferenda.css'],
                  'jsfiles': ['js/ferenda.js'],
                  'imgfiles': [],
                  'disallowrobots': False,
                  'legacyapi': False,
                  'wsgiappclass': 'ferenda.WSGIApp',
                  #'fulltextindex': True,
                  #'removeinvalidlinks': True,
                  'serverport': 5555,
                  'authkey': b'secret',
                  'profile': False,
                  'wsgiexceptionhandler': True}

class MarshallingHandler(logging.Handler):
    def __init__(self, records):
        self.records = records
        super(MarshallingHandler, self).__init__()
        
    def emit(self, record):
        self.records.append(self.marshal(record))

    def marshal(self, record): 
        # Based on SocketHandler.makePickle
        ei = record.exc_info
        if ei:
            # just to get traceback text into record.exc_text ...
            dummy = self.format(record)
        d = dict(record.__dict__)
        d['msg'] = record.getMessage()
        d['args'] = None
        d['exc_info'] = None
        return pickle.dumps(d, 1)

class ParseErrorWrapper(errors.FerendaException): pass

class BasefileLoggerAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        e = self.extra
        label = e['basefile'] + "@" + e['version'] if e['version'] else e['basefile']
        return '[{%s}] %s' % (label, msg), kwargs
    

def makeresources(repos,
                  resourcedir="data/rsrc",
                  combine=False,
                  cssfiles=[],
                  jsfiles=[],
                  imgfiles=[],
                  staticsite=False,
                  legacyapi=False,
                  sitename="MySite",
                  sitedescription="Just another Ferenda site",
                  url="http://localhost:8000/"):
    """Creates the web assets/resources needed for the web app
    (concatenated and minified js/css files, resources.xml used by
    most XSLT stylesheets, etc).

    :param repos: The repositories to create resources for, as instantiated
                  and configured docrepo objects
    :type  repos: list
    :param combine: whether to combine and compact/minify CSS and JS files
    :type  combine: bool
    :param resourcedir: where to put generated/copied resources
    :type  resourcedir: str
    :returns: All created/copied css, js and resources.xml files
    :rtype: dict of lists
    """
    warnings.warn("manager.makeresources is deprecated; "
                  "use ferenda.Resources().make() instead")
    return Resources(repos, resourcedir,
                     combineresources=combine,
                     cssfiles=cssfiles,
                     jsfiles=jsfiles,
                     imgfiles=imgfiles,
                     staticsite=staticsite,
                     legacyapi=legacyapi,
                     sitename=sitename,
                     sitedescription=sitedescription,
                     url=url).make()


def frontpage(repos,
              path="data/index.html",
              stylesheet="xsl/frontpage.xsl",
              sitename="MySite",
              staticsite=False,
              develurl=None,
              removeinvalidlinks=True):
    """Create a suitable frontpage.

    :param repos: The repositories to list on the frontpage, as instantiated
                  and configured docrepo objects
    :type repos: list
    :param path: the filename to create.
    :type  path: str
    """
    log = getlog()
    with util.logtime(log.info,
                      "frontpage: wrote %(path)s (%(elapsed).3f sec)",
                      {'path': path}):
        blocks = ""
        # TODO: if any of the repos has inst.config.primaryfrontpage =
        # True, then all other repos should provide their
        # .frontpage_content() into that repos .frontpage impl (and this
        # method should not have any xhtml template like below).
        xhtml = None
        feed = None
        for inst in repos:
            if inst.config.primaryfrontpage and not xhtml:
                xhtml = inst.frontpage_content(primary=True)
            if inst.config.frontpagefeed and not feed:
                feed = inst.store.resourcepath("feed/main.atom")
                
        if not xhtml:
            for inst in repos:
                content = inst.frontpage_content()
                if content:
                    blocks += "<div id='%s'>%s</div>" % (inst.alias, content)
                    log.debug("frontpage: repo %s provided %s chars of content" %
                              (inst.alias, len(content)))
            vars = {'title': sitename,
                    'blocks': blocks}
            xhtml = """<?xml version='1.0' encoding='utf-8'?>
    <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML+RDFa 1.0//EN" "http://www.w3.org/MarkUp/DTD/xhtml-rdfa-1.dtd">
    <html xmlns="http://www.w3.org/1999/xhtml">
      <head>
        <title>%(title)s</title>
      </head>
      <body>
        %(blocks)s
      </body>
    </html>""" % vars

        xhtml_path = os.path.splitext(path)[0] + ".xhtml"
        with codecs.open(xhtml_path, "w", encoding="utf-8") as fp:
            fp.write(xhtml)

        # FIXME: We don't need to actually store the xhtml file on
        # disk -- we could just keep it in memory as an lxml tree and
        # call .transform(tree) just like
        # DocuementRepository.toc_create_page does
        docroot = os.path.dirname(path)
        conffile = os.path.abspath(
            os.sep.join([docroot, 'rsrc', 'resources.xml']))

        # FIXME: Cut-n-paste of the method in Resources.__init__
        loadpaths = [ResourceLoader.make_loadpath(repo) for repo in repos]
        loadpath = ["."]  # cwd always has priority -- makes sense?
        for subpath in loadpaths:
            for p in subpath:
                if p not in loadpath:
                    loadpath.append(p)
        transformer = Transformer('XSLT', stylesheet, "xsl",
                                  resourceloader=ResourceLoader(*loadpath),
                                  config=conffile,
                                  documentroot=docroot)
        
        transformargs = {'repos': repos,
                         'remove_missing': removeinvalidlinks}
        if staticsite:
            transformargs['basedir'] = os.path.dirname(path)
        elif develurl:
            transformargs['develurl'] = develurl
        if repos:
            urltransform = repos[0].get_url_transform_func(**transformargs)
        else:
            urltransform = lambda x: x
        if feed:
            params = {"feedfile": feed}
        else:
            params = {"feedfile": ""}
        transformer.transform_file(xhtml_path, path,
                                   parameters=params, uritransform=urltransform)
    return True


def status(repo, samplesize=3):
    """Prints out some basic status information about this repository."""
    print = builtins.print
    if not hasattr(repo, 'get_status'):
        return
    print("Status for document repository '%s' (%s)" %
          (repo.alias, getattr(repo.config, 'class')))
    s = repo.get_status()
    for step in s.keys():  # odict
        exists = s[step]['exists']
        todo = s[step]['todo']
        exists_sample = ", ".join(exists[:samplesize])
        exists_more = len(exists) - samplesize
        todo_sample = ", ".join(todo[:samplesize])
        todo_more = len(todo) - samplesize

        if not exists_sample:
            exists_sample = "None"
        if exists_more > 0:
            exists_more_label = ".. (%s more)" % exists_more
        else:
            exists_more_label = ""

        if todo_more > 0:
            todo_more_label = ".. (%s more)" % todo_more
        else:
            todo_more_label = ""

        if step == 'download':
            print(" download: %s.%s" % (exists_sample, exists_more_label))
        else:
            if todo_sample:
                print(" %s: %s.%s Todo: %s.%s" % (step, exists_sample, exists_more_label,
                                                  todo_sample, todo_more_label))
            else:
                print(" %s: %s.%s" % (step, exists_sample, exists_more_label))
        # alias and classname
        # $ ./ferenda-build.py w3c status
        # Status for document repository 'w3c' (w3cstandards.W3Cstandards)
        # downloaded: rdb-direct-mapping r2rml ... (141 more)
        # parsed: None (143 needs parsing)
        # generated: None (143 needs generating)
    
def make_wsgi_app(config, enabled=None):
    """Creates a callable object that can act as a WSGI application by
    mod_wsgi, gunicorn, the built-in webserver, or any other
    WSGI-compliant webserver.

    :param config: Alternatively, a initialized config object
    :type config: LayeredConfig
    :param enabled: A alias->class mapping for all enabled datasources
    :type enabled: dict
    :returns: A WSGI application
    :rtype: callable

    """
    if enabled is None:
        enabled = _enabled_classes()
    repos = [_instantiate_class(cls, config) for cls in _classes_from_classname(enabled, 'all')]
    cls = _load_class(config.wsgiappclass)
    return cls(repos, config)


loglevels = {'DEBUG': logging.DEBUG,
             'INFO': logging.INFO,
             'WARNING': logging.WARNING,
             'ERROR': logging.ERROR,
             'CRITICAL': logging.CRITICAL}


def setup_logger(level='INFO', filename=None,
                 logformat="%(asctime)s %(name)s %(levelname)s %(message)s (%(filename)s:%(lineno)d)",
                 datefmt="%H:%M:%S"):
    """Sets up the logging facilities and creates the module-global log
    object as a root logger.

    :param name: The name of the logger (used in log messages)
    :type  name: str
    :param level: 'DEBUG','INFO','WARNING','ERROR' or 'CRITICAL'
    :type  level: str
    :param filename: The name of the file to log to. If None, log to stdout
    :type filename: str

    """
    l = getlog()  # get the root logger
    if not isinstance(level, int):
        loglevel = loglevels[level]

    # if l.handlers == []:
    if filename:
        util.ensure_dir(filename)
        h = logging.FileHandler(filename)
    else:
        h = logging.StreamHandler()
    for existing_handler in l.handlers:
        if h.__class__ == existing_handler.__class__:
            # print("    A %r already existed" % h)
            return l

    h.setLevel(loglevel)
    h.setFormatter(
        logging.Formatter(logformat, datefmt=datefmt))
    l.addHandler(h)
    l.setLevel(loglevel)

    # turn of some library loggers we're not interested in
    for logname in ['requests.packages.urllib3.connectionpool',
                    'urllib3.connectionpool',
                    'rdflib.plugins.sleepycat',
                    'rdflib.plugins.parsers.pyRdfa',
                    'ferenda.thirdparty.patch']:
        log = getlog(logname)
        log.propagate = False
        if log.handlers == []:
            if hasattr(logging, 'NullHandler'):
                log.addHandler(logging.NullHandler())
            else:  # pragma: no cover
                # py26 compatibility
                class NullHandler(logging.Handler):

                    def emit(self, record):
                        pass
                log.addHandler(NullHandler())

    return l


def shutdown_logger():
    """Shuts down the configured logger. In particular, closes any
    FileHandlers, which is needed on win32 (and is a good idea on all
    platforms).

    """
    l = logging.getLogger()  # get the root logger
    for existing_handler in list(l.handlers):
        # a TempFileHandler means that the quiet() decorator or
        # silence() context manager is in use. Don't remove these, the
        # decorator/ctxmgr will do so when it's time.
        if type(existing_handler).__name__ == "TempFileHandler":
            continue
        if isinstance(existing_handler, logging.FileHandler):
            existing_handler.close()
        l.removeHandler(existing_handler)

@contextmanager
def adaptlogger(instance, basefile, version):
    """A context manager that temporarily switches out the logger on the
provided docrepo instance for a wrapping LoggerAdapter. The wrapper
automatically prepends basefile and optional version to all log messages.

Note that this doesn't change any other loggers that submodules might
use.

    """
    oldlog = instance.log
    instance.log = BasefileLoggerAdapter(instance.log, {'basefile': basefile, 'version': version})
    try:
        yield
    finally:
        instance.log = oldlog

def run(argv, config=None, subcall=False):

    """Runs a particular action for either a particular class or all
    enabled classes.

    :param argv: a ``sys.argv``-style list of strings specifying the class
                 to load, the action to run, and additional
                 parameters. The first parameter is either the name of
                 the class-or-alias, or the special value "all",
                 meaning all registered classes in turn.  The second
                 parameter is the action to run, or the special value
                 "all" to run all actions in correct order. Remaining
                 parameters are either configuration parameters (if
                 prefixed with ``--``, e.g. ``--loglevel=INFO``, or
                 positional arguments to the specified action).
    """
    # make the process print useful information when ctrl-T is pressed
    # (only works on Mac and BSD, who support SIGINFO)
    if hasattr(signal, 'SIGINFO'):
        signal.signal(signal.SIGINFO, _siginfo_handler)
    # or when the SIGUSR1 signal is sent ("kill -SIGUSR1 <pid>")
    if hasattr(signal, 'SIGUSR1'):
        signal.signal(signal.SIGUSR1, _siginfo_handler)
    
    if not config:
        config = load_config(find_config_file(), argv)
        alias = getattr(config, 'alias', None)
        action = getattr(config, 'action', None)
    else:
        alias = argv[0]
        action = argv[1]
    if config and config.profile and not subcall:
        pr = cProfile.Profile()
        pr.enable()
    else:
        pr = None

    log = setup_logger(level=config.loglevel, filename=None)
    # if logfile is set to True (the default), autogenerate logfile
    # name from current datetime. Otherwise assume logfile is set to
    # the desired file name of the log. However, if there already
    # exists a logfile handler, don't create another one (the existing
    # handler might have been set up by the @quiet decorator).
    if (config.logfile and subcall is False and action != "buildclient" and
        not any((isinstance(x, logging.FileHandler) for x in log.handlers))):
        # when running as buildclient, we don't want to each client to
        # create a logfile of their own. Instead, client nodes collect
        # log entries during each run and pass them as part of the
        # result. the buildserver then writes logs to a central
        # logfile.
        if isinstance(config.logfile, bool):
            logfile = "%s/logs/%s.log" % (
                config.datadir, datetime.now().strftime("%Y%m%d-%H%M%S"))
        else:
            logfile = config.logfile
        util.ensure_dir(logfile)
        setup_logger(level=config.loglevel, filename=logfile)

    if not subcall:
        log.info("run: %s" % " ".join(argv))
    try:
        # reads only ferenda.ini using configparser rather than layeredconfig
        enabled = _enabled_classes()
        # returns {'ferenda.sources.docrepo.DocRepo':'base',...}
        enabled_aliases = dict(reversed(item) for item in enabled.items())
        if len(argv) < 1:
            _print_usage()  # also lists enabled modules
        else:
            classname = alias
            alias = enabled_aliases.get(alias, alias)
            # if the selected action exists as a config value and is
            # False (typed or not), then don't do that action.
            if alias != "all" and action != "all" and hasattr(config, alias):
                aliasconfig = getattr(config, alias)
                if (action in aliasconfig and
                    getattr(aliasconfig, action) in (False, 'False')):
                    log.debug("%(alias)s %(action)s: skipping "
                              "(config.%(alias)s.%(action)s=False)" %
                              {'alias': alias,
                               'action': action})
                    return False
            if action == 'enable':
                try:
                    return enable(classname)
                except (ImportError, ValueError) as e:
                    log.error(str(e))
                    return None
            elif action == 'runserver':
                if 'develurl' in config:
                    url = config.develurl
                    develurl = config.develurl
                else:
                    url = config.url
                    develurl = None
                port = urlsplit(url).port or 80
                app = make_wsgi_app(config, enabled)
                getlog().info("Serving wsgi app at http://localhost:%s/" % port)
                # Maybe make use_debugger and use_reloader
                # configurable. But when using ./ferenda-build all
                # runserver, don't you always want a debugger and a
                # reloader?

                # NOTE: If we set use_reloader=True, werkzeug starts
                # a new subprocess with the same args, making us run
                # the expensive setup process twice. Is that
                # unavoidable (maybe the first process determines
                # which files to monitor and the second process
                # actually runs them (and is reloaded by the parent
                # process whenever a file is changed?

                # Note: the actual run_simple method never returns
                run_simple('', port, app, use_debugger=True, use_reloader=True)
            elif action == 'buildclient':
                args = _setup_buildclient_args(config)
                return runbuildclient(**args)
            elif action == 'buildqueue':
                args = _setup_buildqueue_args(config)
                return runbuildqueue(**args)
            elif action == 'makeresources':
                repoclasses = _classes_from_classname(enabled, classname)
                args = _setup_makeresources_args(config)
                repos = []
                for cls in repoclasses:
                    inst = _instantiate_class(cls, config, argv)
                    repos.append(inst)
                # robots.txt must be placed outside of the
                # resourcedirectory ("data/robots.txt" not
                # "data/rsrc/robots.txt"), therefore we do it here.
                robotstxt = config.datadir + os.sep + "robots.txt"
                with open(robotstxt, "w") as fp:
                    if config.disallowrobots:
                        fp.write("""User-agent: *
Disallow: /
""")
                    else:
                        fp.write("""User-agent: *
Disallow: /api/
Disallow: /search/
Disallow: /-/
""")
                log.info("Wrote %s" % robotstxt)
                return Resources(repos, **args).make()
            elif action == 'status':
                repoclasses = _classes_from_classname(enabled, classname)
                args = _setup_makeresources_args(config)
                for cls in repoclasses:
                    inst = _instantiate_class(cls, config, argv)
                    status(inst)

            elif action == 'frontpage':
                repoclasses = _classes_from_classname(enabled, classname)
                args = _setup_frontpage_args(config, argv)
                return frontpage(**args)

            elif action == 'all':
                classnames = _setup_classnames(enabled, classname)
                results = OrderedDict()
                for action in ("download",
                               "parse", "relate", "makeresources",
                               "toc", "generate", "transformlinks", "news", "frontpage"):
                    if action in ("makeresources", "frontpage"):
                        argscopy = argv[2:]  # skip alias and action
                        argscopy.insert(0, action)
                        argscopy.insert(0, "all")
                        results[action] = run(argscopy, config, subcall=True)
                    else:
                        results[action] = OrderedDict()
                        for classname in classnames:
                            alias = enabled_aliases[classname]
                            argscopy = argv[2:]
                            if action in ("parse", "relate", "generate", "transformlinks"):
                                config.all = True
                            else:
                                config.all = False
                            # FIXME: if action is transformlinks and
                            # neither config.{develurl,staticsite} is
                            # set, we should not call run at all
                            # (there's no reason to transform links)
                            argscopy.insert(0, action)
                            argscopy.insert(0, classname)
                            try:
                                results[action][alias] = run(argscopy, config, subcall=True)
                            except Exception as e:
                                loc = util.location_exception(e)
                                log.error("%s %s failed: %s (%s)" %
                                          (action, alias, e, loc))
                return results
            else:
                if classname == "all":
                    ret = []
                    for alias, classname in enabled.items():
                        try:
                            argscopy = argv[2:]  # skip alias and action
                            argscopy.insert(0, action)
                            argscopy.insert(0, alias)
                            ret.append(run(argscopy, config, subcall=True))
                        except Exception as e:
                            loc = util.location_exception(e)
                            log.error("%s %s failed: %s (%s)" %
                                      (action, alias, e, loc))
                    alias = "all"
                    return ret
                else:
                    return _run_class(enabled, argv, config)
    finally:
        if pr:
            pr.disable()
            if isinstance(config.profile, str):
                # gotta be a filename. Dump profile data to disk
                pr.dump_stats(config.profile)
                print("Profiling information dumped to %s" % config.profile)
            else:
                s = io.StringIO()
                sortby = 'cumulative'
                ps = pstats.Stats(pr, stream=s).sort_stats('cumulative')
                # select the top 10 calls not part of manager.py or decorators.py
                restrictions = ('^(?!.*(manager|decorators).py:)', 10)
                ps.print_stats(20)
                print(s.getvalue())            
        if not subcall:
            _shutdown_buildserver()
            shutdown_logger()
            global config_loaded
            config_loaded = False

def _nativestr(unicodestr, encoding="utf-8"):
    return bytes_to_native_str(unicodestr.encode(encoding))


def enable(classname):
    """Registers a class by creating a section for it in the
    configuration file (``ferenda.ini``). Returns the short-form
    alias for the class.

    >>> enable("ferenda.DocumentRepository")
    'base'
    >>> os.unlink("ferenda.ini")

    :param classname: The fully qualified name of the class
    :type classname: str
    :returns: The short-form alias for the class
    :rtype: str
    """

    cls = _load_class(classname)  # eg ferenda.DocumentRepository
    # throws error if unsuccessful

    cfg = configparser.ConfigParser()
    configfilename = find_config_file(create=True)
    cfg.read([configfilename])
    alias = cls.alias
    if False:
        # configparser on py2 has a different API wrt
        # unicode/bytestrings...
        cfg.add_section(alias.encode())
        cfg.set(alias.encode(), b"class", classname.encode())
        mode = "wb"
    else:
        cfg.add_section(alias)
        cfg.set(alias, "class", classname)
        mode = "w"
    with open(configfilename, mode) as fp:
        cfg.write(fp)
    log = getlog()
    log.info("Enabled class %s (alias '%s')" % (classname, alias))
    return alias


def runsetup():
    """Runs :func:`setup` and exits with a non-zero status if setup
    failed in any way

    .. note::

       The ``ferenda-setup`` script that gets installed with ferenda is
       a tiny wrapper around this function.

    """
    # very basic cmd line handling
    force = ('--force' in sys.argv)
    verbose = ('--verbose' in sys.argv)
    unattended = ('--unattended' in sys.argv)
    if not setup(sys.argv, force, verbose, unattended):
        sys.exit(-1)


def setup(argv=None, force=False, verbose=False, unattended=False):
    """Creates a project, complete with configuration file and
    ferenda-build tool.

    Checks to see that all required python modules and command line
    utilities are present. Also checks which triple store(s) are
    available and selects the best one (in order of preference:
    Sesame, Fuseki, RDFLib+Sleepycat, RDFLib+SQLite), and checks which
    fulltextindex(es) are available and selects the best one (in order
    of preference: ElasticSearch, Whoosh)

    :param argv: a sys.argv style command line
    :type  argv: list
    :param force:
    :type  force: bool
    :param verbose:
    :type  verbose: bool
    :param unattended:
    :type  unattended: bool

    """
    log = setup_logger(logformat="%(message)s")

    if not argv:
        argv = sys.argv
    if len(argv) < 2:
        log.error("Usage: %s [project-directory]" % argv[0])
        return False
    projdir = argv[1]
    if os.path.exists(projdir) and not force:
        log.error("Project directory %s already exists" % projdir)
        return False
    sitename = os.path.basename(projdir)

    ok = _preflight_check(log, verbose)
    if not ok and not force:
        if unattended:
            answer = "n"
        else:
            log.info(
                "There were some errors when checking your environment. Proceed anyway? (y/N)")
            answer = input()
        if answer != "y":
            return False

    # The template ini file needs values for triple store
    # configuration. Find out the best triple store we can use.
    storetype, storelocation, storerepository = _select_triplestore(sitename, log, verbose)
    log.info("Selected %s as triplestore" % storetype)
    if not storetype:
        if unattended:
            answer = "n"
        else:
            log.info("Cannot find a useable triple store. Proceed anyway? (y/N)")
            answer = input()
        if answer != "y":
            return False

    indextype, indexlocation = _select_fulltextindex(log, sitename, verbose)
    log.info("Selected %s as search engine" % indextype)

    if not os.path.exists(projdir):
        os.makedirs(projdir)

    # step 1: create buildscript
    loader = ResourceLoader(".")
    buildscript = projdir + os.sep + "ferenda-build.py"
    util.resource_extract(loader,
                          'scripts/ferenda-build.py',
                          buildscript,
                          {})
    mode = os.stat(buildscript)[stat.ST_MODE]
    os.chmod(buildscript, mode | stat.S_IXUSR)

    # step 2: create config file
    configfile = projdir + os.sep + "ferenda.ini"
    util.resource_extract(loader,
                          'scripts/ferenda.template.ini',
                          configfile,
                          locals())

    log.info("Project created in %s" % projdir)

    # step 3: create WSGI app
    wsgifile = projdir + os.sep + "wsgi.py"
    util.resource_extract(loader,
                          'scripts/wsgi.py',
                          wsgifile,
                          {})
    shutdown_logger()
    return True

config_loaded = False

def load_config(filename=None, argv=None, defaults=None):
    """Loads general configuration information from ``filename`` (which
       should be a full path to a ferenda.ini file) and/or command
       line arguments into a :py:class:`~layeredconfig.LayeredConfig`
       instance. It contains a built-in dict of default configuration
       values which can be overridden by the config file or command
       line arguments.

    """
    global config_loaded
    if config_loaded is not False:
        # assert config_loaded is False, "load_config called more than once!"
        getlog().error("load_config called more than once!")
    if not defaults:
        # FIXME: Expand on this list of defaults? Note that it only
        # pertains to global configuration, not docrepo configuration
        # (those have the get_default_options() classmethod).
        defaults = copy.deepcopy(DEFAULT_CONFIG)
        
        for alias, classname in _enabled_classes(inifile=filename).items():
            assert alias not in defaults, "Collision on key %s" % alias
            defaults[alias] = _load_class(classname).get_default_options()
    sources = [Defaults(defaults)]
    if filename:
        sources.append(INIFile(filename))

    sources.append(Environment(prefix="FERENDA_"))
    if argv:
        parser = argparse.ArgumentParser()
        parser.add_argument("alias", metavar="REPOSITORY",
                            help="The repository to process (class or alias)")
        parser.add_argument("action", metavar="ACTION",
                            help="The action or command to perform")
        parser.add_argument("arguments", metavar="ARGS", nargs="*",
                            help="Any positional arguments to ACTION")
        # iterate argv and convert from bytes to strings using a
        # reasonable decoder
        cmdlineencoding = "utf-8"
        saneargv = []
        for arg in argv:
            if isinstance(arg, bytes):
                arg = arg.decode(cmdlineencoding)
            saneargv.append(arg)
        sources.append(Commandline(saneargv, parser=parser))

    config = LayeredConfig(*sources,
                           cascade=True)
    config_loaded = True
    return config


def _classes_from_classname(enabled, classname):
    """Given a classname or alias, returns a list of class objects.

    :param enabled: The currently enabled repo classes, as returned by
                    :py:func:`~ferenda.Manager._enabled_classes`
    :type  enabled: dict
    :param classname: A classname (eg ``'ferenda.DocumentRepository'``) or
                      alias  (eg ``'base'``). The special value ``'all'``
                      expands to all enabled classes.
    :returns: Class objects
    :rtype: list
    """

    classnames = _setup_classnames(enabled, classname)
    instances = [_load_class(x) for x in classnames]
    return instances


def _setup_makeresources_args(config):
    """Given a config object, returns a dict with some of those
    configuration options, but suitable as arguments for
    :py:func:`ferenda.Manager.makeresources`.

    :param config: An initialized config object with data from a ferenda.ini
                   file
    :type config: layered.LayeredConfig
    :returns: A subset of the same configuration options
    :rtype: dict

    """
    return {'resourcedir': config.datadir + os.sep + 'rsrc',
            'combine':     config.combineresources,
            'staticsite':  config.staticsite,
            'cssfiles':    config.cssfiles,
            'jsfiles':     config.jsfiles,
            'imgfiles':    config.imgfiles,
            'sitename':    config.sitename,
            'sitedescription': config.sitedescription,
            'url':         config.url,
            'legacyapi':   config.legacyapi,
            'disallowrobots': config.disallowrobots
            }


def _setup_classnames(enabled, classname):
    """Converts an alias (as enabled in a ferenda.ini file) to a fully
    qualified class name. If the special alias "all" is used, return
    the class names of all enabled repositories.

    Note: a list is always returned, even when the classname ``'all'``
    is not used. If a fully qualified classname is provided, a list
    with the same string is returned.

    :param enabled: The currently enabled repo classes, as returned by
                    :py:func:`~ferenda.Manager._enabled_classes`
    :type  enabled: dict
    :param classname: A classname (eg ``'ferenda.DocumentRepository'``) or
                      alias  (eg ``'base'``). The special value ``'all'``
                      expands to all enabled classes.
    :returns: Class names (as strings)
    :rtype: list
    """
    # "w3c" => ['ferenda.sources.tech.W3Standards']
    # "all" => ['ferenda.sources.tech.W3Standards', 'ferenda.sources.tech.RFC']
    if classname == "all":
        # wonder why we filtered out ferenda.Devel -- does it cause problems with "./ferenda-build.py all [action]" ?
        # return [v for v in enabled.values() if v != 'ferenda.Devel']
        return enabled.values()
    else:
        if classname in enabled:
            classname = enabled[classname]
        return [classname]


class _WrappedKeyboardInterrupt(Exception):

    """Internal class. Wraps a KeyboardInterrupt (which does not inherit
    from :py:exc:`Exception`, but rather :py:exc:`BaseException`) so
    that it can be passed between processes by :py:mod:`multiprocessing`.
    """
    pass


def _run_class(enabled, argv, config):
    """Runs a particular action for a particular class.

    :param enabled: The currently enabled repo classes, as returned by
                    :py:func:`~ferenda.Manager._enabled_classes`
    :type  enabled: dict
    :param argv: An argv-style list of strings, see run (but note
                 that run() replaces ``all`` with every
                 enabled class in turn and then calls this method
                 with the same argv.
    :type argv: list
    :param config: A config object
    :type  config: layeredconfig.LayeredConfig

    If the parameter ``--all`` is given (e.g. ``['myrepo', 'parse',
    '--all']``), the specified command is run once for every available
    file for that action.

    """
    log = getlog()
    alias = argv[0]
    action = argv[1]
    with util.logtime(log.info,
                      "%(alias)s %(action)s finished in %(elapsed).3f sec",
                      {'alias': alias, 'action': action}):
        _enabled_classes = dict(reversed(item) for item in enabled.items())
        if alias not in enabled and alias not in _enabled_classes:
            log.error("Class-or-alias '%s' not enabled" % alias)
            return
        if alias in argv:
            argv.remove(alias)
        # ie a fully qualified classname was used
        if alias in _enabled_classes:
            classname = alias
        else:
            classname = enabled[alias]
        cls = _load_class(classname)
        inst = _instantiate_class(cls, config, argv=argv)
        try:
            clbl = getattr(inst, action)
            assert(callable(clbl))
        except:  # action was None or not a callable thing
            if action:
                log.error("%s is not a valid command for %s" %
                          (action, classname))
            else:
                log.error("No command given for %s" % classname)
            _print_class_usage(cls)
            return

        kwargs = {}
        if action in ('relate', 'generate', 'transformlinks', 'toc', 'news'):
            # we need to provide the otherrepos parameter to get
            # things like URI transformation to work. FIXME: However we might
            # not need all repos (ie. not repos where relate or even
            # tabs is set to false)
            otherrepos = []
            for othercls in _classes_from_classname(enabled, 'all'):
                if othercls != inst.__class__:
                    obj = _instantiate_class(othercls, config, argv=argv)
                    if getattr(obj.config, action, True):
                        otherrepos.append(obj)
            kwargs['otherrepos'] = otherrepos

        if 'all' in inst.config and inst.config.all is True:
            # create an iterable that yields (basefile, version)
            # pairs. If config.allversions is not set to True, the
            # version element will always be None (meaning we'll only
            # parse the current version, not any archived versions)
            iterable = inst.store.list_basefiles_for(action, force=inst.config.force)
            if inst.config.allversions:
                iterable = inst.store.list_versions_for_basefiles(iterable, action)
            else:
                iterable = ((x, None) for x in iterable)
            if action == "parse" and not inst.config.force:
                # if we don't need to parse all basefiles, let's not
                # even send jobs out to buildclients if we can avoid
                # it
                iterable = ((b,v) for b,v in iterable if inst.store.needed(b, "parse", v))
            res = []
            # semi-magic handling
            kwargs['currentrepo'] = inst
            ret = cls.setup(action, inst.config, **kwargs)
            del kwargs['currentrepo']
            if ret is False:
                log.info("%s %s: Nothing to do!" % (alias, action))
            else:
                # Now we have a list of jobs in the iterable. They can
                # be processed in four different ways:
                #
                if LayeredConfig.get(config, 'buildserver'):
                    # - start an internal jobqueue to which buildclients
                    #   connect, and send jobs to it (and read results
                    #   from a similar resultqueue)
                    res = _queuejobs(iterable, inst, classname, action)
                elif LayeredConfig.get(config, 'buildqueue'):
                    # - send jobs to an external jobqueue process to which
                    #   buildclients connect (and read results from a
                    #   similar resultqueue)
                    res = _queuejobs_to_queue(iterable, inst, classname, action)
                elif inst.config.processes != '1':
                    processes = _process_count(inst.config.processes)
                    # - start a number of processess which read from a
                    #   shared jobqueue, and send jobs to that queue (and
                    #   read results from a shared resultqueue)
                    res = _parallelizejobs(
                        iterable,
                        inst,
                        classname,
                        action,
                        processes,
                        argv)
                else:
                    # - run the jobs, one by one, in the current process
                    for (basefile, version) in iterable:
                        with adaptlogger(inst, basefile, version):
                            r = _run_class_with_basefile(
                                clbl,
                                basefile,
                                version,
                                kwargs,
                                action,
                                alias)
                        res.append(r)
                cls.teardown(action, inst.config)
        else:
            # The only thing that kwargs may contain is a 'otherrepos'
            # parameter.
            if len(config.arguments) == 1 and action in ('parse', 'generate', 'transformlinks'):
                basefile = config.arguments[0]
                version = getattr(config, 'version', None)
                with adaptlogger(inst, basefile, version):
                    res = _run_class_with_basefile(clbl, basefile, None, kwargs, action, alias)
                adjective = {'parse': 'downloaded',
                             'generate': 'parsed',
                             'transformlinks': 'generated'}
                if config.allversions and not version:
                    res = [res]
                    for version in inst.store.list_versions(basefile, adjective.get(action, action)):
                        with adaptlogger(inst, basefile, version):
                            res = _run_class_with_basefile(clbl, basefile, version, kwargs, action, alias)
            else:
                # NOTE: This is a shorter version of the error
                # handling that _run_class_with_basefile does. We want
                # to propagate all errors except DocumentRemoved.
                try:
                    res = clbl(*config.arguments, **kwargs)
                except errors.DocumentRemovedError as e:
                    if e.dummyfile:
                        util.writefile(e.dummyfile, "")
                    raise e
                except Exception as e:
                    loc = util.location_exception(e)
                    log.error("%s %s failed: %s (%s)" %
                              (action, alias, e, loc))
                    raise e
    return res

# The functions runbuildclient, _queuejobs, _make_client_manager,
# __make_server_manager, _run_jobqueue_multiprocessing and
# _build_worker are based on the examples in
# http://eli.thegreenplace.net/2012/01/24/distributed-computing-in-python-with-multiprocessing/


def runbuildclient(clientname,
                   serverhost,
                   serverport,
                   authkey,
                   processes):
    done = False
    # _run_jobqueue_multiprocessing > _build_worker might throw an exception,
    # which is how we exit
    getlog().info("%s starting up buildclient with %s processes" % (clientname, processes))
    while not done:
        manager = _make_client_manager(serverhost,
                                       serverport,
                                       authkey)
        job_q = manager.jobqueue()
        result_q = manager.resultqueue()
        _run_jobqueue_multiprocessing(job_q, result_q, processes, clientname)
        # getlog().debug("Client: [pid %s] All done with one run, _run_jobqueue_multiprocessing returned happily" % os.getpid())
        done = True


def _make_client_manager(ip, port, authkey):
    """Create a manager for a client. This manager connects to a server
        on the given address and exposes the jobqueue and
        resultqueue methods for accessing the shared queues from the
        server.  Return a manager object.

    """
    # FIXME: caller should be responsible for setting these to proper
    # values
    if isinstance(ip, bool):
        ip = '127.0.0.1'
    if isinstance(port, str):
        port = int(port)
    if isinstance(authkey, str):
        # authkey must be bytes
        authkey = authkey.encode("utf-8")

    class ServerQueueManager(SyncManager):
        pass

    ServerQueueManager.register(jobqueue_id)
    ServerQueueManager.register(resultqueue_id)

    while True:
        try:
            manager = ServerQueueManager(address=(ip, port), authkey=authkey)
            manager.connect()
            getlog().debug('Client: [pid %s] connected to %s:%s' % (os.getpid(), ip, port))
            return manager
        except Exception as e:
            sleep(2)


def _run_jobqueue_multiprocessing(jobqueue, resultqueue, nprocs, clientname):
    """ Split the work with jobs in jobqueue and results in
        resultqueue into several processes. Launch each process with
        factorizer_worker as the worker function, and wait until all are
        finished.
    """
    procs = _start_multiprocessing(jobqueue, resultqueue, nprocs, clientname)
    _finish_multiprocessing(procs)


def _start_multiprocessing(jobqueue, resultqueue, nprocs, clientname):
    procs = []
    log = getlog()
    # log.debug("Client: [pid %s] about to start %s processes" % (os.getpid(), nprocs))
    for i in range(nprocs):
        p = _start_proc(jobqueue, resultqueue, clientname)
        procs.append(p)
        log.debug("Client: [pid %s] Started process %s" % (os.getpid(), p.pid))
    return procs

def _start_proc(jobqueue, resultqueue, clientname):
        p = multiprocessing.Process(
            target=_build_worker,
            args=(jobqueue, resultqueue, clientname))
        p.start()
        return p

    
def _finish_multiprocessing(procs, join=True):
    # we could either send a DONE signal to each proc or we could just
    # kill them
    for p in procs:
        if join:  # in distributed mode
            p.join()
        else:  # in multiproc mode
            # getlog().debug("Server: killing proc %s" % p.pid)
            p.terminate()


def _build_worker(jobqueue, resultqueue, clientname):
    """A worker function to be launched in a separate process. Takes jobs
        from jobqueue - each job a dict. When the job is done, the
        result is placed into resultqueue. Runs until instructed to
        quit.

    """
    # create the inst with a default config
    # (_instantiate_class will try to read ferenda.ini)
    insts = {}
    repos = {}
    log = getlog()
    log.debug("Client: [pid %s] _build_worker ready to process job queue" % os.getpid())
    logrecords = []
    while True:
        try:
            job = jobqueue.get()  # get() blocks -- wait until a job or the
                                  # DONE/SHUTDOWN signal comes
        except (EOFError, BrokenPipeError) as e:
            getlog().error("%s: Couldn't get a new job from the queue, buildserver "
                           "probably done?" % os.getpid())
            return
        if job == "DONE":  # or a more sensible value
            # getlog().debug("Client: [pid %s] Got DONE signal" % os.getpid())
            return  # back to runbuildclient
        if job == "SHUTDOWN":
            # getlog().debug("Client: Got SHUTDOWN signal")
            # kill the entire thing
            raise Exception("OK we're done now")
        if job['classname'] not in insts:
            insts[job['classname']] = _instantiate_and_configure(job['classname'],
                                                                 job['config'],
                                                                 logrecords,
                                                                 clientname)
            # need to get hold of log as well
        # log.debug("Client: [pid %s] Starting job %s %s %s" % (os.getpid(), job['classname'], job['command'], job['basefile']))
        # Do the work
        clbl = getattr(insts[job['classname']], job['command'])
        # kwargs = job['kwargs']   # if we ever support that
        kwargs = {}

        # For some commands (relate, generate, transformlinks) this
        # child process need to instantiate the correct set of
        # otherrepos and add it to kwargs. Note that the child
        # processes should instantiate these themselves, not get them
        # from the parent process (would that even work?)
        if job['command'] in ('relate', 'generate', 'transformlinks'):
            if job['classname'] not in repos:
                otherrepos = []
                inst = insts[job['classname']]
                for alias, classname in _enabled_classes().items():
                    if alias != inst.alias:
                        obj = _instantiate_and_configure(classname, job['config'], logrecords, clientname)
                        if getattr(obj.config, job['command'], True):
                            otherrepos.append(obj)
                repos[job['classname']] = otherrepos
            kwargs['otherrepos'] = repos[job['classname']]
                        
        # proctitle = re.sub(" [now: .*]$", "", getproctitle())
        proctitle = getproctitle()
        newproctitle = proctitle + " [%(alias)s %(command)s %(basefile)s]" % job
        if job['version']:
            newproctitle = newproctitle[:-1] + "@" + job['version'] + newproctitle[-1]
        setproctitle(newproctitle)
        with adaptlogger(insts[job['classname']], job['basefile'], job['version']):
            res = _run_class_with_basefile(clbl, job['basefile'],
                                           job['version'],
                                           kwargs, job['command'],
                                           job['alias'],
                                           wrapctrlc=True)
        setproctitle(proctitle)
        log.debug("Client: [pid %s] %s finished: %s" % (os.getpid(), job['basefile'], res))
        outdict = {'basefile': job['basefile'],
                   'version': job['version'],
                   'alias': job['alias'],
                   'result':  res,
                   'log': list(logrecords),
                   'client': clientname}
        logrecords[:] = []
        try:
            resultqueue.put(outdict)
            if clientname and log.level < logging.CRITICAL:
                sys.stdout.write(".")
                sys.stdout.flush()

        except EOFError as e:
            print("%s: Result of %s %s %s couldn't be put on resultqueue" % (
                os.getpid(), job['classname'], job['command'], job['basefile']))
        except (TypeError, AttributeError, RemoteError) as e:
            # * TypeError: Has happened with a "can't pickle
            #   pyexpat.xmlparser objects". Still not sure what was
            #   the cause of that.
            # * AttributeError is probably a "Can't pickle local object
            #   'RDFXMLHandler.reset.<locals>.<lambda>'" error --
            #   similar to the difficulties of pickling ParseErrors
            #   above
            # * RemoteError has happened when the result was an lxml.etree.ParseError
            #   exception, as that one couldn't be pickled and unpickled without
            #   problems. So we wrapped it in a ParseErrorWrapper at one end and
            #   unwrapped it on the other end, so now there's no need for this hack.
            print("%s: Catastrophic error %s" % (job['basefile'], e))
            resultqueue.put({'basefile': job['basefile'],
                             'version': job['version'],
                             'result': None,
                             'log': list(logrecords),
                             'client': clientname})
        # log.debug("Client: [pid %s] Put '%s' on the queue" % (os.getpid(), outdict['result']))


def _instantiate_and_configure(classname, config, logrecords, clientname):
    log = getlog()
    # print("Client [pid %s]: supplied config is %s" % (os.getpid(), config))
    log.debug(
        "Client: [pid %s] instantiating and configuring %s" %
        (os.getpid(), classname))
    inst = _instantiate_class(_load_class(classname))
    inst.config.clientname = clientname
    for k, v in config.items():
        LayeredConfig.set(inst.config, k, v)
        # if getattr(inst.config, k) != v:
        #    print("pid %s: config %s is %s, should be %s" %
        #          (os.getpid(), k, getattr(inst.config, k), v))

    # When running in distributed mode (but not in multiprocessing
    # mode), setup the root logger to log to a StringIO buffer.
    if clientname:
        # log.debug("Client: [pid %s] Setting up log" % os.getpid())
        # log = setup_logger(inst.config.loglevel)
        log = setup_logger(config.get('loglevel', 'INFO'))
        for handler in list(log.handlers):
            log.removeHandler(handler)
        handler = MarshallingHandler(logrecords)
        log.addHandler(handler)
        # print("Client: [pid %s] Settings log to %s" % (os.getpid(), config.get('loglevel', None)))
        log.setLevel(config.get('loglevel', 'INFO'))
    # log.debug("Client: [pid %s] Log is configured" % os.getpid())
    else:
        pass
        # FIXME: change the logformat to include pid
    return inst


def _queuejobs(iterable, inst, classname, command):
    # Start a shared manager server and access its queues
    # NOTE: _make_server_manager reuses existing buildserver if there is one
    manager = _make_server_manager(port=inst.config.serverport,
                                   authkey=inst.config.authkey)
    return _queue_jobs(manager, iterable, inst, classname, command)


def _queuejobs_to_queue(iterable, inst, classname, command):
    manager = _make_client_manager(inst.config.buildqueue,
                                   inst.config.serverport,
                                   inst.config.authkey)
    return _queue_jobs(manager, iterable, inst, classname, command)


def __queue_jobs_nomanager(jobqueue, iterable, inst, classname, command):
    log = getlog()
    default_config = _instantiate_class(_load_class(classname)).config
    client_config = {}
    for k in inst.config:
        if (k not in ('all', 'logfile', 'buildserver', 'buildqueue', 'serverport', 'authkey') and
            (LayeredConfig.get(default_config, k) !=
             LayeredConfig.get(inst.config, k))):
            client_config[k] = LayeredConfig.get(inst.config, k)
    # print("Server: Extra config for clients is %r" % client_config)
    basefiles = []
    for idx, basefile in enumerate(iterable):
        job = {'basefile': basefile[0],
               'version': basefile[1],
               'classname': classname,
               'command': command,
               'alias': inst.alias,
               'config': client_config}
        # log.debug("Server: putting %r into jobqueue" %  job['basefile'])
        jobqueue.put(job)
        basefiles.append(basefile)
    log.debug("Server: Put %s jobs into job queue" % len(basefiles))
    return basefiles


def _queue_jobs(manager, iterable, inst, classname, command):
    def format_tupleset(s):
        return ", ".join(("%s:%s" % (t[0], t[1])) for t in s)
    jobqueue = manager.jobqueue()
    resultqueue = manager.resultqueue()
    log = getlog()
    processing = set()
    # we'd like to just provide those config parameters that diff from
    # the default (what the client will already have), ie.  those set
    # by command line parameters (or possibly env variables)
    default_config = _instantiate_class(_load_class(classname)).config
    client_config = {}
    for k in inst.config:
        if (k not in ('all', 'logfile', 'buildserver', 'buildqueue', 'serverport', 'authkey') and
            (LayeredConfig.get(default_config, k) !=
             LayeredConfig.get(inst.config, k))):
            client_config[k] = LayeredConfig.get(inst.config, k)
    log.debug("Server: Extra config for clients is %r" % client_config)
    idx = -1
    for idx, basefile in enumerate(iterable):
        job = {'basefile': basefile[0],
               'version': basefile[1],
               'classname': classname,
               'command': command,
               'alias': inst.alias,
               'config': client_config}
        # print("putting %r into jobqueue" %  job)
        jobqueue.put(job)
        processing.add((inst.alias,basefile))
    res = []
    numres = 0
    if len(processing) == 0:
        return res
    log.info("%s: Put %s jobs into job queue" % (inst.alias, len(processing)))
    # FIXME: only one of the clients will read this DONE package, and
    # we have no real way of knowing how many clients there will be
    # (they can come and go at will). Didn't think this one through...
    # jobqueue.put("DONE")
    res = []
    clients = Counter()
    signal.signal(signal.SIGALRM, _resultqueue_get_timeout)
    # FIXME: be smart about how long we wait before timing out the resultqueue.get() call
    timeout_length = 900 
    while len(processing) > 0:
        try:
            r = resultqueue.get()
        except TimeoutError:
            log.critical("Timeout: %s jobs not processed (%s)" % (len(processing), format_tupleset(processing)))
            processing.clear()
            continue
        signal.alarm(timeout_length)
        if (r['alias'], (r['basefile'], r['version'])) not in processing:
            if r['alias'] == inst.alias:
                log.warning("%s not found in processing (%s)" % (r['basefile'], format_tupleset(processing)))
            else:
                log.warning("%s from repo %s was straggling, better late than never" % (r['basefile'], r['alias']))
        processing.discard((r['alias'], (r['basefile'], r['version'])))
        if isinstance(r['result'], tuple) and r['result'][0] == _WrappedKeyboardInterrupt:
            raise KeyboardInterrupt()
        elif isinstance(r['result'], tuple) and isinstance(r['result'][0], Exception):
            r['except_type'] = r['result'][0]
            r['except_value'] = r['result'][1]
            if r['except_type'] == ParseErrorWrapper:
                code, line, column, message = r['except_value'].split("|", 3)
                r['except_type'] = lxml.etree.ParseError
                r['except_value'] = lxml.etree.ParseError(message, code, line, column)
            log.error(
                "Server: %(client)s failed %(basefile)s: %(except_type)s: %(except_value)s" %
                r)
            print("".join(traceback.format_list(r['result'][2])))
        else:
            for record in r['log']:
                _log_record(record, r['client'], log)
            log.debug(
                "Server: client %(client)s processed %(basefile)s: Result (%(result)s): OK" %
                r)
        if 'client' in r:
            clients[r['client']] += 1
        if 'result' in r and r['alias'] == inst.alias:
            res.append(r['result'])
        numres += 1

    # ok, now we don't need to worry about timeouts anymore
    signal.alarm(0)
    # sort clients on name, not number of jobs
    clientstats = ", ".join(["%s: %s jobs" % (k, v) for k,v in sorted(clients.items())])
    log.info("%s: %s jobs processed. %s" % (inst.alias, numres, clientstats))
    return res
    # sleep(1)
    # don't shut this down --- the toplevel manager.run call must do
    # that
    # manager.shutdown()

def _log_record(marshalled_record, clientname, log):
    record = logging.makeLogRecord(pickle.loads(marshalled_record))
    record.msg = "[%s] %s" % (clientname, record.msg)
    log.handle(record)
                            
buildmanager = None
if sys.version_info[0] < 3:
    jobqueue_id = b'jobqueue'
    resultqueue_id = b'resultqueue'
else:
    jobqueue_id = 'jobqueue'
    resultqueue_id = 'resultqueue'


def _make_server_manager(port, authkey, start=True):
    """ Create a manager for the server, listening on the given port.
        Return a manager object with jobqueue and resultqueue methods.
    """
    global buildmanager
    if not buildmanager:
        if isinstance(port, str):
            port = int(port)
        job_q = Queue()
        result_q = Queue()

        # This is based on the examples in the official docs of
        # multiprocessing.  get_{job|result}_q return synchronized
        # proxies for the actual Queue objects.
        class JobQueueManager(SyncManager):
            pass

        
        JobQueueManager.register(jobqueue_id, callable=lambda: job_q)
        JobQueueManager.register(resultqueue_id, callable=lambda: result_q)

        if isinstance(authkey, str):
            # authkey must be bytes
            authkey = authkey.encode("utf-8")

        buildmanager = JobQueueManager(address=('', port), authkey=authkey)
        getlog().debug(
            "Server: Process %s created new buildmanager at %s" %
            (os.getpid(), id(buildmanager)))
        if start:  # runbuildqueue wants to control this itself
            buildmanager.start()
            getlog().debug('Server: Started at port %s' % port)

    return buildmanager


def runbuildqueue(serverport, authkey):
    # NB: This never returns!
    manager = _make_server_manager(serverport, authkey, start=False)
    getlog().debug("Queue: Starting server manager with .serve_forever()")
    manager.get_server().serve_forever()


def _shutdown_buildserver():
    global buildmanager
    if buildmanager:
        getlog().debug("Server: Shutting down buildserver")
        buildmanager.shutdown()
        buildmanager = None
        sleep(1)


def _parallelizejobs(iterable, inst, classname, command, processes, argv):
    jobqueue = multiprocessing.Queue()
    resultqueue = multiprocessing.Queue()
    procs = _start_multiprocessing(jobqueue, resultqueue, processes, None)
    try:
        basefiles = __queue_jobs_nomanager(jobqueue, iterable, inst, classname, command)
        res = _process_resultqueue(resultqueue, basefiles, procs, jobqueue, None)
        return res
    finally:
        _finish_multiprocessing(procs, join=False)


def _process_resultqueue(resultqueue, basefiles, procs, jobqueue, clientname):
    res = {}
    queuelength = len(basefiles)
    log = getlog()
    signal.signal(signal.SIGALRM, _resultqueue_get_timeout)
    for i in range(queuelength):
        # check if all procs are still alive?
        all_alive = True
        dead = []
        for p in procs:
            if not p.is_alive():
                log.error("Process %s is not alive!!!" % p.pid)
                all_alive = False
                dead.append(p)
        for p in dead:
            p.terminate()  ## needed?
            procs.remove(p)
            newp = _start_proc(jobqueue, resultqueue, clientname)
            log.info("Client: [pid %s] Started new process %s" % (os.getpid(), newp.pid))
            procs.append(newp)
        try:
            r = resultqueue.get()
            # after we recieve the first result, we expect to find new
            # results at least every n seconds until we're done. If
            # we're stalled longer than that, it probably means that
            # some client have failed sending us a result on the
            # queue
            # FIXME: be smart about selecting a suitable timeout
            signal.alarm(900)
            if isinstance(r['result'], tuple) and r['result'][0] == _WrappedKeyboardInterrupt:
                raise KeyboardInterrupt()
            res[r['basefile']] = r['result']
        except TypeError as e:
            # This can happen, and it seems like an error with
            # multiprocessing.queues.get, which calls
            # ForkingPickler.loads(res), which then crashes deep into
            # lxmls C code with the weird "__init__() takes exactly 5
            # positional arguments (2 given)"
            log.error("result could not be decoded: %s" % e)
            # now we'll have a basefile without a result -- maybe we should indicate somehow
    signal.alarm(0)
    # return the results in the same order as they were queued. If we
    # miss a result for a particular basefile, return a catastropic
    # error saying we couldn't get the result
    return [res.get(b, {'basefile': b,
                        'result': False,
                        'log': 'CATASTROPHIC ERROR (couldnt decode result from client)',
                        'client': 'unknown'}) for b, v in basefiles]

def _resultqueue_get_timeout(signum, frame):
    # get a list of sent jobs and recieved results. determine which
    # are missing, and report. Then blow up in spectacular fashion, or
    # preferably do something that'll allow us to cancel the
    # resultqueue.get() call
    print("_resultqueue_get_timeout called! pid: %s" % os.getpid())
    raise TimeoutError()


def _siginfo_handler(signum, frame):
    # walk up to the calling frame in manager (or any other ferenda code)
    while "ferenda" not in frame.f_code.co_filename:
        frame = frame.f_back
        if frame is None:
            print("_siginfo_handler: couldn't find ferenda code in the current stack")
    # at this point, we can maybe print general info abt current
    # frame, and for some locations/functions maybe a status (ie
    # recieved x out of y expected results). Mostly information useful
    # in determining why the process is stuck...  an alternative to
    # this might just be to drop into p(u)db
    print("In %s (%s:%s)" % (frame.f_code.co_name, frame.f_code.co_filename, frame.f_lineno))
    if frame.f_code.co_name == "_queue_jobs":
        print("Queued %s jobs, recieved %s results" % (frame.f_locals['number_of_jobs'], len(frame.f_locals['res'])))
    
    
def _run_class_with_basefile(clbl, basefile, version, kwargs, command,
                             alias="(unknown)", wrapctrlc=False):
    try:
        # This doesn't work great with @managedparsing (particularly
        # the @makedocument decorator changes the method signature
        # from (self, baseefile, version) to (self, doc)
        # if version and 'version' not in inspect.signature(clbl).parameters:
        #     getlog().warning("%s %s: Called with basefile %s and version %s, but %s doesn't support version parameter" % (alias, command, basefile, version, command))
        if version:
            kwargs['version'] = version
        return clbl(basefile, **kwargs)
    except errors.DocumentRemovedError as e:
        errmsg = str(e)
        getlog().error("%s %s %s failed! %s" %
                       (alias, command, basefile, errmsg))
        if hasattr(e, 'dummyfile') and e.dummyfile:
            if not os.path.exists(e.dummyfile):
                util.writefile(e.dummyfile, "")
            return None  # is what DocumentRepository.parse returns
            # when everyting's ok
        else:
            exc_type, exc_value, tb = sys.exc_info()
            return exc_type, exc_value, traceback.extract_tb(tb)
    except lxml.etree.ParseError:
        # one wierdness: If exc_type is lxml.etree.ParseError,
        # that exception expects to be initialized with 5
        # arguments ( with %s" % job['basefile'])(self, message,
        # code, line, column). The default unserialization doesn't
        # seem to support that, calling the constructor with only
        # 2 args (self, message). So if we get that particular
        # error, stuff the extra args in the message of our own
        # substitute exception.
        # 
        # FIXME: Maybe this could be done by registering custom
        # picklers for ParseError objects, see the copyreg module
        # and https://stackoverflow.com/a/25994232/2718243
        exc_type, exc_value, tb = sys.exc_info()
        exc_type = ParseErrorWrapper
        msg = "%s|%s|%s|%s" % (exc_value.code, exc_value.lineno, exc_value.position[1], exc_value.msg)
        exc_value = ParseErrorWrapper(msg)
        return exc_type, exc_value, traceback.extract_tb(tb)
    except Exception as e:
        if 'bdb.BdbQuit' in str(type(e)):
            raise
        errmsg = str(e)
        loc = util.location_exception(e)
        label = basefile + ("@%s" % version if version else "")
        getlog().error("%s %s %s failed: %s (%s)" %
                       (alias, command, label, errmsg, loc))
        exc_type, exc_value, tb = sys.exc_info()
        return exc_type, exc_value, traceback.extract_tb(tb)
    except KeyboardInterrupt as e:   # KeyboardInterrupt is not an Exception
        if wrapctrlc:
            except_type, except_value, tb = sys.exc_info()
            return _WrappedKeyboardInterrupt, _WrappedKeyboardInterrupt(
            ), traceback.extract_tb(tb)
        else:
            raise
    # FIXME: should we add BDBQuit here for practiality?


def _instantiate_class(cls, config=None, argv=[]):
    """Given a class object, instantiate that class and make sure the
       instance is properly configured given it's own defaults, a
       config file, and command line parameters."""
    clsdefaults = cls.get_default_options()
    if not config:
        defaults = dict(clsdefaults)
        defaults[cls.alias] = {}
        config = LayeredConfig(Defaults(defaults),
                               INIFile(find_config_file()),
                               Commandline(argv),
                               cascade=True)
    clsconfig = getattr(config, cls.alias)

    # work in all parameters from get_default_options unless they have
    # been set by some other means
    clsconfig_parameters = list(clsconfig)
    for param in clsdefaults:
        if param not in clsconfig_parameters:
            # the set method sets the parameter on an appropriate
            # store w/o dirtiying it.
            LayeredConfig.set(clsconfig, param, clsdefaults[param], "defaults")
            # setattr(clsconfig, param, clsdefaults[param])

    # FIXME: this is super hacky, but we'd like to make sure that
    # source[0] (the Defaults source) has all type values from
    # clsdefaults. Need to rethink how we initialize the main config
    # object w.r.t. get_default_options() (Maybe: that function could
    # be a staticmethod and called for all enabled repos beforehand,
    # so that we can create the main Defaults object with all repos).
    assert isinstance(clsconfig._sources[0], Defaults)
    for param, value in clsdefaults.items():
        if not isinstance(value, type):
            continue
        if param not in clsconfig._sources[0].source:
            clsconfig._sources[0].source[param] = value

    inst = cls(clsconfig)
    return inst


def _enabled_classes(inifile=None):
    """Returns a mapping (alias -> classname) for all registered classes.

    >>> enable("ferenda.DocumentRepository") == 'base'
    True
    >>> _enabled_classes() == {'base': 'ferenda.DocumentRepository'}
    True
    >>> os.unlink("ferenda.ini")

    :param inifile: The full path to a ferenda.ini file. If None, attempts
                    to find ini file using
                    :py:func:`ferenda.Manager.find_config_file`
    :type inifile: str
    :returns: A mapping between alias and classname for all registered classes.
    :rtype: dict

    """

    cfg = configparser.ConfigParser()
    if not inifile:
        inifile = find_config_file()

    cfg.read([inifile])
    enabled = OrderedDict()
    for section in cfg.sections():
        if cfg.has_option(section, "class"):
            enabled[section] = cfg.get(section, "class")
    return enabled


def _print_usage():
    """Prints out general usage information for the ``ferenda-build.py`` tool."""
    print = builtins.print
    # general info, enabled classes
    executable = sys.argv[0]
    print("""Usage: %(executable)s [class-or-alias] [action] <arguments> <options>
   e.g. '%(executable)s ferenda.sources.EurlexCaselaw enable'
        '%(executable)s ecj parse 62008J0042'
        '%(executable)s all generate'""" % locals())

    enabled = _list_enabled_classes()
    if enabled:
        print("Available modules:")
        for (alias, desc) in enabled.items():
            print(" * %s: %s" % (alias, desc))


def _list_enabled_classes():
    """Returns a mapping (alias -> description) for all registered classes.

    >>> enable("ferenda.DocumentRepository") == 'base'
    True
    >>> _list_enabled_classes() == {'base': 'Base class for handling a repository of documents.'}
    True
    >>> os.unlink("ferenda.ini")

    :returns: a mapping (alias -> description) for all registered classes
    :rtype: dict

    """
    res = OrderedDict()
    for (alias, classname) in _enabled_classes().items():
        cls = _load_class(classname)
        if cls.__doc__:
            res[alias] = cls.__doc__.split("\n")[0]
        else:
            res[alias] = "[Undocumented]"
    return res


def _print_class_usage(cls):
    """Given a class object, print out which actions are defined for that class.

    :param cls: The class object to print usage information for
    :type  cls: class
    """
    print = builtins.print
    print("Valid actions are:")
    actions = _list_class_usage(cls)
    for action, desc in actions.items():
        print(" * %s: %s" % (action, desc))


def _list_class_usage(cls):
    """Given a class object, list the defined actions (with descriptions)
    for that class.

    >>> _list_class_usage(DocumentRepository) == {
    ...     'download':'Downloads all documents from a remote web service.',
    ...     'parse':'Parse downloaded documents into structured XML and RDF.',
    ...     'relate':'Runs various indexing operations for the document.',
    ...     'generate':'Generate a browser-ready HTML file from structured XML and RDF.',
    ...     'transformlinks':'Transform links in generated HTML files.'}
    True

    Note: Descriptions are taken from the first line of the action
    methods' docstring.

    :param cls: The class to list usage for.
    :type cls: class
    :return: a mapping of (action -> description) for a specified class.
    :rtype: dict

    """
    res = OrderedDict()
    for attrname in dir(cls):
        attr = getattr(cls, attrname)
        if type(attr).__module__.startswith("rdflib."):
            continue
        if hasattr(attr, "runnable"):
            doc = attr.__doc__
            if doc:
                res[attr.__name__] = doc.split("\n")[0]
            else:
                res[attr.__name__] = "(Undocumented)"
    return res


def _filter_argv_options(args):
    options = []
    for arg in args:
        if arg.startswith("--"):
            options.append(arg)
    return options


def _load_class(classname):
    """Given a classname, imports and returns the corresponding class object.

    :param classname: A fully qualified class name
    :type classname: str
    :returns: Corresponding class object
    :rtype: class
    """
    if "." in classname:
        (modulename, localclassname) = classname.rsplit(".", 1)
    else:
        raise ValueError(
            "Classname '%s' should be the fully qualified name of a class (i.e. 'modulename.%s')" %
            (classname, classname))
    # NOTE: Don't remove this line! (or make sure testManager works after you do)
    log = getlog()

    __import__(modulename)
    # __import__ returns the topmost module, ie if one attempts to
    # import "ferenda.sources.SKVFS" it returns ferenda. But the
    # lowermost module is available from sys.modules
    # print("modulename: %s, localclassname: %s" % (modulename,localclassname))
    # print("sys.modules: %s" % sys.modules.keys())
    m = sys.modules[modulename]
    classes = dict(inspect.getmembers(m, inspect.isclass))
    for name, cls in list(classes.items()):
        if name == localclassname:
            return cls
    raise ImportError("No class named '%s'" % classname)


def find_config_file(path=None, create=False):
    """
    :returns: the full path to the configuration ini file
    """
    if not path:
        path = os.getcwd()
    inipath = path + os.sep + "ferenda.ini"
    if not create and not os.path.exists(inipath):
        raise errors.ConfigurationError(
            "Config file %s not found (relative to %s)" % (inipath, os.getcwd()))
    return inipath

def _setup_frontpage_args(config, argv):
    # FIXME: This way of instantiating repo classes should maybe be
    # used by _setup_makeresources_args as well?
    #
    # FIXME: why do we pass a config object when we re-read
    # ferenda.ini at least twice (_enabled_classes and
    # _instantiate_class) ?!
    # reads only ferenda.ini using configparser rather than layeredconfig
    enabled = _enabled_classes()
    repoclasses = _classes_from_classname(enabled, classname="all")
    repos = []
    for cls in repoclasses:
        inst = _instantiate_class(cls, config, argv)
        repos.append(inst)
    if 'develurl' in config:
        develurl = config.develurl
    else:
        develurl = None
    return {'sitename': config.sitename,
            'path': config.datadir + "/index.html",
            'staticsite': config.staticsite,
            'develurl': develurl,
            'removeinvalidlinks': config.removeinvalidlinks,
            'repos': repos}


def _process_count(setting):
    if setting == 'auto':
        return multiprocessing.cpu_count()
    else:
        return int(setting)
    

def _setup_buildclient_args(config):
    import socket
    return {'clientname': LayeredConfig.get(config, 'clientname',
                                            socket.gethostname()),
            'serverhost': LayeredConfig.get(config, 'serverhost', '127.0.0.1'),
            'serverport': LayeredConfig.get(config, 'serverport', 5555),
            'authkey':    LayeredConfig.get(config, 'authkey', 'secret'),
            'processes':  _process_count(LayeredConfig.get(config, 'processes'))
            }


def _setup_buildqueue_args(config):
    import socket
    return {'serverport': LayeredConfig.get(config, 'serverport', 5555),
            'authkey':    LayeredConfig.get(config, 'authkey', 'secret'),
            }


def _filepath_to_urlpath(path, keep_segments=2):
    """
    :param path: the full or relative filepath to transform into a urlpath
    :param keep_segments: the number of directory segments to keep
                          (the ending filename is always kept)
    """
    # data/repo/rsrc/js/main.js, 3 -> repo/rsrc/js/main.js
    # /var/folders/tmp4q6b1g/rsrc/resources.xml, 1 -> rsrc/resources.xml
    # C:\docume~1\owner\locals~1\temp\tmpgbyuk7\rsrc\css\test.css, 2 - rsrc/css/test.css
    path = path.replace(os.sep, "/")
    urlpath = "/".join(path.split("/")[-(keep_segments + 1):])
    # print("_filepath_to_urlpath (%s): %s -> %s" % (keep_segments, path, urlpath))
    return urlpath


def _preflight_check(log, verbose=False):
    """Perform a check of needed modules and binaries."""
    pythonver = (2, 6, 0)

    # Module, min ver, required
    modules = (
        ('bs4', '4.3.0', True),
        # ('lxml', '3.2.0', True), # has no top level __version__ property
        ('rdflib', '4.0', True),
        ('html5lib', '0.99', True),
        ('requests', '1.2.0', True),
        # ('six', '1.4.0', True),
        ('future', '0.15.0', True),
        ('jsmin', '2.0.2', True),
        ('cssmin', '0.2.0', True),
        ('whoosh', '2.4.1', True),
        ('pyparsing', '1.5.7', True))

    binaries = (('pdftotext', '-v'),  # FIXME: we also now require pdfimages, at least version 0.25 (which supports the -png flag)
                ('pdftohtml', '-v'),
                ('antiword', '-h'),
                ('convert', '-version'),
                ('curl', '-V'))

    # 1: Check python ver
    success = True
    if sys.version_info < pythonver:
        log.error("ERROR: ferenda requires Python %s or higher, you have %s" %
                  (".".join([str(x) for x in pythonver]),
                   sys.version.split()[0]))
        success = False
    else:
        if verbose:
            log.info("Python version %s OK" % sys.version.split()[0])

    # 2: Check modules -- TODO: Do we really need to do this?
    for (mod, ver, required) in modules:
        try:
            m = importlib.import_module(mod)
            version = getattr(m, '__version__', None)
            if isinstance(version, bytes):
                version = version.decode()
            if isinstance(version, tuple):
                version = ".".join([str(x) for x in version])
            # print("version of %s is %s" % (mod, version))
            if not hasattr(m, '__version__'):
                log.warning("Module %s has no version information,"
                            "it might be older than required" % mod)
            elif util.numcmp(version, ver) < 0:
                if required:
                    log.error("Module %s has version %s, need %s" %
                              (mod, version, ver))
                    success = False
                else:
                    log.warning(
                        "Module %s has version %s, would like to have %s" %
                        (mod, version, ver))
            else:
                if verbose:
                    log.info("Module %s OK" % mod)
        except ImportError:
            if required:
                log.error("Missing module %s" % mod)
                success = False
            else:
                log.warning("Missing (non-essential) module %s" % mod)

    # a thing needed by testManager.Setup.test_preflight
    if (MagicMock is not None and
            isinstance(__import__, MagicMock) and
            __import__.side_effect is not None):
        __import__.side_effect = None

    # 3: Check binaries
    for (cmd, arg) in binaries:
        try:
            ret = subprocess.call([cmd, arg],
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)
            if ret == 127:
                log.error("Binary %s failed to execute" % cmd)
                success = False
            else:
                if verbose:
                    log.info("Binary %s OK" % cmd)
        except OSError as e:
            log.error("Binary %s failed: %s" % (cmd, e))
            success = False
    if success:
        log.info("Prerequisites ok")
    return success


def _select_triplestore(sitename, log, verbose=False):
    # Try triplestores in order: Fuseki, Sesame, Sleepycat, SQLite,
    # and return configuration for the first triplestore that works.

    # 1. Fuseki
    triplestore = os.environ.get('FERENDA_TRIPLESTORE_LOCATION',
                                 'http://localhost:3030')
    if triplestore:
        try:
            if not os.environ.get('FERENDA_SET_TRIPLESTORE_LOCATION'):
                resp = requests.get(triplestore + "/ds/data?default")
                resp.raise_for_status()
                if verbose:
                    log.info("Fuseki server responding at %s" % triplestore)
            # TODO: Find out how to create a new datastore in Fuseki
            # programatically so we can use
            # http://localhost:3030/$SITENAME instead
            return('FUSEKI', triplestore, 'ds')
        except (requests.exceptions.HTTPError,
                requests.exceptions.ConnectionError) as e:
            if verbose:
                log.info("... Fuseki not available at %s: %s" %
                         (triplestore, e))
            pass

    # 2. Sesame
    triplestore = os.environ.get('FERENDA_TRIPLESTORE_LOCATION',
                                 'http://localhost:8080/openrdf-sesame')
    if triplestore:
        try:
            resp = requests.get(triplestore + '/protocol')
            resp.raise_for_status()
            workbench = triplestore.replace('openrdf-sesame',
                                            'openrdf-workbench')
            if verbose:
                log.info("Sesame server responding at %s (%s)" %
                         (triplestore, resp.text))
            # TODO: It is possible, if you put the exactly right triples
            # in the SYSTEM repository, to create a new repo
            # programmatically.
            log.info("""You still need to create a repository at %(workbench)s ->
    New repository. The following settings are recommended:

        Type: Native Java store
        ID: %(sitename)s
        Title: Ferenda repository for %(sitename)s
        Triple indexes: spoc,posc,cspo,opsc,psoc
            """ % locals())
            return('SESAME', triplestore, sitename)
        except (requests.exceptions.HTTPError,
                requests.exceptions.ConnectionError) as e:
            if verbose:
                log.info("... Sesame not available at %s: %s" %
                         (triplestore, e))
            pass

    # 3. RDFLib + SQLite
    try:
        tmp = tempfile.mkdtemp()

        t = TripleStore.connect("SQLITE", tmp + os.sep + "test.sqlite", "ferenda")
        t.close()
        if verbose:
            log.info("SQLite-backed RDFLib triplestore seems to work")
        return ('SQLITE', 'data/ferenda.sqlite', 'ferenda')
    except ImportError as e:
        if verbose:
            log.info("...SQLite not available: %s" % e)
    finally:
        shutil.rmtree(tmp)

    # 4. RDFLib + Sleepycat
    try:
        tmp = tempfile.mkdtemp()
        t = TripleStore.connect("SLEEPYCAT", tmp + os.sep + "test.db", "ferenda")
        # No boom?
        if verbose:
            log.info("Sleepycat-backed RDFLib triplestore seems to work")
        return ('SLEEPYCAT', 'data/ferenda.db', 'ferenda')
    except ImportError as e:
        if verbose:
            log.info("...Sleepycat not available: %s" % e)
    finally:
        shutil.rmtree(tmp)

    log.info(
        "No usable triplestores, the actions 'relate', 'generate' and 'toc' won't work")
    return (None, None, None)


def _select_fulltextindex(log, sitename, verbose=False):
    # 1. Elasticsearch
    #
    # Note that we scan for the root url, but then return root url + sitename
    fulltextindex = os.environ.get('FERENDA_FULLTEXTINDEX_LOCATION',
                                   'http://localhost:9200/')
    if fulltextindex:
        try:
            if not os.environ.get('FERENDA_SET_FULLTEXTINDEX_LOCATION'):
                resp = requests.get(fulltextindex)
                resp.raise_for_status()
                if verbose:
                    log.info("Elasticsearch server responding at %s" % fulltextindex)
            return('ELASTICSEARCH', fulltextindex + sitename + "/")
        except (requests.exceptions.HTTPError,
                requests.exceptions.ConnectionError) as e:
            if verbose:
                log.info("... Elasticsearch not available at %s: %s" %
                         (fulltextindex, e))
            pass
    # 2. Whoosh (just assume that it works)
    return ("WHOOSH", "data/whooshindex")

if __name__ == '__main__':
    pass
