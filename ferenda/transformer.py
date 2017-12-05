# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

from tempfile import mkdtemp, NamedTemporaryFile
import os
import shutil
import re
from io import BytesIO

from lxml import etree
from lxml.etree import XSLT

from ferenda import errors, util
from ferenda import ResourceLoader

# assumption: A transformer is initialized with a single template. If
# you want to use a different template, create a different
# transformer.

class Transformer(object):

    """Transforms parsed "pure content" documents into "browser-ready"
    HTML5 files with site branding and navigation, using a template of
    some kind.

    :param transformertype: The engine to be used for transforming. Right now only ``"XSLT"`` is supported.
    :type  transformertype: str
    :param resourceloader: The :py:class:`~ferenda.ResourceLoader` instance used to find template files.
    :type template: ferenda.ResourceLoader
    :param template: The name of the main template file.
    :type  template: str
    :param templatedir: Directory for supporting templates to the main template.
    :type  templatedir: str
    :param documentroot: The base directory for all generated files -- used to make relative references to CSS/JS files correct.
    :type  documentroot: str
    :param config: Any configuration information used by the
                   transforming engine. Can be a path to a config
                   file, a python data structure, or anything else
                   compatible with the engine selected by
                   ``transformertype``.

    .. note::

       An initialized Transformer object only transforms using the
       template file provided at initialization. If you need to use
       another template file, create another Transformer object.

    """

    def __init__(self,
                 transformertype,
                 template,
                 templatedir,  # within the resourceloader
                 resourceloader=None,
                 documentroot=None,
                 config=None):
        cls = {'XSLT': XSLTTransform,
               'JINJA': JinjaTransform}[transformertype]
        if not resourceloader:
            resourceloader = ResourceLoader()
        self.resourceloader = resourceloader
        self.t = cls(template, templatedir, self.resourceloader)
        self.documentroot = documentroot
        self.config = config

    # valid parameters
    # - annotationfile: intermediate/basefile.grit.xml
    def transform(self, indata, depth, parameters=None, uritransform=None):
        """Perform the transformation. This method always operates on the
        "native" datastructure -- this might be different depending on
        the transformer engine. For XSLT, which is implemented through
        lxml, its in- and outdata are lxml trees

        If you need an engine-indepent API, use
        :meth:`~ferenda.Transformer.transform_stream` or
        :meth:`~ferenda.Transformer.transform_file` instead

        :param indata: The document to be transformed
        :param depth: The directory nesting level, compared to ``documentroot``
        :type  depth: int
        :param parameters: Any parameters that should be provided to the
                           template
        :type  parameters: dict
        :param uritransform: A function, when called with an URI,
                             returns a transformed URI/URL (such as
                             the relative path to a static file) --
                             used when transforming to files used for
                             static offline use.
        :type  uritransform: callable
        :returns: The transformed document

        """
        if parameters is None:
            parameters = {}

        # the provided configuration (might be a file or a python dict
        # or anything else, depending on the transformer engine) will
        # contain lists of JS/CSS resources. In order to make it
        # possible to use relative links to these (needed for offline
        # static HTML files), we first do a transformer
        # engine-specific adaption of the configuration depending on
        # the directory depth level of the outfile (as provided
        # through the depth parameter), then we provide this adapted
        # configuration to the transform call
        if self.config:
            adapted_config = self.t.getconfig(self.config, depth)
        else:
            adapted_config = None
        outdata = self.t.transform(indata, adapted_config, parameters)
        if self.t.reparse:
            outdata = etree.parse(BytesIO(etree.tostring(outdata)))
        if uritransform:
            self._transform_links(outdata.getroot(), uritransform)
        return outdata

    def _transform_links(self, tree, uritransform):
        for part in tree:
            # depth-first transformation seems the easiest
            self._transform_links(part, uritransform)
            if part.tag not in ("a", "{http://www.w3.org/1999/xhtml}a",
                                "link", "{http://www.w3.org/1999/xhtml}link",
                                "img", "{http://www.w3.org/1999/xhtml}img"):
                continue
            for attr in ("href", "src", "data-src"):
                uri = part.get(attr)
                if not uri:
                    continue
                newuri = uritransform(uri)
                if newuri is False:
                    # add the "invalid" class to the element if the
                    # URI doesn't correspond to a document we have
                    existingclasses = list(filter(None,part.get("class", "").split(" ")))
                    part.set("class", " ".join(existingclasses + ["invalid-link"]))
                else:
                    part.set(attr, newuri)
                
                break

    def transform_stream(self, instream, depth,
                         parameters=None, uritransform=None):
        """Accepts a file-like object, returns a file-like object."""
        return self.t.native_to_stream(
            self.transform(self.t.stream_to_native(instream),
                           depth,
                           parameters,
                           uritransform))

    def transform_file(self, infile, outfile,
                       parameters=None, uritransform=None, depth=None):
        """Accepts two filenames, reads from *infile*, writes to *outfile*."""
        if depth is None:
            depth = self._depth(os.path.dirname(outfile),
                            self.documentroot + os.sep + "index.html")
        helpful = os.environ.get('FERENDA_TRANSFORMDEBUG', False)
        if helpful:
            import logging
            log = logging.getLogger("ferenda.transformer")
            if self.config:
                xslfile = self.resourceloader.filename(self.t.orig_template)
                p = {}
                if parameters:
                    p.update(parameters.copy())
                for key, value in p.items():
                    if key.endswith("file"):
                        p[key] = os.path.relpath(value,
                                                 os.path.dirname(xslfile))
                p['configurationfile'] = self.t.getconfig(self.config, depth)
                log.debug("Equiv: xsltproc --nonet %s %s %s > %s" %
                          (" ".join(['--stringparam %s "%s"' % (x, p[x]) for x in p]),
                           os.path.relpath(xslfile,
                                           os.getcwd()),
                           infile, outfile))
            else:
                log.warning(
                    "self.config not set, cannot construct equivalent xsltproc command line")

        self.t.native_to_file(self.transform(self.t.file_to_native(infile),
                                             depth,
                                             parameters,
                                             uritransform),
                              outfile)

    def _depth(self, outfiledir, root):
        # NB: root must be a file in the root dir
        return os.path.relpath(root, outfiledir).count("..")


