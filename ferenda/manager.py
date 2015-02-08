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
from __future__ import unicode_literals, print_function
# system
from ast import literal_eval
from datetime import datetime
from ferenda.compat import OrderedDict, MagicMock
from functools import partial, wraps
from io import StringIO, BytesIO
from multiprocessing.managers import SyncManager
from time import sleep
from wsgiref.simple_server import make_server
import argparse
import inspect
import logging
from logging import getLogger as getlog
import multiprocessing
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import traceback

from six import text_type as str
from six.moves import configparser
from six.moves.urllib_parse import urlsplit
from six.moves.queue import Queue, Empty
import six
input = six.moves.input

# 3rd party
import requests
import requests.exceptions
from layeredconfig import (LayeredConfig, Defaults, INIFile, Commandline,
                           Environment)

# my modules
from ferenda import DocumentRepository  # needed for a doctest
from ferenda import Transformer
from ferenda import TripleStore
from ferenda import WSGIApp
from ferenda import Resources
from ferenda import errors
from ferenda import util

# NOTE: This is part of the published API and must be callable in
# scenarios without configfile or logger.
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
              stylesheet="res/xsl/frontpage.xsl",
              sitename="MySite",
              staticsite=False):
    """Create a suitable frontpage.

    :param repos: The repositories to list on the frontpage, as instantiated and configured docrepo objects
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
        with open(xhtml_path, "w") as fp:
            fp.write(xhtml)
        # FIXME: We don't need to actually store the xhtml file on
        # disk -- we could just keep it in memory as an lxml tree and
        # call .transform(tree) just like
        # DocuementRepository.toc_create_page does
        docroot = os.path.dirname(path)
        conffile = os.path.abspath(
            os.sep.join([docroot, 'rsrc', 'resources.xml']))
        transformer = Transformer('XSLT', stylesheet, ["res/xsl"],
                                  config=conffile,
                                  documentroot=docroot)
        if staticsite:
            uritransform = repos[0].get_url_transform_func(repos, os.path.dirname(path))
        else:
            uritransform = None
        transformer.transform_file(xhtml_path, path, uritransform=uritransform)
    return True


def runserver(repos,
              port=8000, # now that we require url, we don't need this
              documentroot="data",  # relative to cwd
              apiendpoint="/api/",
              searchendpoint="/search/",
              url="http://localhost:8000/",
              indextype="WHOOSH",
              indexlocation="data/whooshindex",
              legacyapi=False):
    """Starts up a internal webserver and runs the WSGI app (see
    :py:func:`make_wsgi_app`) using all the specified document
    repositories. Runs forever (or until interrupted by keyboard).

    :param repos: Object instances for the repositories that should be served
                  over HTTP
    :type repos: list
    :param port: The port to use
    :type port: int
    :param documentroot: The root document, used to locate files not directly
                         handled by any repository
    :type documentroot: str
    :param apiendpoint: The part of the URI space handled by the API
                        functionality
    :type apiendpoint: str
    :param searchendpoint: The part of the URI space handled by the search
                           functionality
    :type searchendpoint: str

    """
    getlog().info("Serving wsgi app at http://localhost:%s/" % port)
    kwargs = {'port': port,
              'documentroot': documentroot,
              'apiendpoint': apiendpoint,
              'searchendpoint': searchendpoint,
              'indextype': indextype,
              'indexlocation': indexlocation,
              'legacyapi': legacyapi,
              'repos': repos}
    httpd = make_server('', port, make_wsgi_app(None, **kwargs))
    httpd.serve_forever()


def make_wsgi_app(inifile=None, **kwargs):
    """Creates a callable object that can act as a WSGI application by
    mod_wsgi, gunicorn, the built-in webserver, or any other
    WSGI-compliant webserver.

    :param inifile: The full path to a ``ferenda.ini`` configuration file
    :type inifile: str
    :param \*\*kwargs: Configuration values for the wsgi app (must
                         include ``documentroot``, ``apiendpoint`` and
                         ``searchendpoint``). Only used if ``inifile``
                         is not provided.
    :returns: A WSGI application
    :rtype: callable

    """
    if inifile:
        assert os.path.exists(
            inifile), "INI file %s doesn't exist (relative to %s)" % (inifile, os.getcwd())
        config = _load_config(inifile)
        args = _setup_runserver_args(config, inifile)
    else:
        args = kwargs  # sanity check: is documentroot, searchendpoint and
                       # apiendpoint defined?

    # if we have an inifile, we should provide that instead of the
    # **args we've got from _setup_runserver_args()
    repos = args['repos']
    del args['repos']
    return WSGIApp(repos, **args)


loglevels = {'DEBUG': logging.DEBUG,
             'INFO': logging.INFO,
             'WARNING': logging.WARNING,
             'ERROR': logging.ERROR,
             'CRITICAL': logging.CRITICAL}


def setup_logger(level='INFO', filename=None,
                 logformat="%(asctime)s %(name)s %(levelname)s %(message)s",
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
    l = logging.getLogger()  # get the root logger
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
                    'rdflib.plugins.sleepycat',
                    'ferenda.thirdparty.patch']:
        log = logging.getLogger(logname)
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
    FileHandlers, which is needed on win32."""
    
    l = logging.getLogger()  # get the root logger
    for existing_handler in list(l.handlers):
        if isinstance(existing_handler, logging.FileHandler):
            existing_handler.close()
        l.removeHandler(existing_handler)

    

