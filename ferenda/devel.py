# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *
import builtins

from ast import literal_eval
from bz2 import BZ2File
from collections import OrderedDict, defaultdict, Counter
from difflib import unified_diff
from datetime import datetime
from itertools import islice
from io import BytesIO, StringIO
from tempfile import mkstemp
from time import sleep
from operator import attrgetter, itemgetter
from pprint import pformat
import codecs
import fileinput
import functools
import inspect
import json
import logging
import os
import random
import re
import shutil
import sys
import time
import traceback
import importlib
from wsgiref.util import request_uri
from urllib.parse import parse_qsl, urlencode
from cached_property import cached_property

from rdflib import Graph, URIRef, RDF, Literal
from rdflib.namespace import DCTERMS
from layeredconfig import LayeredConfig, Defaults
from lxml import etree
from lxml.etree import XMLSyntaxError
from ferenda.thirdparty.patchit import PatchSet, PatchSyntaxError, PatchConflictError
from werkzeug.routing import Rule
from werkzeug.wrappers import Response

from ferenda.compat import Mock
from ferenda import (TextReader, TripleStore, FulltextIndex, WSGIApp,
                     Document, DocumentRepository,
                     CompositeRepository, DocumentEntry, Transformer,
                     RequestHandler, ResourceLoader)
from ferenda.elements import serialize
from ferenda import decorators, util, manager
from ferenda.manager import enable
from ferenda.requesthandler import login_required

class DummyStore(object):

    def __init__(self, path, **kwargs):
        pass  # pragma: no cover

    def list_basefiles_for(self, action, basedir=None, force=True):
        return []  # pragma: no cover

    def list_versions_for_basefiles(self, basefiles, action):
        return [] # pragma: no cover