class TransformerEngine(object):

    def __init__(self, template, templatedir):
        pass


class XSLTTransform(TransformerEngine):

    def __init__(self, template, templatedir, resourceloader, **kwargs):
        self.orig_template = template
        self.orig_templatedir = templatedir  # ?
        self.format = True  # FIXME: make configurable
        self.resourceloader = resourceloader
        self.templdir = self._setup_templates(template, templatedir)
        # worktemplate = self.templdir + os.sep + template
        worktemplate = self.templdir + os.sep + os.path.basename(template)
        assert os.path.exists(worktemplate)
        parser = etree.XMLParser(remove_blank_text=self.format)
        xsltree = etree.parse(worktemplate, parser)

        # if the XSLT transform contained <xsl:value-of
        # disable-output-escaping="yes"/> nodes, the result of such
        # transforms will not be proper lxml elements but rather a
        # .tail string on the previous element. That's bad because
        # uritransform can't get at it. Therefore, if needed, we
        # re-parse it.
        self.reparse = xsltree.find(".//*[@disable-output-escaping='yes']") is not None
        try:
            self._transformer = etree.XSLT(xsltree)
        except etree.XSLTParseError as e:
            raise errors.TransformError(str(e.error_log))

    def __del__(self):
        if os.path.exists(self.templdir):
            # this had better be a tempdir!
            shutil.rmtree(self.templdir)

    # purpose: get all XSLT files (main and supporting) into one place
    #   (should support zipped eggs, even if setup.py don't)
    # template:     full path to actual template to be used
    # templatedir: directory of supporting XSLT templates
    # returns:      directory name of the place where all files ended up
    def _setup_templates(self, template, templatedir):
        workdir = mkdtemp()
        self.resourceloader.extractdir(templatedir, workdir, (".xsl", ".xslt"))
        if os.path.basename(template) not in os.listdir(workdir):
            shutil.copy2(template, workdir)
        return workdir

    # getconfig may return different data depending on engine -- in
    # this case it creates a xml file and returns the path for it
    def getconfig(self, configfile, depth):
        filename = configfile
        if depth != 0:
            (base, ext) = os.path.splitext(configfile)
            filename = "%(base)s-depth-%(depth)d%(ext)s" % locals()
            if not util.outfile_is_newer([configfile],  filename):
                tree = etree.parse(configfile)
                # adjust the relevant link attribute for some nodes
                for xpath, attrib in (("stylesheets/link", "href"),
                                      ("javascripts/script", "src"),
                                      (".//img", "src")):
                    for node in tree.findall(xpath):
                        # don't adjust absolute links
                        if not (re.match("(https?://|/)", node.get(attrib))):
                            node.set(attrib, "../" * depth + node.get(attrib))
                tree.write(filename)
        return filename

    def transform(self, indata, config=None, parameters={}):
        
        strparams = {}
        if config:
            # paths to be used with the document() function
            # must use unix path separators
            if os.sep == "\\":
                config = config.replace(os.sep, "/")
            # print("Tranform: Using config %s. Contents:" % config)
            # print(util.readfile(config))
            config_fullpath = os.path.abspath(config)
            strparams['configurationfile'] = XSLT.strparam(config_fullpath)
        removefiles = []
        for key, value in parameters.items():
            if key.endswith("file") and value:
                if all(ord(c) < 128 and c != " " for c in value):
                    # IF the file name contains ONLY ascii chars and
                    # no spaces, we can use it directly. However, we
                    # need to relativize path of file relative to the
                    # XSL file we'll be using. The mechanism could be
                    # clearer...
                    value = os.path.relpath(value, self.templdir)
                else:
                    # If the filename contains non-ascii characters or
                    # space, any attempt to eg
                    # "document($annotationfile)" in the XSLT document
                    # will silently fail. Seriously, fuck lxml's error
                    # handling. In this case, copy it to a temp file
                    # (in the temporary templdir, with ascii filename)
                    # and use that.
                    contents = util.readfile(value)
                    value = os.path.basename(value)
                    value = "".join(c for c in value if ord(c) < 128 and c != " ")
                    removefiles.append(self.templdir+os.sep+value)
                    util.writefile(self.templdir+os.sep+value, contents)
                if os.sep == "\\":
                    value = value.replace(os.sep, "/")
            strparams[key] = XSLT.strparam(value)
        try:
            return self._transformer(indata, **strparams)
        except etree.XSLTApplyError as e:
            raise errors.TransformError(str(e))
        finally:
            for f in removefiles:
                util.robust_remove(f)
        # FIXME: This can never be reached, if _transformer() does not
        # raise an error, the above returns immediately.
        if len(self._transformer.error_log) > 0:
            raise errors.TransformError(str(_transformer.error_log))

    # nativedata = lxml.etree
    def native_to_file(self, nativedata, outfile):
        res = etree.tostring(nativedata, pretty_print=self.format, encoding="utf-8")
        util.ensure_dir(outfile)
        with open(outfile, "wb") as fp:
            fp.write(res)

    def file_to_native(self, infile):
        return etree.parse(infile)
        # FIXME: hook in the transform_links step somehow?


