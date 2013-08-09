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
from __future__ import unicode_literals
# system
import os
import time
import stat
import subprocess
import sys
import inspect
import itertools
import logging
import json
import mimetypes
from ast import literal_eval
from datetime import datetime
import xml.etree.cElementTree as ET
import cgi

try:
    from collections import OrderedDict
except ImportError:
    from ordereddict import OrderedDict

import six  # technically third-party, but needed for cross-version
            # system imports
if six.PY3:
    from urllib.parse import urlsplit, parse_qsl, urlencode
else:
    from urlparse import urlsplit, parse_qsl, urlencode

from wsgiref.simple_server import make_server
from wsgiref.util import FileWrapper
# from pprint import pprint
from six.moves import configparser
input = six.moves.input

# 3rd party
import pkg_resources
import requests
import requests.exceptions
from rdflib import URIRef, Namespace, Literal
from bs4 import BeautifulSoup

# my modules
from ferenda import DocumentRepository
from ferenda import DocumentStore
from ferenda import FulltextIndex
from ferenda import LayeredConfig
from ferenda import TripleStore
from ferenda import elements
from ferenda.elements  import html
from ferenda import errors
from ferenda import util

# NOTE: This is part of the published API and must be callable in
# scenarios without configfile or logger.
def makeresources(repos,
                  resourcedir="data/rsrc",
                  combine=False,
                  cssfiles=[],
                  jsfiles=[],
                  staticsite=False,
                  sitename="MySite",
                  sitedescription="Just another Ferenda site"):
    """Creates the web assets/resources needed for the web app
    (concatenated and minified js/css files, resources.xml used by
    most XSLT stylesheets, etc).

    :param repos: The repositories to create resources for
    :type  repos: list (or other iterable) of docrepo objects
    :param combine: whether to combine and compact/minify CSS and JS files
    :type  combine: bool
    :param resourcedir: where to put generated/copied resources
    :type  resourcedir: string (directory name)
    :returns: All created/copied css, js and resources.xml files
    :rtype: dict of lists
    """
    # NOTE: even though the returned dict of lists of paths should use the appropriate path
    # separator for the current platform (/ on unix, \ on windows), the resources.xml always
    # uses the / separator regardless of platform.
    log = setup_logger()
    res = {}
    processed_files = []
    # Create minfied combined.css file
    cssbuffer = six.BytesIO()
    cssurls = []
    cssdir = resourcedir + os.sep + "css"

    # 1. Process all css files specified in the main config
    for cssfile in cssfiles:
        if cssfile in processed_files:
            continue
        cssurls.append(_process_file(
                cssfile, cssbuffer, cssdir, "ferenda.ini", combine))
        processed_files.append(cssfile)

    # 2. Visit each enabled class and see if it specifies additional
    # css files to read
    for inst in repos:
        if not hasattr(inst,'config'):
            continue
        for cssfile in inst.config.cssfiles:
            if cssfile in processed_files:
                continue
            # FIXME: CSS file path should be interpreted
            # relative to the module source code file instead
            # of cwd
            cssurls.append(_process_file(
                    cssfile, cssbuffer, cssdir, inst.alias, combine))
            processed_files.append(cssfile)
    cssurls = list(filter(None,cssurls))
    if combine:
        # 3. Minify the result using cssmin
        css = cssbuffer.getvalue().decode('utf-8')
        log.debug("Read %s files, CSS is now %s bytes" % (len(
                    cssfiles), len(css)))
        from .thirdparty import cssmin
        css = cssmin.cssmin(css) 
        log.debug("After minifying, CSS is now %s bytes" % (len(css)))
        outcssfile = cssdir + os.sep + 'combined.css'
        util.writefile(outcssfile, css)
        res['css'] = [_filepath_to_urlpath(outcssfile,2)]
    else:
        res['css'] = cssurls

    # Create data/rsrc/js/combined.js in a similar way but use slimit to
    # compress the result
    jsbuffer = six.BytesIO()
    jsurls = []
    jsdir = resourcedir + os.sep + "js"
    for jsfile in jsfiles:
        if jsfile in processed_files:
            continue
        jsurls.append(_process_file(
            jsfile, jsbuffer, jsdir, "ferenda.ini", combine))
        processed_files.append(jsfile)

    for inst in repos:
        if not hasattr(inst,'config'):
            continue
        for jsfile in inst.config.jsfiles:
            if jsfile in processed_files:
                continue
            jsurls.append(_process_file(
                jsfile, jsbuffer, jsdir, inst.alias, combine))
            processed_files.append(jsfile)

    jsurls = list(filter(None,jsurls))
    if combine:
        js = jsbuffer.getvalue().decode('utf-8')
        log.debug("Read %s files, JS is now %s bytes" % (len(jsfiles),
                                                         len(js)))
        # slimit provides better perf, but isn't py3 compatible
        # import slimit
        #js = slimit.minify(
        #     jsbuffer.getvalue(), mangle=True, mangle_toplevel=True)
        import jsmin
        js = jsmin.jsmin(js)
        log.debug("After compression, JS is now %s bytes" % (len(js)))
        outjsfile = jsdir + os.sep + 'combined.js'
        util.writefile(outjsfile, js)
        res['js'] = [_filepath_to_urlpath(outjsfile,2)]
    else:
        res['js'] = jsurls

    # Populate data/rsrc/img/ from files found in config['imgdir'] and
    # module.imagedir (putting each module's imagedir in a separate
    # subdir, eg EurlexTreaties.imagedir = res/eut/img results in
    # res/eut/img/foo.png being placed in data/rsrc/img/eut/foo.png
    # Finally, create a resources.xml file containing refs to the css and js
    # files (and also favicon?) that base5.xsl can include.

    # FIXME: Do this in LXML instead (and remove util.indent_node afterwards)
    root = ET.Element("configuration")
    sitename_el = ET.SubElement(root, "sitename")
    sitename_el.text = sitename
    sitedescription_el = ET.SubElement(root, "sitedescription")
    sitedescription_el.text = sitedescription
    tabs = ET.SubElement(
        ET.SubElement(ET.SubElement(root, "tabs"), "nav"), "ul")

    sitetabs = []
    for inst in repos:
        if hasattr(inst, 'tabs'):
            for tab in inst.tabs():
                if not tab in sitetabs:
                    (label, url) = tab
                    alias = inst.alias
                    log.debug("Adding tab %(label)s (%(url)s) from docrepo %(alias)s" % locals())
                    sitetabs.append(tab)

    for tab in sitetabs:
        link = ET.SubElement(ET.SubElement(tabs, "li"), "a")
        link.text = tab[0]
        link.attrib['href'] = tab[1]

    if not staticsite:
        search = ET.SubElement(ET.SubElement(ET.SubElement(root, "search"), "form", action="/search/"), "input", type="search", name="q")

    stylesheets = ET.SubElement(root, "stylesheets")
    log.debug("Adding %s stylesheets to resources.xml"%len(res['css']))
    for f in res['css']:
        stylesheet = ET.SubElement(stylesheets, "link")
        stylesheet.attrib['rel'] = "stylesheet"
        stylesheet.attrib['href'] = f
    log.debug("Adding %s javascripts to resources.xml"%len(res['js']))
    javascripts = ET.SubElement(root, "javascripts")
    for f in res['js']:
        javascript = ET.SubElement(javascripts, "script")
        javascript.attrib['src'] = f
        javascript.text = " "
    util.indent_node(root)
    tree = ET.ElementTree(root)
    outxmlfile = resourcedir+os.sep+"resources.xml"
    util.ensure_dir(outxmlfile)
    tree.write(outxmlfile, encoding="utf-8")
    log.debug("Wrote %s" %outxmlfile)
    # NOTE: If DocumentRepository.generate feels like it, it may
    # create a temporary copy of resources.xml with js/css paths
    # modified to be relative to the generated file (which may be 2-3
    # directories deep) instead of the document root, in order to
    # support static HTML file generation with arbitrarily deep
    # directory structure.
    res['xml'] = [_filepath_to_urlpath(outxmlfile,1)]
    if os.sep == "\\":
        for part in res:
            res[part] = [x.replace('/',os.sep) for x in res[part]]
    return res