def run(argv, subcall=False):
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
    config = _load_config(_find_config_file(), argv)
    # if logfile is set to True (the default), autogenerate logfile
    # name from current datetime. Otherwise assume logfile is set to
    # the desired file name of the log
    log = setup_logger(level=config.loglevel, filename=None)
    if config.logfile:
        if isinstance(config.logfile, bool):
            logfile = "%s/logs/%s.log" % (
                config.datadir, datetime.now().strftime("%Y%m%d-%H%M%S"))
        else:
            logfile = config.logfile
        util.ensure_dir(logfile)
        setup_logger(level=config.loglevel, filename=logfile)

    try:
        # reads only ferenda.ini using configparser rather than layeredconfig
        enabled = _enabled_classes()
        # returns {'ferenda.sources.docrepo.DocRepo':'base',...}
        enabled_aliases = dict(reversed(item) for item in enabled.items())
        if len(argv) < 1:
            _print_usage()  # also lists enabled modules
        else:
            classname = config.alias

            if config.action == 'enable':
                try:
                    return enable(classname)
                except (ImportError, ValueError) as e:
                    log.error(str(e))
                    return None

            elif config.action == 'runserver':
                args = _setup_runserver_args(config, _find_config_file())
                # Note: the actual runserver method never returns
                return runserver(**args)

            elif config.action == 'buildclient':
                args = _setup_buildclient_args(config)
                return runbuildclient(**args)

            elif config.action == 'buildqueue':
                args = _setup_buildqueue_args(config)
                return runbuildqueue(**args)

            elif config.action == 'makeresources':
                repoclasses = _classes_from_classname(enabled, classname)
                args = _setup_makeresources_args(config)
                repos = []
                for cls in repoclasses:
                    inst = _instantiate_class(cls, config, argv)
                    repos.append(inst)
                return makeresources(repos, **args)

            elif config.action == 'frontpage':
                repoclasses = _classes_from_classname(enabled, classname)
                args = _setup_frontpage_args(config, argv)
                return frontpage(**args)

            elif config.action == 'all':
                classnames = _setup_classnames(enabled, classname)
                results = OrderedDict()
                for action in ("download",
                               "parse", "relate", "makeresources",
                               "generate", "toc", "news", "frontpage"):
                    if action in ("makeresources", "frontpage"):
                        # FIXME: This way of re-creating the argv so
                        # that LayeredConfig can process it again in
                        # the subcall to run is less than elegant. A
                        # better way would perhaps be to pass the
                        # existing config object to run as an optional
                        # argument)
                        argscopy = argv[2:] # skip alias and action
                        argscopy.insert(0, action)
                        argscopy.insert(0, "all")
                        results[action] = run(argscopy, subcall=True)
                    else:
                        results[action] = OrderedDict()
                        for classname in classnames:
                            alias = enabled_aliases[classname]
                            argscopy = argv[2:]
                            if (action in ("parse", "relate", "generate") and
                                "--all" not in argscopy):
                                argscopy.append("--all")
                            argscopy.insert(0, action)
                            argscopy.insert(0, classname)
                            results[action][alias] = run(argscopy, subcall=True)
                return results
            else:
                if classname == "all":
                    ret = []
                    for alias, classname in enabled.items():
                        #argv_copy = list(argv)
                        #argv_copy[0] = alias
                        config.alias = alias
                        try:
                            ret.append(_run_class(enabled, argv, config))
                        except Exception as e:
                            log.error("%s %s failed: %s" %
                                      (config.action, config.alias, e))
                    config.alias = "all"
                    return ret
                else:
                    return _run_class(enabled, argv, config)
    finally:
        if not subcall:
            _shutdown_buildserver()
        shutdown_logger()

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
    configfilename = _find_config_file(create=True)
    cfg.read([configfilename])
    alias = cls.alias
    cfg.add_section(alias)
    cfg.set(alias, "class", classname)
    with open(configfilename, "w") as fp:
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
            log.info("There were some errors when checking your environment. Proceed anyway? (y/N)")
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
    buildscript = projdir + os.sep + "ferenda-build.py"
    util.resource_extract('res/scripts/ferenda-build.py', buildscript)
    mode = os.stat(buildscript)[stat.ST_MODE]
    os.chmod(buildscript, mode | stat.S_IXUSR)

    # step 2: create config file
    configfile = projdir + os.sep + "ferenda.ini"
    util.resource_extract('res/scripts/ferenda.template.ini', configfile,
                          locals())

    log.info("Project created in %s" % projdir)

    # step 3: create WSGI app
    wsgifile = projdir + os.sep + "wsgi.py"
    util.resource_extract('res/scripts/wsgi.py', wsgifile)
    shutdown_logger()
    return True