class DevelHandler(RequestHandler):

    @property
    def rules(self):
        return [Rule('/devel/',      endpoint=self.handle_dashboard),
                Rule('/devel/build', endpoint=self.handle_build),
                Rule('/devel/logs',  endpoint=self.handle_logs),
                Rule('/devel/streaming-test',  endpoint=self.handle_streaming_test),
                Rule('/devel/change-options',  endpoint=self.handle_change_options),
                Rule('/devel/patch',  endpoint=self.handle_patch)]


    @login_required
    def handle_dashboard(self, request, **values):
        def compare_classnames(given, inspected):
            # repoconfig.class can be "lagen.nu.SFS" and classname
            # "lagen.nu.sfs.SFS". Unify this according to this
            # heuristic (a proper solution would involve examining
            # varius import statements in __init__.py files
            if inspected == given:
                return True
            segments = inspected.split(".")
            if segments[-1].lower() == segments[-2].lower():
                inspected = ".".join(segments[:-2] + segments[-1:])
            return inspected == given
        
        if request.method == 'POST':
            statusmsg = errmsg = ""
            if request.form['action'].lower() == "enable":
                alias = enable(request.form['repo'])
                statusmsg = "Enabled repository %s (%s)" % (alias, request.form['action'])
            else:
                errmsg = "Sorry, support for %s %s is not yet implemented -- you'll have to change ferenda.ini by hand" % (
                    request.form['action'], request.form['repo'])

        # 1 create links to other devel tools (build, mkpatch, logs)
        tools = []
        for rule in self.rules:
            if rule.endpoint == self.handle_dashboard:
                continue
            tools.append({'href': rule.rule,
                          'name': rule.endpoint.__name__.split("_",1)[1].replace("_", " ").capitalize(),
                          'doc': rule.endpoint.__doc__})
        # 2 create a list of available repos that we can enable
        # 3 list currently enabled repos and
        #   3.1 their current status (downloaded, parsed, generated documents etc)
        #   3.2 list available build actions for them
        # Also, user-friendly descriptions for the first few steps that you can take
        config = self.repo.config._parent
        possible_repos = []
        reported_repos = set()
        for path in config.systempaths: #  normally [".."] or ["ferenda"]
            for filename in util.list_dirs(path, ".py"):
                if "/doc/" in filename or "/test/" in filename or "/res/" in filename or "/tools/" in filename:
                    continue
                # transform py file "ferenda/lagen/nu/sfs.py" > "lagen.nu.sfs"
                modulename = filename[len(path)+1:-3].replace(os.sep, ".")
                try:
                    m = importlib.import_module(modulename)
                    for cls in [o for (n,o) in inspect.getmembers(m) if inspect.isclass(o) and issubclass(o, DocumentRepository) and o.alias]:
                        if cls.alias == "base":
                            continue
                        classname = cls.__module__ + "." + cls.__name__
                        if classname in reported_repos:
                            continue
                        repoconfig = getattr(config, cls.alias, None)
                        enabled = bool(repoconfig and compare_classnames(getattr(repoconfig, 'class'), classname))
                        r = {'cls': cls,
                             'alias': cls.alias,
                             'classname': classname,
                             'enabled': enabled,
                             'toggle': 'Disable' if enabled else 'Enable',
                             'doc': str(getattr(cls, '__doc__', '')).split("\n")[0]}
                        if r['enabled']:
                            blacklist = ("datadir", "patchdir",
                                         "processes", "force", "parseforce",
                                         "generateforce", "fsmdebug",
                                         "refresh", "download", "url",
                                         "develurl", "fulltextindex", "relate",
                                         "clientname", "bulktripleload",
                                         "class", "storetype", "storelocation",
                                         "storerepository", "indextype",
                                         "indexlocation", "combineresources",
                                         "staticsite", "legacyapi", "sitename",
                                         "sitedescription", "apiendpoint",
                                         "searchendpoint", "toc", "news",
                                         "loglevel", "logfile", "all",
                                         "disallowrobots", "wsgiappclass",
                                         "serverport", "authkey", "profile",
                                         "wsgiexceptionhandler", "systempaths",
                                         "alias", "action", "arguments")
                            c = getattr(config, cls.alias)
                            r['config'] = dict([(k, repr(getattr(c, k))) for k in c if k not in blacklist])
                        possible_repos.append(r)
                        reported_repos.add(classname)
                except (ImportError, FileNotFoundError, NameError):
                    pass
        return self.render_template("""
{% if statusmsg %}
<div class="alert alert-success" role="alert">
  {{statusmsg}}
</div>
{% endif %}

{% if errmsg %}
<div class="alert alert-danger" role="alert">
  {{errmsg}}
</div>
{% endif %}

<p>Welcome to the ferenda dashboard. Here you can configure and monitor
your ferenda installation, and access other tools for maintaining your
documents.</p>
<p>{{errmsg}}</p>
<p>{{statusmsg}}</p>

<h2>Tools</h2>
<ul>
{% for tool in tools %}
<li>
<a href="{{tool.href}}">{{tool.name}}</a>: {{ tool.doc }}
</li>
{% endfor %}
</ul>
<h2>Available repositories</h2>
<table class="table">
<tr><th>repo</th><th>description</th><th>enabled</th><th>options</th></tr>
{% for repo in possible_repos %}
<tr>
<td><big><a href="repo.cls.start_url">{{ repo.alias }}</a></big><br/><small>{{ repo.classname }}</small></td>
<td>{{ repo.doc }}</td>
<td>
<form method="POST">
<input type="hidden" name="repo" value="{{repo.classname}}"/>
<input type="hidden" name="action" value="{{repo.toggle}}"/>
<button type="submit" class="btn {% if not(repo.enabled) %}btn-primary{% endif %}">{{repo.toggle}}</button>
</form>
</td>
<td><small>{% if repo.enabled %}
{% for k in repo.config %}
{{ k }}: {{ repo.config[k] }}<br/>
{% endfor %}
{% endif %}</small></td>
</tr>
{% endfor %}
</table>
""", "Dashboard", possible_repos=possible_repos, enabled=enabled, config=config, tools=tools)


    @login_required
    def handle_build(self, request, **values):
        """Perform any action that the command line tool ferenda-build.py can do (download, parse, generate etc), over the web"""
        if request.args:
            if request.args.get("stream") == "true":
                argv = [request.args[x] for x in ('repo', 'action', 'basefile', 'all', 'force', 'refresh') if request.args.get(x)]
                argv.append('--loglevel=DEBUG')
                manager.run(argv)
            else:
                label = "Running %(repo)s %(action)s %(basefile)s %(all)s %(force)s %(refresh)s" % defaultdict(str, request.args.to_dict())
                streamurl = request.url + "&stream=true"
                return self.render_template("""
<h2>ferenda-build</h2>
                <pre class="pre-scrollable" id="streaming-log-output" src="{{streamurl|e}}">
</pre>""", label, streamurl=streamurl)
            
        else:
            return self.render_template("""
<form class="form-inline">
  <div class="form-group">
    <label class="sr-only" for="repo">repo</label>
    <input class="form-control" id="repo" name="repo" type="text" placeholder="repo"/>
    <label class="sr-only" for="action">action</label>
    <input class="form-control" name="action" id="action" type="text" placeholder="action"/>
    <label class="sr-only" for="basefile">basefile</label>
    <input class="form-control" name="basefile" id="basefile" type="text" placeholder="basefile"/>
  </div>
  <div class="form-check form-check-inline">
    <input class="form-check-input" id="all" name="all" type="checkbox" value="--all"/>
    <label class="form-check-label" for="all">--all</label>
  </div>
  <div class="form-check form-check-inline">
    <input class="form-check-input" id="force" name="force" type="checkbox" value="--force"/>
    <label class="form-check-label" for="force">--force</label>
  </div>
  <div class="form-check form-check-inline">
    <input class="form-check-input" id="refresh" name="refresh" type="checkbox" value="--refresh"/>
    <label class="form-check-label" for="refresh">--refresh</label>
  </div>
  <div class="form-group">
    <button class="btn btn-default" type="submit">Build</button>
  </div>
</form>""", "build")


    @login_required
    def handle_streaming_test(self, request, **values):
        """Diagnostic tool to see if long-running processes are able to stream their output to the web browser"""
        if request.values.get('stream') == 'true':
            log = logging.getLogger(__name__)
            log.debug("Debug messages should work")
            sleep(1)
            log.info("Info messages should work")
            sleep(1)
            log.warning("Warnings should, unsurprisingly, work")
        else:
            return self.render_template("""
            <pre class="pre-scrollable" id="streaming-log-output" src="/devel/streaming-test?stream=true"></pre>""", "Streaming-test")


    @login_required
    def handle_change_options(self, request, **values):
        """Display and change parse options for individual documents"""
        # this method changes the options and creates a response page
        # that, in turn, does an ajax request that ends up calling
        # handle_change_parse_options_stream
        if request.method == 'POST':
            repo = request.form['repo']
            subrepo = request.form['subrepo']
            basefile = request.form['basefile']
            newvalue = request.form['newvalue']
            reason = request.form['reason']
            inst = self.repo._repo_from_alias(repo)
            optionsfile = inst.resourceloader.filename("options/options.py")
            want = '("%s", "%s"):' % (repo, basefile)
            lineidx = None
            out = ""
            with open(optionsfile) as f:
                for idx, line in enumerate(f):
                    if want in line:
                        lineidx = idx
                        currentvalue = re.search(': "([^"]+)",', line).group(1)
                        line = line.replace(currentvalue, newvalue)
                        line = line.rstrip() + " # " + reason + "\n"
                    out += line
            util.writefile(optionsfile, out)
            # now we must invalidate the cached property
            if 'parse_options' in inst.__dict__:
                del inst.__dict__['parse_options']
            if lineidx:
                datasrc = "%s?repo=%s&subrepo=%s&basefile=%s&stream=true" % (
                    environ['PATH_INFO'],
                    repo,
                    subrepo,
                    basefile)
                return self.render_template("""
<div>
  <h2>Changing options for {{basefile}} in repo {{repo}}</h2>
  <p>Changed option at line {{lineidx}} from <code>{{currentvalue}}</code> to <code>{{newvalue}}</code></p>
  <p>Now downloading and processing (please be patient...)</p>
  <pre class="pre-scrollable" id="streaming-log-output" src="{{datasrc}}"></pre>
</div>""", "Change options", basefile=basefile,
                             repo=repo, lineidx=lineidx,
                             currentvalue=currentvalue,
                             newvalue=newvalue, datasrc=datasrc)
            else:
                return self.render_template("""
<div>
  <h2>Couldn't change options for {{basefile}} in repo {{repo}}</h2>
  <p>Didn't manage to find a line matching <code>{{want}}</code> in <code>{{optionsfile}}</p>
</div>""", "Change options", basefile=basefile, repo=repo, want=want, optionsfile=optionsfile)
        elif request.args.get("stream") == "true":
            repoconfig = getattr(self.repo.config._parent, request.form['repo'])
            repoconfig.loglevel = "DEBUG"
            repo = self.repo._repo_from_alias(request.form['repo'], repoconfig=repoconfig)
            if 'subrepo' in request.form:
                subrepoconfig = getattr(self.repo.config._parent, request.form['subrepo'])
                subrepoconfig.loglevel = "DEBUG"
                subrepo = self.repo._repo_from_alias(request.form['subrepo'], repoconfig=subrepoconfig)
            else:
                subrepo = repo
            basefile = request.form['basefile']
            rootlogger.info("Downloading %s" % basefile)
            subrepo.config.refresh = True  # the repo might have a partial download, eg of index HTML page but without PDF document
            subrepo.download(basefile)
            # sleep(1)
            rootlogger.info("Parsing %s" % basefile)
            repo.parse(basefile)
            # sleep(1)
            rootlogger.info("Relating %s" % basefile)
            repo.relate(basefile)
            # sleep(1)
            rootlogger.info("Generating %s" % basefile)
            repo.generate(basefile)
        else:
            self.render_template("""
<div>
<form method="POST">
  <label>Repo: 
    <input name="repo"/>
  </label>
  <label>Subrepo (if applicable):
    <input name="subrepo"/>
  </label>
  <label>Basefile:
    <input name="basefile"/>
  </label>
  <label>
    Action:
    <select name="action">
      <option>download</option>
      <option selected="selected">parse</option>
      <option>relate</option>
      <option>generate</option>
    </selection>
  </label>
  <label>
    New value:
    <input name="newvalue">
  </label>
  <input type="submit" class="btn btn-default" value="Importera dokument"/>
<form>
</div>""", "Change options for a specific basefile")


    @login_required
    def handle_patch(self, request, **values):
        """Create patch files for documents for redacting or correcting data in the source documents"""
        def open_intermed_text(repo, basefile, mode="rb"):
            intermediatepath = repo.store.intermediate_path(basefile)
            opener = open
            if repo.config.compress == "bz2":
                intermediatepath += ".bz2"
                opener = BZ2File
            if os.path.exists(intermediatepath):
                stage = "intermediate"
                outfile = intermediatepath
            else:
                stage = "download"
                outfile = repo.store.downloaded_path(basefile)
            fp = opener(outfile, mode)
            return fp
        def format_exception():
            exc_type, exc_value, tb = sys.exc_info()
            tblines = traceback.format_exception(exc_type, exc_value, tb)
            tbstr = "\n".join(tblines)
            return tbstr

        if not request.args:
            # start page: list available patches maybe? form with repo
            # names and textbox for basefile?
            return self.render_template("""
<div>
  <h2>Create a new patch</h2>
  <form>
    <div class="form-group">
      <label for="repo">repo
        <input type="text" id="repo" name="repo" class="form-control"/>
      </label>
      <label for="basefile">basefile
        <input type="text" id="basefile" name="basefile" class="form-control"/>
      </label>
    </div>
  </form>
</div>""", "patch")
        else:
            alias = request.args['repo']
            basefile = request.args['basefile']
            repo = self.repo._repo_from_alias(alias)
            patchstore = repo.documentstore_class(repo.config.patchdir +
                                                  os.sep + repo.alias)
            patchpath = patchstore.path(basefile, "patches", ".patch")
            if request.method == 'POST':
                self.repo.mkpatch(repo, basefile, request.args.get('description',''),
                                  request.args['filecontents'].replace("\r\n", "\n"))
                log = []
                do_generate = request.args.get('generate') == "true"
                if request.args.get('parse') == "true":
                    repo.config.force = True
                    log.append("Parsing %s" % basefile)
                    try:
                        repo.parse(basefile)
                        log.append("Parsing successful")
                    except Exception:
                        log.append(format_exception())
                        do_generate = False
                if do_generate:
                    repo.config.force = True
                    repo.generate(basefile)
                    log.append("Generating %s")
                    try:
                        repo.generate(basefile)
                        log.append('Generation successful: <a href="%s">%s</a>' % (repo.canonical_uri(basefile)), basefile)
                    except Exception:
                        log.append(Pre([format_exception()]))

                patchexists = os.path.exists(patchpath)
                if patchexists:
                    patchcontent = util.readfile(patchpath)
                else:
                    patchcontent = None
                return self.render_template("""
<div>
  {% if patchexists %}
  <h2>Patch generated at {{patchpath}}</h2>
  <p>Contents of new patch</p>
  <pre>{{patchcontent}}</pre>
  {% else %}
  <h2>Patch was not generated</h2>
  {% endif %}
  {% for line in log %}
  <p>{{line}}</p>
  {% endfor %)
</div>""", "patch", patchexists=patchexists, patchpath=patchpath, patchcontent=patchcontent, log=log)
            else:
                fp = open_intermed_text(repo, basefile)
                outfile = util.name_from_fp(fp)
                text = fp.read().decode(repo.source_encoding)
                fp.close
                patchdescription = None
                if os.path.exists(patchpath) and request.args.get('ignoreexistingpatch') != 'true':
                    ignorepatchlink = request.url + "&ignoreexistingpatch=true"
                    with codecs.open(patchpath, 'r', encoding=repo.source_encoding) as pfp:
                        if repo.config.patchformat == 'rot13':
                            pfp = StringIO(codecs.decode(pfp.read(), "rot13"))
                        try:
                            patchcontent = util.readfile(patchpath)
                            ps = PatchSet.from_stream(pfp)
                            lines = text.split("\n")
                            offsets = ps.patches[0].adjust(lines)
                            text = "\n".join(ps.patches[0].merge(lines))
                            if ps.patches[0].hunks[0].comment:
                                patchdescription = ps.patches[0].hunks[0].comment
                            else:
                                patchdescription = ""
                            instructions = "existing-patch"
                        except (PatchSyntaxError, PatchConflictError) as e:
                            instructions = "existing-patch-fail"
                            patchdescription = ""

                self.render_template("""
<div>
  <h2>Editing {{outfile}}</h2>
  {% if instructions == "existing-patch" %}
  <p>Existing patch at {{patchpath}} has been applied 
  (<a href="{{ignorepatchlink}}">ignore existing patch</a>)</p>
  <p>Contents of that patch, for reference</p>
  <pre>{{patchcontent}}</pre>
    {% if offsets %}
    <p>Patch did not apply cleanly, the following adjustments were made: {{offsets}}</p>
    {% endif %}                
  {% elif instructions == "existing-patch-fail" %}
  <p>Existing patch at {{patchpath}} could not be applied 
  (<a href="{{ignorepatchlink}}">ignore existing patch</a></p>
  <p>The error was</p>
  <pre>{{formatted_exception}}</pre>
  {% endif %}
  <p>Change the original data as needed</p>
  <form method="POST">
    <textarea class="form-control" id="filecontents" name="filecontents" rows="30" cols="80"></textarea>
    <br/>
    <div class="form-group">
      <label>Description of patch
        <input class="form-control" name="description"/>
      </label>
    </div>
    <div class="form-check">
      <label class="form-check-label">
        Parse resulting file
        <input class="form-check-input" id="parse" name="parse" type="checkbox" value="true" checked="checked"/>
      </label>
    </div>
    <div class="form-check">
      <label class="form-check-label">
        Generate HTML from results of parse
        <input class="form-check-input"name="generate" type="checkbox" value="true" checked="checked"/>
      </label>
    </div>
    <input id="repo" name="repo" type="hidden" value="{{alias}}"/>
    <input id="basefile" name="basefile" type="hidden" value="{{basefile}}"/>
    <button class="btn btn-default" type="submit">Create patch</button>
  </form>
</div>""", "patch", outfile=outfile, alias=alias, basefile=basefile)

    def analyze_log(self, filename, listerrors=False):
        modules = defaultdict(int)
        locations = defaultdict(int)
        locationmsg = {}
        errors = []
        output = StringIO()
        with open(filename) as fp:
            for line in fp:
                try:
                    timestamp, module, level, message = line.split(" ", 3)
                except ValueError:
                    continue
                if level == "ERROR":
                    if module == "root":
                        module = message.split(" ", 1)[0]
                    modules[module] += 1
                    m = re.search("\([\w/]+.py:\d+\)", message)
                    if m:
                        location = m.group(0)
                        locations[location] += 1
                        if location not in locationmsg:
                            locationmsg[location] = message.strip()
                    if listerrors:
                        m = re.match("([\w\.]+) (\w+) ([^ ]*) failed", message)
                        if m:
                            errors.append((m.group(1), m.group(3)))
        if listerrors:
            for repo, basefile in errors:
                print(repo,basefile, file=output)
        else:
            print("Top error modules:", file=output)
            self.printdict(modules, file=output)
            print("Top error messages:", file=output)
            self.printdict(locations, locationmsg, file=output)
        return output.getvalue()

    def printdict(self, d, labels=None, file=sys.stdout):
        # prints out a dict with int values, sorted by these
        for k in sorted(d, key=d.get, reverse=True):
            if labels:
                lbl = labels[k]
            else:
                lbl = k
            print("%4d %s" % (d[k], lbl), file=file)

    re_message_loc = re.compile
    def analyze_buildstats(self, logfilename):
        output = StringIO()
        counters = defaultdict(Counter)
        msgloc = re.compile(" \([\w/]+.py:\d+\)").search
        eventok = re.compile("[^ ]+:? (download|parse|relate|generate|transformlinks) OK").match
        with open(logfilename) as fp:
            for line in fp:
                try:
                    timestamp, module, level, message = line.split(" ", 3)
                except ValueError:
                    continue
                m = msgloc(message)
                if m:
                    message = message[:m.start()]
                m = eventok(message)
                if m:
                    action = m.group(1)
                    counters[action][module] += 1
        sortkeys = defaultdict(int,
                               {"download": -5,
                                "parse": -4,
                                "relate": -3,
                                "generate": -2,
                                "transformlinks": -1})
        actions = sorted(counters.keys(), key=sortkeys.get)  # maybe sort in a reasonable order?
        if actions:
            alength = max([len(a) for a in actions])
            formatstring = "%-" + str(alength) + "s: %d (%s)\n"
            for action in actions:
                actionsum = sum(counters[action].values())
                modcounts = ", ".join(["%s: %s" % (k, v) for k, v in sorted(counters[action].items())])
                output.write(formatstring % (action, actionsum, modcounts))
            # download: 666 (sfs 421, prop 42, soukb 12)
            # parse:    555 (sfs 400, prop 0,  sou 12)
            # relate:   500 (sfs 140, prop 0,  sou 12)
            # generate: 450 (sfs 130, prop 0,  sou 12)
        else:
            output.write("[no successful processing actions found]\n")
        return output.getvalue()
        

    @login_required
    def handle_logs(self, request, **values):
        """Display and summarize logfiles from recent ferenda-build.py runs"""
        logdir = self.repo.config.datadir + os.sep + "logs"
        def elapsed(f):
            filesize = os.path.getsize(f)
            with open(f) as fp:
                first = fp.readline()
                fp.seek(filesize - min(500,filesize - fp.tell()))
                last = fp.read().split("\n")[-2]
            start = datetime.strptime(first.split(" ")[0], "%H:%M:%S")
            end = datetime.strptime(last.split(" ")[0], "%H:%M:%S")
            return end - start  # FIXME: Handle wraparound

        def firstline(f):
            with open(logdir+os.sep+f) as fp:
                # trim uninteresting things from start and end
                l = fp.readline().split(" ", 3)[-1].rsplit(" (", 1)[0]
                if l.strip():
                    return l
                else:
                    return "[log is empty?]"
            
        def linkelement(f):
            return {"filename": f,
                    "href": request.path + "?file=" + f,
                    "firstline": firstline(f),
                    "size": os.path.getsize(logdir + os.sep + f)}

        if not request.args:
            logfiles = sorted([linkelement(f) for f in os.listdir(logdir) if f.endswith(".log")], reverse=True, key=itemgetter('filename'))
            return self.render_template("""
<div>
<ul>
{% for f in logfiles %}
            <li><a href="{{f.href}}"></a><code>{{f.firstline}}</code> {{f.size|filesizeformat}}</li>
{% endfor %}
</ul>
</div>
            """, "logfiles", logfiles=logfiles)
        elif request.args.get('stream'):
            assert 'writer' in values
            logfilename = logdir+os.sep+request.args.get('file')
            with open(logfilename, "rb") as fp:
                for line in fp:
                  values['writer'](line)
        elif request.args.get('file'):
            start = time.time()
            assert re.match("\d{8}-\d{6}.log$", request.args.get('file')), "invalid log file name"
            logfilename = logdir+os.sep+request.args.get('file')
            buildstats = self.analyze_buildstats(logfilename)
            errorstats = self.analyze_log(logfilename)
            if not errorstats:
                errorstats = "[analyze_log didn't return any output?]"
            logcontents = util.readfile(logfilename)
            processtime = time.time() - start
            elapsedtime = elapsed(logfilename).total_seconds()
            if self.repo.config.develurl:
                baseurl = self.repo.config.develurl
            else:
                baseurl = self.repo.config.url
            streamurl = "%s%s?file=%s&stream=true" % (baseurl, request.path[1:], request.args.get('file'))
            pagetitle = "log %s" % logfilename
            return self.render_template("""
<div>
  <p>Log processed in {{"%.3f"|format(processtime)}} s. The logged action took {{"%.0f"|format(elapsedtime)}} s</p>
  <h3>Buildstats</h3>
  <pre>{{buildstats}}</pre>
  <h3>Errors</h3>            
  <pre>{{errorstats}}</pre>
  <h3>Logs</h3>
  <pre class="pre-scrollable logviewer" id="streaming-log-output" src="{{streamurl|e}}">
  </pre>
</div>""", pagetitle, logfilename=logfilename,
                      processtime=processtime,
                      elapsedtime=elapsedtime,
                      buildstats=buildstats,
                      errorstats=errorstats,
                      streamurl=streamurl)