def _process_file(filename, buf, destdir, origin="", combine=False):
    """
    Helper function to concatenate or copy CSS/JS (optionally
    processing them with e.g. Scss) or other files to correct place
    under the web root directory.
    
    :param filename: The name (relative to the ferenda package) of the file
    :param buf: A buffer into which the contents of the file is written (if combine == True)
    :param destdir: The directory into which the file will be copied (unless combine == True)
    :param origin: The source of the configuration that specifies this files
    :param combine: Whether to combine all files into a single one
    :returns: The URL path of the resulting file, relative to the web root (or None if combine == True)
    :rtype: str
    """
    mapping = {'.scss': {'transform': _transform_scss,
                         'suffix': '.css'}
               }
    log = setup_logger()
    # FIXME: extend this through a load-path mechanism?
    if os.path.exists(filename):
        log.debug("Process file found %s as a file relative to %s" % (filename, os.getcwd()))
        fp = open(filename,"rb")
    elif pkg_resources.resource_exists('ferenda',filename):
        log.debug("Found %s as a resource" % filename)
        fp = pkg_resources.resource_stream('ferenda',filename)
    elif filename.startswith("http://") or filename.startswith("https://"):
        if combine:
            raise errors.ConfigurationError("makeresources: Can't use combine=True in combination with external js/css URLs (%s)" % filename)
        log.debug("Using external url %s" % filename)
        return filename
    else:
        log.warning("file %(filename)s (specified in %(origin)s) doesn't exist" % locals())
        return None
        
    (base, ext) = os.path.splitext(filename)
    if ext in mapping:
        outfile = base + mapping[ext]['suffix']
        mapping[ext]['transform'](filename, outfile)
        filename = outfile
    if combine:
        log.debug("combining %s into buffer" % filename)
        buf.write(fp.read())
        fp.close()
        return None
    else:
        log.debug("writing %s out to %s" % (filename,destdir))
        outfile = destdir + os.sep + os.path.basename(filename)
        util.ensure_dir(outfile)
        with open(outfile,"wb") as fp2:
            fp2.write(fp.read())
        fp.close()
        return _filepath_to_urlpath(outfile,2)