def _load_config(filename=None, argv=None, defaults=None):
    """Loads general configuration information from ``filename`` (which
       should be a full path to a ferenda.ini file) and/or command
       line arguments into a :py:class:`~layeredconfig.LayeredConfig`
       instance. It contains a built-in dict of default configuration
       values which can be overridden by the config file or command
       line arguments.

    """

    # FIXME: Expand on this list of defaults? Note that it only
    # pertains to global configuration, not docrepo configuration
    # (those have the get_default_options() method).
    if not defaults:
        defaults = {'loglevel': 'DEBUG',
                    'logfile': True,
                    'processes': 1,
                    'datadir': 'data',
                    'force': False,
                    'downloadmax': int,  # used strictly for typing
                    'combineresources': False,
                    'staticsite': False,
                    'sitename': 'MySite',
                    'sitedescription': 'Just another Ferenda site',
                    'cssfiles': ['http://fonts.googleapis.com/css?family=Raleway:200,100',
                                 'res/css/normalize-1.1.3.css',
                                 'res/css/main.css',
                                 'res/css/ferenda.css'],
                    'jsfiles': ['res/js/jquery-1.10.2.js',
                                'res/js/modernizr-2.6.3.js',
                                'res/js/respond-1.3.0.js',
                                'res/js/ferenda.js'],
                    'imgfiles': ['res/img/navmenu-small-black.png',
                                 'res/img/navmenu.png',
                                 'res/img/search.png'],
                    'legacyapi': False,
                    'fulltextindex': True,
                    'serverport': 5555,
                    'authkey': b'secret'}
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
            'legacyapi':   config.legacyapi
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
        return [v for v in enabled.values() if v != 'ferenda.Devel']
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
    with util.logtime(
        log.info, "%(alias)s %(command)s finished in %(elapsed).3f sec",
        {'alias': config.alias,
         'command': config.action}):
        _enabled_classes = dict(reversed(item) for item in enabled.items())

        if config.alias not in enabled and config.alias not in _enabled_classes:
            log.error("Class-or-alias %s not enabled" % config.alias)
            return
        if config.alias in argv:
            argv.remove(config.alias)
        # ie a fully qualified classname was used
        if config.alias in _enabled_classes:
            classname = config.alias
        else:
            classname = enabled[config.alias]
        cls = _load_class(classname)
        inst = _instantiate_class(cls, config, argv=argv)
        try:
            clbl = getattr(inst, config.action)
            assert(callable(clbl))
        except:  # action was None or not a callable thing
            if config.action:
                log.error("%s is not a valid command for %s" %
                          (config.action, classname))
            else:
                log.error("No command given for %s" % classname)
            _print_class_usage(cls)
            return

        kwargs = {}
        if config.action in ('relate', 'generate', 'toc', 'news'):
            # we need to provide the otherrepos parameter
            otherrepos = []
            for othercls in _classes_from_classname(enabled, 'all'):
                if othercls != inst.__class__:
                    otherrepos.append(_instantiate_class(othercls, argv=argv))
            kwargs['otherrepos'] = otherrepos

        if 'all' in inst.config and inst.config.all == True:
            iterable = inst.store.list_basefiles_for(config.action)
            res = []
            # semi-magic handling
            ret = cls.setup(config.action, inst.config)
            if ret == False:
                log.info("%s %s: Nothing to do!" % (config.alias, config.action))
            else:
                # Now we have a list of jobs in the iterable. They can
                # be processed in four different ways:
                # 
                if LayeredConfig.get(config, 'buildserver'):
                    # - start an internal jobqueue to which buildclients
                    #   connect, and send jobs to it (and read results
                    #   from a similar resultqueue)
                    res = _queuejobs(iterable, inst, classname, config.action)
                elif LayeredConfig.get(config, 'buildqueue'):
                    # - send jobs to an external jobqueue process to which
                    #   buildclients connect (and read results from a
                    #   similar resultqueue)
                    res = _queuejobs_to_queue(iterable, inst, classname, config.action)
                elif inst.config.processes > 1:
                    # - start a number of processess which read from a
                    #   shared jobqueue, and send jobs to that queue (and
                    #   read results from a shared resultqueue)
                    res = _parallelizejobs(iterable, inst, classname, config.action, config, argv)
                else:
                    # - run the jobs, one by one, in the current process
                    for basefile in inst.store.list_basefiles_for(config.action):
                        res.append(_run_class_with_basefile(clbl, basefile, kwargs, config.action))
                cls.teardown(config.action, inst.config)
        else:
            # The only thing that kwargs may contain is a
            # 'otherrrepos' parameter
            res = clbl(*config.arguments, **kwargs)
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
    while not done:  # _run_jobqueue_multiprocessing > _build_worker might throw an exception,
                     # which is how we exit
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
            # print("Client: %s: sleeping and retrying..." % e)
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

        p = multiprocessing.Process(
                target=_build_worker,
                args=(jobqueue, resultqueue, clientname))
        procs.append(p)
        # sleep(1)
        p.start()
        log.debug("Client: [pid %s] Started process %s" % (os.getpid(), p.pid))
    return procs


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
    inst = None
    logstream  = StringIO()
    log = getlog()
    log.debug("Client: [pid %s] _build_worker ready to process job queue" % os.getpid())
    while True:
        job = jobqueue.get() # get() blocks -- wait until a job or the
                          # DONE/SHUTDOWN signal comes
        if job == "DONE": # or a more sensible value
            # getlog().debug("Client: [pid %s] Got DONE signal" % os.getpid())
            return  # back to runbuildclient
        if job == "SHUTDOWN":
            # getlog().debug("Client: Got SHUTDOWN signal")
            # kill the entire thing
            raise Exception("OK we're done now")
        if inst == None:
            inst = _instantiate_and_configure(job['classname'],
                                              job['config'],
                                              logstream,
                                              clientname)
            # need to get hold of log as well
        # log.debug("Client: [pid %s] Starting job %s %s %s" % (os.getpid(), job['classname'], job['command'], job['basefile']))
        # Do the work
        clbl = getattr(inst, job['command'])
        # kwargs = job['kwargs']   # if we ever support that
        kwargs = {}
        res = _run_class_with_basefile(clbl, job['basefile'],
                                       kwargs, job['command'],
                                       wrapctrlc=True)
        log.debug("Client: [pid %s] %s finished: %s" % (os.getpid(), job['basefile'], res))
        logtext = logstream.getvalue()
        logstream.truncate(0)
        logstream.seek(0)
        outdict = {'basefile': job['basefile'],
                   'result':  res,
                   'log': logtext,
                   'client': clientname}
        resultqueue.put(outdict)
        # log.debug("Client: [pid %s] Put '%s' on the queue" % (os.getpid(), outdict['result']))
        