class JinjaTransform(TransformerEngine):
    pass


# client code
#
# doc.body = elements.Body()
# for r in res:
#     doc.body.append(html.Div(
#         [html.H2([elements.Link(r['title'], uri=r['uri'])]),
#          r['text']], **{'class':'hit'}))
# pages = [html.P(["Results %(firstresult)s-%(lastresult)s of %(totalresults)s" %          pager])]
# for pagenum in range(pager['pagecount']):
#     if pagenum + 1 == pager['pagenum']:
#         pages.append(html.Span([str(pagenum+1)],**{'class':'page'}))
#     else:
#         querystring['p'] = str(pagenum+1)
#         url = environ['PATH_INFO'] + "?" + urlencode(querystring)
#         pages.append(html.A([str(pagenum+1)],**{'class':'page',
#                                                 'href':url}))
# doc.body.append(html.Div(pages, **{'class':'pager'}))
#
# transformer = TemplateTransformer(transformertype="XSLT",
#                                   template="res/xsl/generic.xsl",
#                                   templatedir=["res/xsl"],
#                                   documentroot="/var/www/site")
#
# newtree = transformer.transform_tree(doc.body.as_xhtml(),
#                                      reldepth=1)
# fp.write(etree.tostring(newtree, pretty_print=True))
#
# -- or --
#
#
# util.writefile("indata.xhtml", doc.body.as_xhtml().serialize())
# transformer.transform("indata.xhtml", "/var/www/site/my/own/file.html")
#
# references to root resources in file.html are now on the form
# "../../css/main.css", since file.html is 2 levels deep compared to
# documentroot.
#