class Devel(object):

    """Collection of utility commands for developing docrepos.

    This module acts as a docrepo (and as such is easily callable from
    ``ferenda-manager.py``), but instead of ``download``, ``parse``,
    ``generate`` et al, contains various tool commands that is useful
    for developing and debugging your own docrepo classes.

    Use it by first enabling it::

        ./ferenda-build.py ferenda.Devel enable

    And then run individual tools like::

        ./ferenda-build.py devel dumprdf path/to/xhtml/rdfa.xhtml

    """

    alias = "devel"

    @decorators.action
    def dumprdf(self, filename, format="turtle"):
        """Extract all RDF data from a parsed file and dump it to stdout.

        :param filename: Full path of the parsed XHTML+RDFa file.
        :type filename: str
        :param format: The serialization format for RDF data (same as for :py:meth:`rdflib.graph.Graph.serialize`)
        :type format: str

        Example::

            ./ferenda-build.py devel dumprdf path/to/xhtml/rdfa.xhtml nt


        """
        print = builtins.print
        g = Graph()
        g.parse(data=util.readfile(filename), format="rdfa")
        # At least the turtle serializer creates UTF-8 data. Fix this!
        print((g.serialize(None, format=format).decode("utf-8")))

    @decorators.action
    def dumpstore(self, format="turtle"):
        """Extract all RDF data from the system triplestore and dump
        it to stdout using the specified format.

        :param format: The serialization format for RDF data (same as
                       for :py:meth:`ferenda.TripleStore.get_serialized`).
        :type format: str

        Example::

            ./ferenda-build.py devel dumpstore nt > alltriples.nt
        """
        # print("Creating store of type %s, location %s, repository %s" %
        #       (self.config.storetype, self.config.storelocation, self.config.storerepository))
        print = builtins.print
        store = TripleStore.connect(self.config.storetype,
                                    self.config.storelocation,
                                    self.config.storerepository)
        print(store.get_serialized(format=format).decode('utf-8'))