def _instantiate_and_configure(classname, config, logstream, clientname):
    log = getlog()
    log.debug("Client: [pid %s] instantiating and configuring %s" % (os.getpid(), classname))
    inst = _instantiate_class(_load_class(classname))
    for k,v in config.items():
        # log.debug("Client: [pid %s] setting config value %s to %r" % (os.getpid(), k, v))
        LayeredConfig.set(inst.config, k, v)

    # When running in distributed mode (but not in multiprocessing
    # mode), setup the root logger to log to a StringIO buffer.
    if clientname:
        # log.debug("Client: [pid %s] Setting up log" % os.getpid())
        log = setup_logger(inst.config.loglevel)
        for handler in log.handlers:
            log.removeHandler(handler)
        handler = logging.StreamHandler(logstream)
        fmt = clientname+" %(asctime)s %(name)s %(levelname)s %(message)s"
        formatter = logging.Formatter(fmt, datefmt="%H:%M:%S")
        handler.setFormatter(formatter)
        handler.setLevel(loglevels[inst.config.loglevel])
        log.addHandler(handler)
        log.setLevel(loglevels[inst.config.loglevel])
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
        job = {'basefile': basefile,
               'classname': classname,
               'command': command,
               'config': client_config}
        # log.debug("Server: putting %r into jobqueue" %  job['basefile'])
        jobqueue.put(job)
        basefiles.append(basefile)
    log.debug("Server: Put %s jobs into job queue" % len(basefiles))
    return basefiles