def _transform_scss(infile, outfile):
    print(("Transforming %s to %s" % (infile, outfile)))
    from scss import Scss
    compiler = Scss()
    util.writefile(outfile, compiler.compile(util.readfile(infile)))


def frontpage(repos,
              path="data/index.html",
              stylesheet="res/xsl/frontpage.xsl",
              sitename="MySite"):
    """Create a suitable frontpage.

    :param repos: The repositories to list on the frontpage
    :type repos: list (or other iterable) of docrepo objects
    :param path: the filename to create."""
    log = setup_logger()
    with util.logtime(log.info,
                      "frontpage: wrote %(path)s (%(elapsed).3f sec)",
                      {'path':path}):
        blocks = ""
        # TODO: if any of the repos has inst.config.primaryfrontpage =
        # True, then all other repos should provide their
        # .frontpage_content() into that repos .frontpage impl (and this
        # method should not have any xhtml template like below).
        for inst in repos:
            content = inst.frontpage_content()
            blocks += "<div id='%s'>%s</div>" % (inst.alias, content)
            log.debug("frontpage: repo %s provided %s chars of content" % (inst.alias, len(content)))
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
        with open(xhtml_path,"w") as fp:
            fp.write(xhtml)

        xsltdir = repos[0].setup_transform_templates(os.path.dirname(stylesheet))
        params = repos[0].get_transform_configuration(xsltdir,xhtml_path)
        repos[0].transform_html(stylesheet, xhtml_path, path, params, otherrepos=repos)
    return True


def runserver(repos,
              port=8000,
              documentroot="data", # relative to cwd
              apiendpoint="/api/",
              searchendpoint="/search/"):
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
    print("Serving wsgi app at http://localhost:%s/" % port)
    kwargs = {'port':port,
              'documentroot': documentroot,
              'apiendpoint': apiendpoint,
              'searchendpoint' :searchendpoint,
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
        assert os.path.exists(inifile), "INI file %s doesn't exist (relative to %s)" % (inifile, os.getcwd())
        config = _load_config(inifile)
        args = _setup_runserver_args(config, inifile)
    else:
        args = kwargs  # sanity check: is documentroot, searchendpoint and
                       # apiendpoint defined?

    def app(environ, start_response):
        path = environ['PATH_INFO']
        if path.startswith(args['searchendpoint']):
            return _wsgi_search(environ, start_response, args)
        elif path.startswith(args['apiendpoint']):
            return _wsgi_api(environ, start_response, args)
        else:
            return _wsgi_static(environ, start_response, args)
    return app


def _wsgi_search(environ, start_response, args):
    """WSGI method, called by the wsgi app for requests that matches
       ``searchendpoint``."""
    # get the location for the index. Different repos could
    # technically have different paths here, but that'd be stupid. It
    # would be bettter if indexlocation was available direct from args
    # (which requires changing _setup_runserver_args())
    idx = FulltextIndex.connect("WHOOSH",
                                args['repos'][0].config.indexlocation)
    # FIXME: QUERY_STRING should probably be sanitized before calling
    # .query() - but in what way?
    querystring = OrderedDict(parse_qsl(environ['QUERY_STRING']))
    query = querystring['q']
    pagenum = int(querystring.get('p','1'))
    res, pager = idx.query(query,pagenum=pagenum)
    if pager['totalresults'] == 1:
        resulthead = "1 match"
    else:
        resulthead = "%s matches" % pager['totalresults']
    resulthead += " for '%s'" % query  # query will be escaped later

    # Creates simple XHTML result page
    repo = args['repos'][0]
    doc = repo.make_document()
    doc.uri = "http://example.org/"
    doc.meta.add((URIRef(doc.uri),
                  Namespace(util.ns['dct']).title,
                  Literal(resulthead, lang="en")))
    doc.body = elements.Body()
    for r in res:
        if not 'title' in r:
            r['title'] = r['uri']
        doc.body.append(html.Div(
            [html.H2([elements.Link(r['title'], uri=r['uri'])]),
             r['text']], **{'class':'hit'}))

    pages = [html.P(["Results %(firstresult)s-%(lastresult)s of %(totalresults)s" % pager])]
    for pagenum in range(pager['pagecount']):
        if pagenum + 1 == pager['pagenum']:
            pages.append(html.Span([str(pagenum+1)],**{'class':'page'}))
        else:
            querystring['p'] = str(pagenum+1)
            url = environ['PATH_INFO'] + "?" + urlencode(querystring)
            pages.append(html.A([str(pagenum+1)],**{'class':'page',
                                                  'href':url}))
    doc.body.append(html.Div(pages, **{'class':'pager'}))
    # Transform that XHTML into HTML5
    
    # FIXME: this way of transforming a etree to HTML5 is way too
    # complicated, dependent on args['repo'][0], stores temporary
    # files on disk for no good reason, abuses
    # get_transform_configuration with a fake path and duplicates code
    xsltfile = "res/xsl/search.xsl"
    tmpfile = args['documentroot']+"/_searchtmp-%s.xhtml" % os.getpid()
    outfile = args['documentroot']+"/_searchtmp-%s.html" % os.getpid()
    # create a fake path so taht get_transform_configuration selects a
    # resources.xml with correct relative path
    depth = len(list(filter(None,args['searchendpoint'].split("/"))))
    fake_outfile = "%s/%s/search.html" % (args['documentroot'],
                                          "/".join(["fake"]*depth))
    xsltdir = repo.setup_transform_templates(os.path.dirname(xsltfile))
    params = repo.get_transform_configuration(xsltdir,fake_outfile)
    
    repo.render_xhtml(doc,tmpfile)
    repo.transform_html("res/xsl/search.xsl",
                        tmpfile, outfile, params, args['repos'][1:])
    data = util.readfile(outfile,"rb")
    start_response("200 OK", [
        ("Content-Type", "text/html; charset=utf-8"),
        ("Content-Length", str(len(data)))
    ])
    return iter([data])


def _wsgi_api(environ, start_response, args):
    """WSGI method, called by the wsgi app for requests that matches
       ``apiendpoint``."""
    d = dict((str(key),str(environ[key])) for key in environ.keys())
    
    data = json.dumps(dict(d), indent=4).encode('utf-8')
    start_response("200 OK", [
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(data)))
    ])
    return iter([data])
    
    