#    Not really useful for anything than finding bugs in ferenda itself
#
#    def testlog(self):
#        """Logs a series of messages at various levels, to test that
#        your client code logging configuration behaves as
#        expectedly."""
#        log = logging.getLogger(__name__)
#        log.critical('Log message at CRITICAL level')
#        log.error('Log message at ERROR level')
#        log.warning('Log message at WARNING level')
#        log.info('Log message at INFO level')
#        log.debug('Log message at DEBUG level')
#        sub = logging.getLogger(__name__+'.sublogger')
#        sub.critical('Sublog message at CRITICAL level')
#        sub.error('Sublog message at ERROR level')
#        sub.warning('Sublog message at WARNING level')
#        sub.info('Sublog message at INFO level')
#        sub.debug('Sublog message at DEBUG level')

    @decorators.action
    def csvinventory(self, alias, predicates=None):
        """Create an inventory of documents, as a CSV file. 

        Only documents that have been parsed and yielded some minimum
        amount of RDF metadata will be included.

        :param alias: Docrepo alias
        :type  alias: str

        """
        if predicates is None:
            predicates = ['basefile',
                          'subobjects',  # sections that have rdf:type
                          'rdf:type',
                          'dcterms:identifier',
                          'dcterms:title',
                          'dcterms:published',
                          'prov:wasGeneratedBy',
            ]
        else:
            # predicates are given as a comma separated list, eg ./ferenda-build.py devel csvinventory kkv rpubl:malnummer,rpubl:avgorandedatum,rinfoex:instanstyp,rinfoex:domstol,rinfoex:upphandlande,rinfoex:leverantor,rinfoex:arendetyp,rinfoex:avgorande
            predicates = predicates.split(",")
        import csv
        # if six.PY2:
        #     delimiter = b';'
        #     out = sys.stdout
        # else:
        import codecs
        delimiter = ';'
        out = codecs.getwriter("latin-1")(sys.stdout.detach())
        out.errors = "replace"

        writer = csv.DictWriter(out, predicates, delimiter=delimiter)
        repo = self._repo_from_alias(alias)
        writer.writerow(dict([(p, p) for p in predicates]))
        for basefile in repo.store.list_basefiles_for("relate"):
            baseuri = URIRef(repo.canonical_uri(basefile))
            with repo.store.open_distilled(basefile) as fp:
                row = {}
                if 'basefile' in predicates:
                    row['basefile'] = basefile
                g = Graph().parse(fp, format="xml")
                for (p, o) in g.predicate_objects(baseuri):
                    qname = g.qname(p)
                    if qname in predicates:
                        if isinstance(o, URIRef) and qname not in ("prov:wasDerivedFrom",):
                            row[qname] = g.qname(o)
                        else:
                            # it seems py2 CSV modue expects latin-1
                            # encoded bytestrings (for non-ascii
                            # values), while py3 CSV expects unicode
                            # (sensibly)
                            fld = str(o)
                            # if six.PY2:
                            #     fld = fld.encode("latin-1", errors="replace")
                            row[qname] = fld
                if 'subobjects' in predicates:
                    row['subobjects'] = len(list(g.subject_objects(RDF.type)))
                writer.writerow(row)

    def _repo_from_alias(self, alias, datadir=None, repoconfig=None, basefile=None):
        #  (FIXME: This uses several undocumented APIs)
        mainconfig = self.config._parent
        assert mainconfig is not None, "Devel must be initialized with a full set of configuration"
        if repoconfig is None:
            repoconfig = getattr(mainconfig, alias)
        from ferenda import manager
        repocls = manager._load_class(getattr(repoconfig, 'class'))
        repo = repocls()
        repo.config = getattr(mainconfig, alias)
        # work in all parameters from get_default_options
        for key, val in repocls.get_default_options().items():
            if key not in repo.config:
                LayeredConfig.set(repo.config, key, val, "defaults")
        if datadir is None:
            datadir = repo.config.datadir + os.sep + repo.alias
        repo.store.datadir = datadir
        if isinstance(repo, CompositeRepository) and basefile:
            # try to get at the actual subrepo responsible for this particular basefile
            repo = list(repo.get_preferred_instances(basefile))[0]
        return repo


    @decorators.action
    def mkpatch(self, alias, basefile, description, patchedtext=None):
        """Create a patch file from downloaded or intermediate files. Before
        running this tool, you should hand-edit the intermediate
        file. If your docrepo doesn't use intermediate files, you
        should hand-edit the downloaded file instead. The tool will
        first stash away the intermediate (or downloaded) file, then
        re-run :py:meth:`~ferenda.DocumentRepository.parse` (or
        :py:meth:`~ferenda.DocumentRepository.download_single`) in
        order to get a new intermediate (or downloaded) file. It will
        then calculate the diff between these two versions and save it
        as a patch file in it's proper place (as determined by
        ``config.patchdir``), where it will be picked up automatically
        by :py:meth:`~ferenda.DocumentRepository.patch_if_needed`.

        :param alias: Docrepo alias
        :type  alias: str
        :param basefile: The basefile for the document to patch
        :type  basefile: str

        Example::

            ./ferenda-build.py devel mkpatch myrepo basefile1 "Removed sensitive personal information"

        """
        # 1. initialize the docrepo indicated by "alias"
        # alias might sometimes be the initialized repo so check for that first...
        if isinstance(alias, str):
            repo = self._repo_from_alias(alias, basefile=basefile)
        else:
            repo = alias 
        # 2. find out if there is an intermediate file or downloaded
        # file for basefile. FIXME: unify this with open_intermed_patchedtext
        # in handle_patch
        intermediatepath = repo.store.intermediate_path(basefile)
        if repo.config.compress == "bz2":
            intermediatepath += ".bz2"
        if os.path.exists(intermediatepath):
            stage = "intermediate"
            outfile = intermediatepath
        else:
            stage = "download"
            outfile = repo.store.downloaded_path(basefile)

        if patchedtext:
            # If we provide the new patchedtext as a parameter (assumed to be
            # unicode patchedtext, not bytestring, the existing intermediate
            # file is assumed to be untouched
            patchedtext_lines = patchedtext.split("\n")
            patchedtext_path = ""
        else:
            # but if we don't, the existing intermediate file is
            # assumed to be edited in-place, and we need to stash it
            # away, then regenerate a pristine version of the
            # intermediate file
            fileno, patchedtext_path = mkstemp()
            with os.fdopen(fileno, "wb") as fp:
                patchedtext_lines = util.readfile(outfile, encoding=repo.source_encoding).split("\n")
                fp.write("\n".join(patchedtext_lines).encode(repo.source_encoding))

            # 2.1 if intermediate: after stashing a copy of the
            # intermediate file, delete the original and run
            # parse(config.force=True) to regenerate the intermediate file
            if stage == "intermediate":
                repo.config.force = True
                util.robust_remove(intermediatepath)
                try:
                    repo.config.ignorepatch = True
                    repo.parse(basefile)
                    repo.config.ignorepatch = False
                except:
                    # maybe this throws an error (hopefully after creating
                    # the intermediate file)? may be the reason for
                    # patching in the first place?
                    pass
            # 2.2 if only downloaded: stash a copy, run download_single(config.refresh=True)
            else:
                repo.config.refresh = True
                repo.download_single(basefile)

        # 2.9 re-add line endings to patchedtext_lines
        if patchedtext_lines[-1] == "":  # remove last phantom line
                                         # caused by splitting
                                         # "foo\nbar\n" -- this should
                                         # only be two lines!
            patchedtext_lines.pop()
        patchedtext_lines = [x + "\n" for x in patchedtext_lines]

        # 3. calculate the diff using difflib.

        # Assume that intermediate files use the same encoding as
        # source files
        if repo.config.compress == "bz2":
            opener = BZ2File
        else:
            opener = open
        encoding = repo.source_encoding
        with opener(outfile, mode="rb") as fp:
            outfile_lines = [l.decode(encoding) for l in fp.readlines()]
        difflines = list(unified_diff(outfile_lines,
                                      patchedtext_lines,
                                      outfile,
                                      patchedtext_path))
        if patchedtext_path and os.path.exists(patchedtext_path):
            os.unlink(patchedtext_path)
        # 4. calculate place of patch using docrepo.store.
        patchstore = repo.documentstore_class(repo.config.patchdir +
                                              os.sep + repo.alias)
        patchpath = patchstore.path(basefile, "patches", ".patch")

        # 3.1 If comment is single-line, append it on the first hunks
        # @@-control line
        if description.count("\n") == 0:
            for idx, line in enumerate(difflines):
                if line.startswith("@@") and line.endswith("@@\n"):
                    difflines[idx] = difflines[idx].replace("@@\n",
                                                            "@@ " + description + "\n")
                    break
        else:
            # 4.2 if comment is not single-line, write the rest
            # in corresponding .desc file
            descpath = patchstore.path(basefile, "patches", ".desc")
            util.writefile(descpath, description)

        # 4.1 write patch
        patchcontent = "".join(difflines)
        if patchcontent:
            if repo.config.patchformat == "rot13":
                print("rot13:ing the patch at %s" % patchpath)
                patchcontent = codecs.encode(patchcontent, "rot13")
            # write the patch using the same encoding as the
            # downloaded/intermediate files
            util.writefile(patchpath, patchcontent, encoding=encoding)
            # print("Created patch %s" % patchpath)
            return patchpath
        else:
            print("WARNING: patch would be empty, not creating it")

    @decorators.action
    def parsestring(self, string, citationpattern, uriformatter=None):
        """Parse a string using a named citationpattern and print
        parse tree and optionally formatted uri(s) on stdout.

        :param string: The text to parse
        :type  string: str
        :param citationpattern: The fully qualified name of a citationpattern
        :type  citationpattern: str
        :param uriformatter: The fully qualified name of a uriformatter
        :type  uriformatter: str

        .. note::

           This is not implemented yet

        Example::

            ./ferenda-build.py devel parsestring \\
                "According to direktiv 2007/42/EU, ..." \\
                ferenda.citationpatterns.eulaw

        """
        raise NotImplementedError

    @decorators.action
    def fsmparse(self, functionname, source):
        """Parse a list of text chunks using a named fsm parser and
        output the parse tree and final result to stdout.

        :param functionname: A function that returns a configured
                             :py:class:`~ferenda.FSMParser`
        :type  functionname: str
        :param source:       A file containing the text chunks, separated
                             by double newlines
        :type source:        str

        """
        print = builtins.print
        modulename, classname, methodname = functionname.rsplit(".", 2)
        __import__(modulename)
        m = sys.modules[modulename]
        for name, cls in inspect.getmembers(m, inspect.isclass):
            if name == classname:
                break
        method = getattr(cls, methodname)
        parser = method()
        parser.debug = True
        tr = TextReader(source)
        b = parser.parse(tr.getiterator(tr.readparagraph))
        print(serialize(b))

    @decorators.action
    def queryindex(self, querystring):
        """Query the system fulltext index and return the IDs/URIs for matching documents.

        :param querystring: The query
        :type querystring: str
        """
        print = builtins.print
        # from ferenda.sources.legal.se import Propositioner, Direktiv, SOU, Ds, JO, JK, ARN,DV
        # from lagen.nu import MyndFskr, LNMediaWiki, LNKeyword
        # repos = [Propositioner(), Direktiv(), SOU(), Ds(), JO(), JK(), ARN(), DV(), LNKeyword(), MyndFskr(), LNMediaWiki()]
        repos = []
        index = FulltextIndex.connect(self.config.indextype,
                                      self.config.indexlocation, repos)
        rows, pager = index.query(querystring)
        for row in rows:
            print("%s (%s): %s" % (row['label'], row['uri'], row['text']))

    @decorators.action
    def construct(self, template, uri, format="turtle"):
        """Run the specified SPARQL CONSTRUCT query."""
        print = builtins.print
        sq = util.readfile(template) % {'uri': uri}
        ts = TripleStore.connect(self.config.storetype,
                                 self.config.storelocation,
                                 self.config.storerepository)
        print("# Constructing the following from %s, repository %s, type %s" %
              (self.config.storelocation,
               self.config.storerepository,
               self.config.storetype))
        print("".join(["# %s\n" % x for x in sq.split("\n")]))
        p = {}
        with util.logtime(print,
                          "# %(triples)s triples constructed in %(elapsed).3fs",
                          p):
            res = ts.construct(sq)
            p['triples'] = len(res)
            print(res.serialize(format=format).decode('utf-8'))

    @decorators.action
    def select(self, template, uri, format="json"):
        """Run the specified SPARQL SELECT query."""
        sq = util.readfile(template) % {'uri': uri}
        ts = TripleStore.connect(self.config.storetype,
                                 self.config.storelocation,
                                 self.config.storerepository)
        print = builtins.print
        print("# Constructing the following from %s, repository %s, type %s" %
              (self.config.storelocation,
               self.config.storerepository,
               self.config.storetype))
        print("".join(["# %s\n" % x for x in sq.split("\n")]))
        p = {}
        with util.logtime(print,
                          "# Selected in %(elapsed).3fs",
                          p):
            res = ts.select(sq, format=format)
            # res should be a unicode string, not an encoded bytestring
            # print(res)

            # NO! res must be a bytestring, select should return
            # whatever is the appropriately encoded version for the
            # given format.
            print(res.decode('utf-8'))

    @decorators.action
    def destroyindex(self):
        """Clear all data in the fulltext search index."""
        f = FulltextIndex.connect(self.config.indextype,
                                  self.config.indexlocation,
                                  [])
        f.destroy()
        print("%s index at %s destroyed" % (self.config.indextype,
                                            self.config.indexlocation))

    @decorators.action
    def clearstore(self):
        """Clear all data in the current triplestore."""
        store = TripleStore.connect(self.config.storetype,
                                    self.config.storelocation,
                                    self.config.storerepository)
        triplecount = store.triple_count()
        store.clear()
        print("%s triplestore at %s %s cleared (was %s triples, now %s)" %
              (self.config.storetype, self.config.storelocation,
               self.config.storerepository, triplecount, store.triple_count()))

    @decorators.action
    def wsgi(self, path="/"):
        """Runs WSGI calls in-process."""
        globalconfig = self.config._parent
        from ferenda import manager
        classnames = [
            getattr(
                repoconfig,
                'class') for repoconfig in globalconfig._subsections.values() if hasattr(
                repoconfig,
                'class')]
        repos = [
            manager._instantiate_class(
                manager._load_class(x),
                globalconfig) for x in classnames if x != 'ferenda.Devel']
        url = globalconfig.develurl if 'develurl' in globalconfig else globalconfig.url
        app = WSGIApp(repos, manager._find_config_file(), url=url)
        DEFAULT_HTTP_ACCEPT = 'text/xml, application/xml, application/xhtml+xml, text/html;q=0.9, text/plain;q=0.8, image/png,*/*;q=0.5'
        if "?" in path:
            pathinfo, querystring = path.split("?", 1)
        else:
            pathinfo, querystring = path, ""
        environ = {'HTTP_ACCEPT': DEFAULT_HTTP_ACCEPT,
                   'PATH_INFO':   pathinfo,
                   'SERVER_NAME': 'localhost',
                   'SERVER_PORT': '8000',
                   'QUERY_STRING': querystring,
                   'wsgi.url_scheme': 'http'
                   }

        start_response = Mock()
        for chunk in app(environ, start_response):
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8")
            sys.stdout.write(chunk)

    @decorators.action
    def samplerepo(self, alias, sourcedir, sourcerepo=None, destrepo=None, samplesize=None):
        """Copy a random selection of documents from an external docrepo to the current datadir.""" 
        if not samplesize:
            if 'samplesize' in self.config:
                samplesize = int(self.config.samplesize)
            else:
                samplesize = 10
        if sourcerepo is None:
            sourcerepo = self._repo_from_alias(alias, sourcedir)
        if destrepo is None:
            destrepo = self._repo_from_alias(alias)
        randomsample = True
        if randomsample:
            basefiles = list(sourcerepo.store.list_basefiles_for("parse"))
            samplesize = min([len(basefiles), samplesize])
            basefiles = random.sample(basefiles, samplesize)
        else:
            basefiles = islice(sourcerepo.store.list_basefiles_for("parse"),
                               0, samplesize)
        for basefile in basefiles:
            if isinstance(sourcerepo, CompositeRepository):
                sourcerepo = self._repo_from_alias(alias)
                for cls in sourcerepo.subrepos:
                    subsourcerepo = sourcerepo.get_instance(cls)
                    subsdestrepo = destrepo.get_instance(cls)
                    try:
                        self._samplebasefile(sourcerepo, destrepo, basefile)
                        break  # everything OK, no need to copy more
                    except IOError: # or whatever could happen
                        pass  # try the next one or bail
                else:
                    print("None of the subrepos had basefile %s" % basefile)
            else:
                self._samplebasefile(sourcerepo, destrepo, basefile)

    @decorators.action
    def copyrepos(self, sourcedir, basefilelist):
        """Copy some specified documents to the current datadir.

        The documents are specified in BASEFILELIST, and copied from
        the external directory SOURCEDIR.

        To be used with the output of analyze-error-log.py, eg
        $ ../tools/analyze-error-log.py data/logs/20160522-120204.log --listerrors > errors.txt
        $ ./ferenda-build.py devel copyrepos /path/to/big/external/datadir errors.txt
        """
        with open(basefilelist) as fp:
            basefilelist = []
            for line in fp:
                if line.startswith("("):
                    basefilelist.append(literal_eval(line))
                else:
                    # remove comments
                    line = line.rsplit("#", 1)[0].strip()
                    if not line:  # remove blank lines
                        continue
                    basefilelist.append(line.strip().split(" ", 1))
        destrepos = {}
        sourcerepos = {}
        for (alias, basefile) in basefilelist:
            if alias not in destrepos:
                try:
                    destrepos[alias] = self._repo_from_alias(alias)
                    sourcerepos[alias] = self._repo_from_alias(alias, sourcedir + os.sep + alias)
                except AttributeError: # means the repo alias was wrong
                    continue
            destrepo = destrepos[alias]
            sourcerepo = sourcerepos[alias]
            if isinstance(sourcerepo, CompositeRepository):
                for cls in sourcerepo.subrepos:
                    subsourcerepo = sourcerepo.get_instance(cls)
                    subsourcerepo.store.datadir = (sourcedir + os.sep +
                                                   subsourcerepo.alias)
                    if os.path.exists(subsourcerepo.store.downloaded_path(basefile)):
                        subdestrepo = destrepo.get_instance(cls)
                        self._samplebasefile(subsourcerepo, subdestrepo, basefile)
                        break
            else:
                self._samplebasefile(sourcerepo, destrepo, basefile)


    def _samplebasefile(self, sourcerepo, destrepo, basefile):
        print("  %s: copying %s" % (sourcerepo.alias, basefile))
        src = sourcerepo.store.downloaded_path(basefile)
        dst = destrepo.store.downloaded_path(basefile)
        if os.path.splitext(src)[1] != os.path.splitext(dst)[1]:
            # FIX for DV.py (and possibly other multi-suffix
            # repos) this will yield an incorrect suffix (eg ".zip")
            dst = os.path.splitext(dst)[0] + os.path.splitext(src)[1]
        isrc = sourcerepo.store.intermediate_path(basefile)
        if sourcerepo.config.compress == "bz2":
            isrc += ".bz2"
        idst = destrepo.store.intermediate_path(basefile)
        if destrepo.config.compress == "bz2":
            idst += ".bz2"
        copy = shutil.copy
        copytree = False
        if sourcerepo.store.storage_policy == "dir":
            src = os.path.dirname(src)
            dst = os.path.dirname(dst)
            isrc = os.path.dirname(isrc)
            idst = os.path.dirname(idst)
            if os.path.exists(dst):
                shutil.rmtree(dst)
            if os.path.exists(idst):
                shutil.rmtree(idst)
            # copy = shutil.copytree
            copytree = True
        util.ensure_dir(dst)
        try:
            if copytree:
                shutil.copytree(src,dst,copy_function=copy)
            else:
                copy(src, dst)
            if os.path.exists(isrc):
                util.ensure_dir(idst)
                if copytree:
                    shutil.copytree(isrc, idst, copy_function=copy)
                else:
                    copy(isrc, idst)
        except FileNotFoundError as e:
            print("WARNING: %s" % e)

        # NOTE: For SFS (and only SFS), there exists separate
        # register files under
        # data/sfs/register/1998/204.html. Maybe we should use
        # storage_policy="dir" and handle those things as
        # attachments?
        if os.path.exists(sourcerepo.store.path(basefile, "register", ".html")):
            dst = destrepo.store.path(basefile, "register", ".html")
            util.ensure_dir(dst)
            shutil.copy2(sourcerepo.store.path(basefile, "register", ".html"),
                         dst)
        # also copy the docentry json file
        if os.path.exists(sourcerepo.store.documententry_path(basefile)):
            util.ensure_dir(destrepo.store.documententry_path(basefile))
            shutil.copy(sourcerepo.store.documententry_path(basefile),
                         destrepo.store.documententry_path(basefile))


    @decorators.action
    def samplerepos(self, sourcedir):
        """Copy a random selection of external documents to the current datadir - for all docrepos.""" 
        # from ferenda.sources.general import Static
        from lagen.nu import Static
        if 'samplesize' in self.config:
            samplesize = int(self.config.samplesize)
        else:
            samplesize = 10
        classes = set([Static,]) # blacklist static because of how it
                              # hardcodes .store.staticdir -- leads to
                              # copy attempts with identical src and
                              # dst
        for alias in self.config._parent._subsections:
            if alias == self.alias:  # ie "devel"
                continue
            destrepo = self._repo_from_alias(alias)
            if destrepo.__class__ in classes:
                print("...skipping class %r" % destrepo.__class__)
                continue
            if ('parse' in self.config._parent._subsections[alias] and
                self.config._parent._subsections[alias].parse in
                (False, 'False')):
                print("...skipping class %r (parse=False)" % destrepo.__class__)
                continue
            if isinstance(destrepo, CompositeRepository):
                sourcerepo = self._repo_from_alias(alias)
                for cls in destrepo.subrepos:
                    subdestrepo = destrepo.get_instance(cls)
                    if isinstance(subdestrepo, CompositeRepository):
                        print("...giving up on nested compositerepository")
                        continue
                    if subdestrepo.__class__ in classes:
                        print("...skipping class %r" % subdestrepo.__class__)
                        continue
                    classes.add(subdestrepo.__class__)
                    subsourcerepo = sourcerepo.get_instance(cls)
                    assert id(subdestrepo) != id(subsourcerepo)
                    subsourcerepo.store.datadir = (sourcedir + os.sep +
                                                   subsourcerepo.alias)
                    alias = subsourcerepo.alias
                    aliasdir = subsourcerepo.store.datadir
                    print("%s/%s: Copying docs from  %s" %
                          (sourcerepo.alias, alias, aliasdir))
                    self.samplerepo(alias, aliasdir, subsourcerepo,
                                    subdestrepo,
                                    samplesize=round(samplesize/
                                                     len(destrepo.subrepos)))
            else:
                classes.add(destrepo.__class__)
                aliasdir = sourcedir+os.sep+alias
                print("%s: Copying docs from %s" % (alias, aliasdir))
                self.samplerepo(alias, aliasdir)

    @decorators.action
    def statusreport(self, alias=None):
        """Generate report on which files parse()d OK, with errors, or failed.

        Creates a servable HTML file containing information about how
        the last parse went for each doc in the given repo (or all
        repos if none given).

        """
        log = logging.getLogger("devel")
        if alias:
            repos = [self._repo_from_alias(alias)]
        else:
            repos = [self._repo_from_alias(alias) for alias in self.config._parent._subsections]
        root = etree.fromstring("<status></status>")

        for repo in sorted(repos, key=attrgetter("alias")):
            # Find out if this repo is outwardly-responsible for
            # parsing -- we check against "False" as well since
            # LayeredConfig may lack typing info for this setting and
            # so interprets the value in the .ini file as a str, not a
            # bool...
            if 'parse' in repo.config and repo.config.parse in (False, "False"):
                continue

            # listing basefiles for the action "news" gives us
            # everyting that has a docentry file.
            basefiles = list(repo.store.list_basefiles_for("news"))
            if not basefiles:
                continue
            repo_el = etree.SubElement(root, "repo", {"alias": repo.alias})
            successcnt = warncnt = failcnt = removecnt = errcnt = 0
            durations = defaultdict(dict)
            for basefile in basefiles:
                # sys.stdout.write(".")
                # print("%s/%s" % (repo.alias, basefile))
                entrypath = repo.store.documententry_path(basefile)
                if not os.path.exists(entrypath):
                    log.warning("%s/%s: file %s doesn't exist" % (repo.alias, basefile, entrypath))
                    errcnt += 1
                    continue
                elif os.path.getsize(entrypath) == 0:
                    log.warning("%s/%s: file %s is 0 bytes" % (repo.alias, basefile, entrypath))
                    errcnt += 1
                    continue
                try:
                    entry = DocumentEntry(entrypath)
                except ValueError as e:
                    log.error("%s/%s: %s %s" % (repo.alias, basefile, e.__class__.__name__, e))
                    errcnt += 1
                    continue
                if not entry.status:  # an empty dict
                    log.warning("%s/%s: file %s has no status sub-dict" % (repo.alias, basefile, entrypath))
                    errcnt += 1
                    continue
                if "parse" in entry.status and "success" in entry.status["parse"] and entry.status["parse"]["success"] == "removed":
                    log.debug("%s/%s: document was removed in parse" % (repo.alias, basefile))
                    durations["parse"][basefile] = -1
                    continue
                doc_el = etree.SubElement(repo_el, "basefile",
                                          {"id": basefile})
                # FIXME: we should sort the entries in a reasonable way, eg
                # "download"/"parse"/"relate"/"generate"/any custom
                # action, probably through a custom key func
                for action in sorted(entry.status):
                    status = entry.status[action]
                    if not status:
                        log.warning("%s/%s: file %s has no status data for action %s" % (repo.alias, basefile, entrypath, action))
                        continue
                    if "success" in status and status["success"] == "removed":
                        # this special truthy value indicates that
                        # everything went as OK as it could, but the
                        # actual document doesn't exist (anymore) so we
                        # don't feature it in our overview.
                        #
                        # FIXME: Can this ever be reached, seemingly
                        # as we check for entry.status.parse.success
                        # == "removed" above, and no other action
                        # could produce a removed status?
                        durations[action][basefile] = -1
                        removecnt += 1
                        continue
                    durations[action][basefile] = status["duration"]
                    action_el = etree.SubElement(doc_el, "action",
                                                 {"id": action,
                                                  "success": str(status["success"]),
                                                  "duration": str(status["duration"]),
                                                  "date": str(status["date"])})
                    if status["success"]:
                        successcnt += 1
                    else:
                        failcnt += 1
                    if "warnings" in status:
                        warncnt += 1

                    # add additional (optional) text data if present
                    for optional in ("warnings", "error", "traceback"):
                        if optional in status:
                            opt_el = etree.SubElement(action_el, optional)
                            opt_el.text = status[optional]
            log.info("%s: %s processed, %s ok (%s w/ warnings), %s failed, %s removed. %s corrupted entries." % (repo.alias, len(basefiles), successcnt, warncnt, failcnt, removecnt, errcnt))
            with open(repo.store.path(".durations", "entries", ".json", storage_policy="file"), "w") as fp:
                json.dump(durations, fp, indent=4)
        conffile = os.path.abspath(
            os.sep.join([self.config.datadir, 'rsrc', 'resources.xml']))
        resourceloader = [x.resourceloader for x in repos if hasattr(x, 'resourceloader')][0]
        transformer = Transformer('XSLT', "xsl/statusreport.xsl", "xsl",
                                  resourceloader=resourceloader,
                                  config=conffile)
        xhtmltree = transformer.transform(root, depth=1)
        outfile = os.sep.join([self.config.datadir, 'status', 'status.html'])
        util.ensure_dir(outfile)
        with open(outfile, "wb") as fp:
            fp.write(etree.tostring(xhtmltree, encoding="utf-8", pretty_print=True))
        log.info("Wrote %s" % outfile)

    # FIXME: These are dummy implementations of methods and class
    # variables that manager.py expects all docrepos to have. We don't
    # want to have coverage counting these as missing lines, hence the
    # pragma: no cover comments.
    def __init__(self, config=None, **kwargs):
        self.store = DummyStore(None)
        if config is None:
            config = LayeredConfig(Defaults(kwargs))
        self.config = config
        self.requesthandler = DevelHandler(self)

    documentstore_class = DummyStore
    downloaded_suffix = ".html"
    storage_policy = "file"
    ns = {}
    resourceloader = ResourceLoader()
    
    @classmethod
    def get_default_options(cls):
        options = DocumentRepository.get_default_options()
        options.update({'username': str,
                        'password': str})
        return options

    def download(self):
        pass  # pragma: no cover

    def parse(self, basefile):
        pass  # pragma: no cover

    def relate(self, basefile):
        pass  # pragma: no cover

    def generate(self, basefile):
        pass  # pragma: no cover

    def transformlinks(self, basefile):
        pass  # pragma: no cover

    def toc(self, otherrepos):
        pass  # pragma: no cover

    def news(self, otherrepos):
        pass  # pragma: no cover

    def tabs(self):
        return []

    def footer(self):
        return []

    def frontpage_content(self, primary=False):
        return ("""<?xml version='1.0' encoding='utf-8'?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>site</title>
</head>
<body>
<div>
<h2>Welcome to ferenda</h2>
<p>Add a few document repositories and have fun!</p>
<p><a href="/devel/">Dashboard</a></p>
</div>
</body>
</html>
""")

    def get_url_transform_func(self, **transformargs):
        return lambda x: x
    
    def facets(self):
        return []

    def basefile_from_uri(self, uri):
        return None

    @classmethod
    def setup(cls, action, config, *args, **kwargs):
        pass  # pragma: no cover

    @classmethod
    def teardown(cls, action, config, *args, **kwargs):
        pass  # pragma: no cover