def _queue_jobs(manager, iterable, inst, classname, command):
    jobqueue = manager.jobqueue()
    resultqueue = manager.resultqueue()
    log = getlog()
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
    for idx, basefile in enumerate(iterable):
        job = {'basefile': basefile,
               'classname': classname,
               'command': command,
               'config': client_config}
        # print("putting %r into jobqueue" %  job)
        jobqueue.put(job)
    number_of_jobs = idx+1
    log.debug("Server: Put %s jobs into job queue" % number_of_jobs)
    # FIXME: only one of the clients will read this DONE package, and
    # we have no real way of knowing how many clients there will be
    # (they can come and go at will). Didn't think this one through...
    jobqueue.put("DONE")
    numres = 0
    res = []
    while numres < number_of_jobs:
        r = resultqueue.get()
        if isinstance(r['result'], tuple) and r['result'][0] == _WrappedKeyboardInterrupt:
            raise KeyboardInterrupt()
        elif isinstance(r['result'], tuple) and isinstance(r['result'], Exception):
            r['except_type'] = r['result'][0]
            r['except_value'] = r['result'][1]
            log.error("Server: %(client)s failed %(basefile)s: %(except_type)s: %(except_value)s" % r)
            print("".join(traceback.format_list(r['result'][2])))
        else:
            for line in [x.strip() for x in r['log'].split("\n") if x.strip()]:
                print("   %s" % line)
            log.debug("Server: client %(client)s processed %(basefile)s: Result (%(result)s): OK" % r)
        if 'result' in r:
            res.append(r['result'])
        numres += 1
    log.debug("Server: %s tasks processed" % numres)
    return res
    # sleep(1)

    # don't shut this down --- the toplevel manager.run call must do
    # that
    # manager.shutdown()

    