def _wsgi_static(environ, start_response, args):
    """WSGI method, called by the wsgi app for all other requests not handled
    by :py:func:`~ferenda.Manager.search` or :py:func:`~ferenda.Manager.api`"""

    fullpath = args['documentroot'] + environ['PATH_INFO']
    # we should start by asking all repos "do you handle this path"?
    # default impl is to say yes if 1st seg == self.alias and the rest
    # can be treated as basefile yielding a existing generated file.
    # a yes answer contains a FileWrapper around the repo-selected
    # file and optionally length (but not status, always 200, or
    # mimetype, always text/html). None means no.
    fp = None
    for repo in args['repos']:
        (fp, length, status, mimetype) = repo.http_handle(environ)  # and args?
        if fp:
            status = {200:"200 OK",
                      406:"406 Not Acceptable"}[status]
            iterdata = FileWrapper(fp)
            break
    if not fp:
        if os.path.isdir(fullpath):
            fullpath = fullpath + "index.html"
        if os.path.exists(fullpath):
            ext = os.path.splitext(fullpath)[1]
            if not mimetypes.inited:
                mimetypes.init()
            mimetype = mimetypes.types_map.get(ext, 'text/plain')
            status = "200 OK"
            length = os.path.getsize(fullpath)
            fp = open(fullpath, "rb")
            iterdata = FileWrapper(fp)
        else:
            msg = "<h1>404</h1>The path %s not found at %s" % (environ['PATH_INFO'],
                                                               fullpath)
            mimetype = "text/html"
            status = "404 Not Found"
            length = len(msg.encode('utf-8'))
            fp = six.BytesIO(msg.encode('utf-8'))
            iterdata = FileWrapper(fp)
    start_response(status, [
        ("Content-Type", mimetype),
        ("Content-Length", str(length))
    ])
    return iterdata
    # FIXME: How can we make sure fp.close() is called, regardless of
    # whether it's a real fileobject or a BytesIO object?


loglevels = {'DEBUG': logging.DEBUG,
             'INFO': logging.INFO,
             'WARNING': logging.WARNING,
             'ERROR': logging.ERROR,
             'CRITICAL': logging.CRITICAL}

def setup_logger(level='INFO',filename=None):
    """Sets up the logging facilities and creates the module-global log
       object as a root logger.

    :param name: The name of the logger (used in log messages)
    :type  name: str
    :param level: 'DEBUG','INFO','WARNING','ERROR' or 'CRITICAL'
    :type  level: str
    :param filename: The name of the file to log to. If None, log to stdout
    :type filename: str
    """
    if not isinstance(level, int):
        loglevel = loglevels[level]
        
    l = logging.getLogger() # get the root logger
        
    #if l.handlers == []:
    if filename:
        h = logging.FileHandler(filename)
    else:
        h = logging.StreamHandler()
    for existing_handler in l.handlers:
        #if type(h) == type(existing_handler):
        #    print("A %s already existed, not adding a new one" % h)
        return l

    h.setLevel(level)
    h.setFormatter(
        logging.Formatter(
            "%(asctime)s %(name)s %(levelname)s %(message)s",
            datefmt="%H:%M:%S"))
    l.addHandler(h)
    l.setLevel(level)

    # turn of some library loggers we're not interested in
    for logname in ['requests.packages.urllib3.connectionpool',
                    'rdflib.plugins.sleepycat']:
        logging.getLogger(logname).propagate = False

    return l