buildmanager = None
if six.PY2:
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
        getlog().debug("Server: Process %s created new buildmanager at %s" % (os.getpid(), id(buildmanager)))
        if start: # runbuildqueue wants to control this itself
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

def _parallelizejobs(iterable, inst, classname, command, config, argv):
    jobqueue = multiprocessing.Queue()
    resultqueue = multiprocessing.Queue()
    procs = _start_multiprocessing(jobqueue, resultqueue, inst.config.processes, None)
    try:
        basefiles = __queue_jobs_nomanager(jobqueue, iterable, inst, classname, command)
        res = _process_resultqueue(resultqueue, basefiles)
        return res
    finally:
        _finish_multiprocessing(procs, join=False)

def _process_resultqueue(resultqueue, basefiles):
    res = {}
    queuelength = len(basefiles)
    for i in range(queuelength):
        r = resultqueue.get()
        if isinstance(r['result'], tuple) and r['result'][0] == _WrappedKeyboardInterrupt:
            raise KeyboardInterrupt()
        res[r['basefile']] = r['result']
    # return the results in the same order as they were queued
    return [res[x] for x in basefiles]

    
def _run_class_with_basefile(clbl, basefile, kwargs, command, wrapctrlc=False):
    try:
        return clbl(basefile, **kwargs)
    except errors.DocumentRemovedError as e:
        if hasattr(e, 'dummyfile'):
            if not os.path.exists(e.dummyfile):
                util.writefile(e.dummyfile, "")
            return None  # is what DocumentRepository.parse returns
                         # when everyting's ok
        else:
            errmsg = str(e)
            getlog().error("%s of %s failed: %s" %
                                 (command, basefile, errmsg))
            exc_type, exc_value, tb = sys.exc_info()
            return exc_type, exc_value, traceback.extract_tb(tb)
    except Exception as e:
        errmsg = str(e)
        getlog().error("%s of %s failed: %s" %
                             (command, basefile, errmsg))
        exc_type, exc_value, tb = sys.exc_info()
        return exc_type, exc_value, traceback.extract_tb(tb)
    except KeyboardInterrupt as e:   # KeyboardInterrupt is not an Exception
        if wrapctrlc:
            except_type, except_value, tb = sys.exc_info()
            return _WrappedKeyboardInterrupt, _WrappedKeyboardInterrupt(), traceback.extract_tb(tb)
        else:
            raise


        