def run(argv):
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
    config = _load_config(_find_config_file(),argv)

    # if logfile is set to True, autogenerate logfile name from
    # current datetime. Otherwise assume logfile is set to the desired
    # file name of the log
    log = setup_logger(level=config.loglevel,filename=None)
    if config.logfile:
        if isinstance(config.logfile,bool):
            logfile = "%s/logs/%s.log" % (config.datadir,datetime.now().strftime("%Y%m%d-%H%M%S"))
        else:
            logfile = config.logfile 
        util.ensure_dir(logfile)
        setup_logger(level=config.loglevel,filename=logfile)
    
    enabled = _enabled_classes()  # reads only ferenda.ini using configparser rather than layeredconfig
    # returns {'ferenda.sources.docrepo.DocRepo':'base',...}
    enabled_aliases = dict(reversed(item) for item in enabled.items())
    if len(argv) < 1:
        _print_usage()  # also lists enabled modules
    else:
        # _filter_argv("ecj", "parse", "62008J0034", "--force=True", "--frobnicate")
        #    -> ("ecj", "parse", ["62008J0034"])
        # _filter_argv("ecj", "--frobnicate") -> ("ecj", None, [])
        (classname, action, args) = _filter_argv(argv)
        if action == 'enable':
            try:
                return enable(classname)
            except (ImportError, ValueError) as e:
                log.error(six.text_type(e))
                return None
        elif action == 'runserver':
            args = _setup_runserver_args(config, _find_config_file())
            return runserver(**args) # Note: the actual runserver method never returns

        elif action == 'makeresources':
            repoclasses = _classes_from_classname(enabled, classname)
            args = _setup_makeresources_args(config)
            repos = []
            for cls in repoclasses:
                inst = _instantiate_class(cls, _find_config_file(), argv)
                repos.append(inst)
            return makeresources(repos, **args)

        elif action == 'frontpage':
            repoclasses = _classes_from_classname(enabled, classname)
            args = _setup_frontpage_args(config, argv)
            return frontpage(**args)
            
        elif action == 'all':
            classnames = _setup_classnames(enabled, classname)
            results = OrderedDict()
            for action in ("download", 
                           "parse", "relate", "makeresources",
                           "generate", "toc", "news", "frontpage"):
                if action in ("makeresources", "frontpage"):
                    argscopy = list(args)
                    argscopy.extend(_filter_argv_options(argv))
                    argscopy.insert(0, action)
                    argscopy.insert(0, "all")
                    results[action] = run(argscopy)
                else:
                    results[action] = OrderedDict()
                    for classname in classnames:
                        alias = enabled_aliases[classname]
                        argscopy = list(args)
                        argscopy.extend(_filter_argv_options(argv))
                        if (action in ("parse", "relate", "generate") and
                            "--all" not in argscopy):
                            argscopy.append("--all")
                        argscopy.insert(0, action)
                        argscopy.insert(0, classname)
                        results[action][alias] =  run(argscopy)
            return results
        else:
            if classname == "all":
                ret = []
                for alias, classname in enabled.items():
                    argv_copy = list(argv)
                    argv_copy[0] = alias
                    ret.append(_run_class(enabled, argv_copy))
                return ret
            else:
                return _run_class(enabled, argv)


def enable(classname):
    """Registers a class by creating a section for it in the
    configuration file (``ferenda.ini``). Returns the short-form
    alias for the class.

    >>> enable("ferenda.DocumentRepository") == 'base'
    True
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
    log = setup_logger()
    log.info("Enabled class %s (alias '%s')" % (classname, alias))
    return alias


def setup():
    """Creates a project, complete with configuration file and
    ferenda-build tool. Takes no parameters, but expects ``sys.argv``
    to contain the path to the project being created.

    Checks to see that all required python modules and command line
    utilities are present. Also checks which triple store(s) are
    available and selects the best one (in order of preference:
    Sesame, Fuseki, RDFLib+Sleepycat, RDFLib+SQLite).

    .. note::

       The ``ferenda-setup`` script that gets installed with ferenda is
       a tiny wrapper around this function.

    """
    if len(sys.argv) < 2:
        print(("Usage: %s [project-directory]" % sys.argv[0]))
        return False
    projdir = sys.argv[1]
    if os.path.exists(projdir):
        print(("Project directory %s already exists" % projdir))
        return False
    sitename = os.path.basename(projdir)
    
    ok = _preflight_check()
    if not ok:
        print("There were some errors when checking your environment. Proceed anyway? (y/N)")
        answer = input()
        if answer != "y":
            sys.exit(1)

    # The template ini file needs values for triple store
    # configuration. Find out the best triple store we can use.
    storetype, storelocation, storerepository = _select_triplestore(sitename)
    if not storetype:
        print("Cannot find a useable triple store. Proceed anyway? (y/N)")
        answer = input()
        if answer != "y":
            sys.exit(1)

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

    print("Project created in %s" % projdir)

    # step 3: create WSGI app
    wsgifile = projdir + os.sep + "wsgi.py"
    util.resource_extract('res/scripts/wsgi.py', wsgifile)


def _load_config(filename, argv=[]):
    """Loads general configuration information from ``filename`` (which
should be a full path to a ferenda.ini file) and/or command line
arguments into a :py:class:`~ferenda.LayeredConfig` instance. It
contains a built-in dict of default configuration values which can be
overridden by the config file or command line arguments."""
    
    # FIXME: Expand on this list of defaults? Note that it only
    # pertains to global configuration, not docrepo configuration
    # (those have the get_default_options() method).
    defaults = {'loglevel': 'DEBUG',
                'logfile': True,
                'datadir': 'data',
                'combineresources': False,
                'staticsite': False,
                'sitename': 'MySite',
                'sitedescription': 'Just another Ferenda site'}
    config = LayeredConfig(defaults, filename, argv, cascade=True)
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
    """Given a config object, returns a dict with some of those configuration options, but suitable as arguments for :py:func:`ferenda.Manager.makeresources`. 
    
    :param config: An initialized config object with data from a ferenda.ini file
    :type config: ferenda.LayeredConfig
    :returns: A subset of the same configuration options
    :rtype: dict
    """
    # our config file stores the cssfiles and jsfiles parameters as string
    def getlist(config, key):
        if hasattr(config, key):
            if isinstance(getattr(config,key), six.text_type):
                return literal_eval(getattr(config,key))
            else:
                return getattr(config,key)
        else:
            return []
    
    cssfiles = getlist(config, 'cssfiles')
    jsfiles = getlist(config, 'jsfiles')
    
    return {'resourcedir': config.datadir + os.sep + 'rsrc',
            'combine':     config.combineresources,
            'staticsite':  config.staticsite,
            'cssfiles':    cssfiles,
            'jsfiles':     jsfiles,
            'sitename':    config.sitename,
            'sitedescription': config.sitedescription}


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


def _run_class(enabled, argv):
    """Runs a particular action for a particular class.

    :param enabled: The currently enabled repo classes, as returned by
                    :py:func:`~ferenda.Manager._enabled_classes`
    :type  enabled: dict
    :param argv: An argv-style list of strings, see run (but note
                 that that function replaces ``all`` with every
                 enabled class in turn and then calls this method
                 with the same argv.
    :type argv: list

    If the parameter ``--all`` is given (e.g. ``['myrepo', 'parse',
    '--all']``), the specified command is run once for every available
    file for that action (as determined by each class' implementation
    of :py:meth:`~ferenda.DocumentRepository.list_basefiles_for`).

    """
    log = setup_logger()
    (alias, command, args) = _filter_argv(argv)
    with util.logtime(log.info, "%(alias)s %(command)s finished in %(elapsed).3f sec",
                      {'alias':alias,
                       'command': command}):
        _enabled_classes = dict(reversed(item) for item in enabled.items())

        if alias not in enabled and alias not in _enabled_classes:
            log.error("Class-or-alias %s not enabled" % alias)
            return
        if alias in argv:
            argv.remove(alias)
        if alias in _enabled_classes:  # ie a fully qualified classname was used
            classname = alias
        else:
            classname = enabled[alias]
        cls = _load_class(classname)
        inst = _instantiate_class(cls, argv=argv)
        try:
            clbl = getattr(inst, command)
            assert(callable(clbl))
        except:  # action was None or not a callable thing
            if command:
                log.error("%s is not a valid command for %s" % (command, classname))
            else:
                log.error("No command given for %s" % classname)
            _print_class_usage(cls)
            return

        kwargs = {}
        if command in ('relate','generate','toc','news'):
            # we need to provide the otherrepos parameter
            otherrepos = []
            for othercls in _classes_from_classname(enabled, 'all'):
                if othercls != inst.__class__:
                    otherrepos.append(_instantiate_class(othercls, argv=argv))
            kwargs['otherrepos'] = otherrepos
        
        if hasattr(inst.config, 'all') and inst.config.all == True:
            res = []
            # semi-magic handling
            ret = cls.setup(command,inst.config)
            if ret == False:
                log.info("%s %s: Nothing to do!" % (alias, command))
            else: 
                # TODO: use multiprocessing.pool.map or celery for
                # task queue handling
                for basefile in inst.list_basefiles_for(command):
                    try:
                        res.append(clbl(basefile, **kwargs))
                    except Exception as e:
                        log.error("%s of %s failed: %s" % (command,basefile,e))
                        res.append(sys.exc_info())
                cls.teardown(command,inst.config)
        else:
            res = clbl(*args, **kwargs)
    return res

def _instantiate_class(cls, configfile="ferenda.ini", argv=[]):
    """Given a class object, instantiate that class and make sure the
       instance is properly configured given it's own defaults, a
       config file, and command line parameters."""

    inst = cls()  # no options -- we re-set .config and .store explicitly
    defaults = inst.get_default_options()
    defaults[cls.alias] = {}
    globalcfg = LayeredConfig(defaults,
                              configfile,
                              argv, cascade=True)
    classcfg = getattr(globalcfg, cls.alias)
    inst.config = classcfg
    inst.store = inst.documentstore_class(classcfg.datadir + os.sep + inst.alias,
                                          downloaded_suffix = inst.downloaded_suffix,
                                          storage_policy = inst.storage_policy)
    # FIXME: this is a quick hack for controlling trace loggers for
    # ferenda.sources.legal.se.SFS. Must think abt how to generalize
    # this.
    if hasattr(inst,'trace'):
        for tracelog in inst.trace:
            try:
                
                loglevel = getattr(inst.config.trace, tracelog)
                log = logging.getLogger(inst.alias+"."+tracelog)
                log.setLevel(loglevels.get(loglevel,'DEBUG')) 
            except AttributeError:
                logging.getLogger(inst.alias+"."+tracelog).propagate = False
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
    actions = _list_class_usage(cls)
    if actions:
        print("Valid actions are:")
    else:
        print("No valid actions in this class (%s). Did you forget the @action decorator?" % cls.__name__)
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
        if hasattr(attr, "runnable"):
            res[attr.__name__] = attr.__doc__.split("\n")[0]
    return res


def _filter_argv(args):
    """Given a command line, extract a tuple containing the
    class-or-alias to use, the command to run, and the positional
    arguments for that command. Strip away all --options.

    :param args: A sys.argv style command line argument list.
    :type args: list
    :returns: (class-or-alias, command, [positional-arguments])
    :rtype: tuple
    
    """
    alias = None
    command = None
    commandargs = []
    if len(args) > 0 and not args[0].startswith("--"):
        alias = args[0]
    if len(args) > 1 and not args[1].startswith("--"):
        command = args[1]
    if len(args) > 2:
        for arg in args[2:]:
            if not arg.startswith("--"):
                commandargs.append(arg)
    return (alias, command, commandargs)

def _filter_argv_options(args):
    options=[]
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
        raise ValueError("Classname '%s' should be the fully qualified name of a class (i.e. 'modulename.%s')" % (classname, classname))
    # NOTE: Don't remove this line! (or make sure testManager works after you do) 
    # log = logging.getLogger()
    log = setup_logger()

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
        raise errors.ConfigurationError("Config file %s not found (relative to %s)" % (inipath, os.getcwd()))
    return inipath



def _setup_runserver_args(config, inifilename):
    """Given a config object, returns a dict with some of those
       configuration options, but suitable as arguments for
       :py:func:`ferenda.Manager.runserver`.
    
    :param config: An initialized config object with data from a ferenda.ini
                   file
    :type config: ferenda.LayeredConfig
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
        config_as_dict = dict([(k,getattr(instconfig,k)) for k in instconfig])
        inst = cls(**config_as_dict)
        repos.append(inst)

    #for repo in repos:
    #    print("Repo %r %s: config.datadir is %s" % (repo, id(repo), repo.config.datadir))
    return {'port':           port,
            'documentroot':   relativeroot,
            'apiendpoint':    config.apiendpoint,
            'searchendpoint': config.searchendpoint,
            'repos':          repos}

def _setup_frontpage_args(config, argv):
    # FIXME: This way of instantiating repo classes should maybe be
    # used by _setup_makeresources_args as well?
    #
    # FIXME: why do we pass a config object when we re-read
    # ferenda.ini at least twice (_enabled_classes and
    # _instantiate_class) ?!
    enabled = _enabled_classes()  # reads only ferenda.ini using configparser rather than layeredconfig
    repoclasses = _classes_from_classname(enabled, classname="all")
    repos = []
    for cls in repoclasses:
        inst = _instantiate_class(cls, _find_config_file(), argv)
        repos.append(inst)
    return {'sitename': config.sitename,
            'path': config.datadir + "/index.html",
            'repos': repos}
    

def _filepath_to_urlpath(path,keep_segments=2):
    """
    :param path: the full or relative filepath to transform into a urlpath
    :param keep_segments: the number of directory segments to keep (the ending filename is always kept)
    """
    # data/repo/rsrc/js/main.js, 3 -> repo/rsrc/js/main.js
    # /var/folders/tmp4q6b1g/rsrc/resources.xml, 1 -> rsrc/resources.xml
    urlpath = os.sep.join(path.split(os.sep)[-(keep_segments+1):])
    # print("_filepath_to_urlpath (%s): %s -> %s" % (keep_segments, path, urlpath))
    return urlpath.replace(os.sep, "/")


def _preflight_check():
    """Perform a check of needed modules and binaries."""
    pythonver = (2, 6, 0)

    # Module, min ver, required
    modules = (
        ('bs4', '4.2.0', True),
        # ('lxml', '3.2.0', True), # has no top level __version__ property
        ('rdflib', '4.0', True),
        ('html5lib', '1.0b1', True),
        ('requests', '1.2.0', True),
        ('six', '1.2.0', True),
        ('jsmin', '2.0.2', True),
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
        print("ERROR: ferenda requires Python %s or higher, you have %s" %
              (".".join(pythonver), sys.version.split()[0]))
        success = False
    else:
        print("Python version %s OK" % sys.version.split()[0])

    # 2: Check modules -- TODO: Do we really need to do this?
    for (mod, ver, required) in modules:
        try:
            m = __import__(mod)
            version = getattr(m,'__version__', None)
            if isinstance(version, tuple):
                version = ".".join([str(x) for x in version])
            if not hasattr(m, '__version__'):
                print("WARNING: Module %s has no version information, it might be older than required" % mod)
            elif version < ver: # FIXME: use util.numcmp?
                if required:
                    print("ERROR: Module %s has version %s, need %s" %
                          (mod, version, ver))
                    success = False
                else:
                    print("WARNING: Module %s has version %s, would like to hav %s" %
                          (mod, version, ver))
            else:
                print("Module %s OK" % mod)
        except ImportError:
            if required:
                print("ERROR: Missing module %s" % mod)
                success = False
            else:
                print("WARNING: Missing (non-essential) module %s" % mod)

    # 3: Check binaries
    for (cmd, arg) in binaries:
        try: 
            ret = subprocess.call([cmd, arg],
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)
            if ret == 127:
                print("ERROR: Binary %s failed to execute")
                success = False
            else:
                print("Binary %s OK" % cmd)
        except OSError as e:
            print("ERROR: Binary %s failed: %s" % (cmd, e))
            success = False

    return success


def _select_triplestore(sitename):
    # Try triplestores in order: Fuseki, Sesame, Sleepycat, SQLite,
    # and return configuration for the first triplestore that works.


    # 1. Fuseki
    try:
        triplestore = os.environ.get('FERENDA_TRIPLESTORE_LOCATION',
                                     'http://localhost:3030')
        resp = requests.get(triplestore + "/ds/data?default")
        resp.raise_for_status()
        print("Fuseki server responding at %s" % triplestore)
        # TODO: Find out how to create a new datastore in Fuseki
        # programatically so we can use
        # http://localhost:3030/$SITENAME instead
        return('FUSEKI', triplestore, 'ds')
    except (requests.exceptions.HTTPError,
            requests.exceptions.ConnectionError) as e:
        print("... Fuseki not available at %s: %s" % (triplestore, e))
        pass

    # 2. Sesame
    try:
        triplestore = os.environ.get('FERENDA_TRIPLESTORE_LOCATION',
                                     'http://localhost:8080/openrdf-sesame')
        resp = requests.get(triplestore + '/protocol')
        resp.raise_for_status()
        workbench = triplestore.replace('openrdf-sesame', 'openrdf-workbench')
        print("Sesame server responding at %s (%s)" % (triplestore, resp.text))
        # TODO: It is possible, if you put the exactly right triples
        # in the SYSTEM repository, to create a new repo
        # programmatically.
        print("""You still need to create a repository at %(workbench)s ->
New repository. The following settings are recommended:

    Type: Native Java store
    ID: %(sitename)s
    Title: Ferenda repository for %(sitename)s
    Triple indexes: spoc,posc,cspo,opsc,psoc
        """ % locals())
        return('SESAME', triplestore, sitename)
    except (requests.exceptions.HTTPError,
            requests.exceptions.ConnectionError) as e:
        print("... Sesame not available at %s: %s" % (triplestore, e))
        pass

    # 3. RDFLib + Sleepycat
    try:
        t = TripleStore("test.db", "ferenda", storetype=TripleStore.SLEEPYCAT)
        # No boom?
        print("Sleepycat-backed RDFLib triplestore seems to work")
        return ('SLEEPYCAT', 'data/ferenda.db', 'ferenda')
    except ImportError as e:
        print("...Sleepycat not available: %s" % e)

    # 4. RDFLib + SQLite
    try:
        t = TripleStore("test.sqlite", "ferenda", storetype=TripleStore.SQLITE)
        print("SQLite-backed RDFLib triplestore seems to work")
        return ('SQLITE', 'data/ferenda.sqlite', 'ferenda')
    except ImportError as e:
        print("...SQLite not available: %s" % e)
    
    print("No usable triplestores, the actions 'relate', 'generate' and 'toc' won't work")
    return (None, None, None)