def _instantiate_class(cls, config=None, argv=[]):
    """Given a class object, instantiate that class and make sure the
       instance is properly configured given it's own defaults, a
       config file, and command line parameters."""
    clsdefaults = cls().get_default_options()
    if not config:
        defaults = dict(clsdefaults)
        defaults[cls.alias] = {}
        config = LayeredConfig(Defaults(defaults),
                               INIFile(_find_config_file()),
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
                    :py:func:`ferenda.Manager._find_config_file`
    :type inifile: str
    :returns: A mapping between alias and classname for all registered classes.
    :rtype: dict
    
    """

    cfg = configparser.ConfigParser()
    if not inifile:
        inifile = _find_config_file()

    cfg.read([inifile])
    enabled = OrderedDict()
    for section in cfg.sections():
        if cfg.has_option(section, "class"):
            enabled[section] = cfg.get(section, "class")
    return enabled


def _print_usage():
    """Prints out general usage information for the ``ferenda-build.py`` tool."""
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
    >>> _list_enabled_classes() == {'base': 'Base class for downloading, parsing and generating HTML versions of a repository of documents.'}
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
    ...     'generate':'Generate a browser-ready HTML file from structured XML and RDF.'}
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


def _find_config_file(path=None, create=False):
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


def _setup_runserver_args(config, inifilename):
    """Given a config object, returns a dict with some of those
       configuration options, but suitable as arguments for
       :py:func:`ferenda.Manager.runserver`.
    
    :param config: An initialized config object with data from a ferenda.ini
                   file
    :type config: layeredconfig.LayeredConfig
    :returns: A subset of the same configuration options
    :rtype: dict

    """
    port = urlsplit(config.url).port or 80
    relativeroot = os.path.join(os.path.dirname(inifilename), config.datadir)

    # create an instance of every enabled repo
    enabled = _enabled_classes(inifilename)
    repoclasses = _classes_from_classname(enabled, 'all')
    repos = []
    for cls in repoclasses:
        instconfig = getattr(config, cls.alias)
        config_as_dict = dict(
            [(k, getattr(instconfig, k)) for k in instconfig])
        inst = cls(**config_as_dict)
        repos.append(inst)

    # for repo in repos:
    #    print("Repo %r %s: config.datadir is %s" % (repo, id(repo), repo.config.datadir))
    return {'port':           port,
            'documentroot':   relativeroot,
            'apiendpoint':    config.apiendpoint,
            'searchendpoint': config.searchendpoint,
            'url':            config.url,
            'indextype':      config.indextype,
            'indexlocation':  config.indexlocation,
            'legacyapi':      config.legacyapi,
            'repos':          repos}


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
        # inst = _instantiate_class(cls, _find_config_file(), argv)
        inst = _instantiate_class(cls, config, argv)
        repos.append(inst)
    return {'sitename': config.sitename,
            'path': config.datadir + "/index.html",
            'staticsite': config.staticsite,
            'repos': repos}

def _setup_buildclient_args(config):
    import socket
    return {'clientname': LayeredConfig.get(config, 'clientname',
                                            socket.gethostname()),
            'serverhost': LayeredConfig.get(config, 'serverhost', '127.0.0.1'),
            'serverport': LayeredConfig.get(config, 'serverport', 5555),
            'authkey':    LayeredConfig.get(config, 'authkey', 'secret'),
            'processes':  LayeredConfig.get(config, 'processes',
                                            multiprocessing.cpu_count())
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
        ('six', '1.4.0', True),
        ('jsmin', '2.0.2', True),
        ('cssmin', '0.2.0', True),
        ('whoosh', '2.4.1', True),
        ('pyparsing', '1.5.7', True))

    binaries = (('pdftotext', '-v'),
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
            m = __import__(mod)
            version = getattr(m, '__version__', None)
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
    if  (MagicMock is not None and
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
        
        t = TripleStore.connect("SQLITE", tmp+os.sep+"test.sqlite", "ferenda")
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
        t = TripleStore.connect("SLEEPYCAT", tmp+os.sep+"test.db", "ferenda")
        # No boom?
        if verbose:
            log.info("Sleepycat-backed RDFLib triplestore seems to work")
        return ('SLEEPYCAT', 'data/ferenda.db', 'ferenda')
    except ImportError as e:
        if verbose:
            log.info("...Sleepycat not available: %s" % e)
    finally:
        shutil.rmtree(tmp)

    log.info("No usable triplestores, the actions 'relate', 'generate' and 'toc' won't work")
    return (None, None, None)


def _select_fulltextindex(log, sitename, verbose=False):
    # 1. Elasticsearch
    #
    # Note that we scan for the root url, but then return root url + sitename
    fulltextindex = os.environ.get('FERENDA_FULLTEXTINDEX_LOCATION',
                                   'http://localhost:9200/')
    if fulltextindex:
        try:
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
