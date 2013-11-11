# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from tempfile import mkdtemp
import os
import shutil
import re

import pkg_resources
from lxml import etree
from lxml.etree import XSLT

from ferenda import errors, util

# assumption: A transformer is initialized with a single template. If
# you want to use a different template, create a different
# transformer.


class Transformer(object):

    """Transforms parsed "pure content" documents into "browser-ready"
    HTML5 files with site branding and navigation, using a template of
    some kind.

    :param transformertype: The engine to be used for transforming. Right now only ``"XSLT"`` is supported.
    :type  transformertype: str
    :param template: The main template file.
    :type  template: str
    :param templatedirs: Directories that may contain supporting templates used by the main template.
    :type  templatedirs: str
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

    def __init__(self, transformertype,
                 template,
                 templatedirs,
                 documentroot=None,
                 config=None):
        cls = {'XSLT': XSLTTransform,
               'JINJA': JinjaTransform}[transformertype]
        self.t = cls(template, templatedirs)
        self.documentroot = documentroot
        self.config = config

    # transform() always operate on the native datastructure -- this might
    # be different depending on the transformer engine. For XSLT, which is
    # implemented through lxml, its in- and outdata are lxml trees
    #
    # If you want engine-indepent apis, use transform_stream or
    # transform_file instead
    #
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

        if parameters == None:
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
        if uritransform:
            self._transform_links(outdata.getroot(), uritransform)
        return outdata

    def _transform_links(self, tree, uritransform):
        for part in tree:
            # depth-first transformation seems the easiest
            self._transform_links(part, uritransform)
            if part.tag != "a":
                continue
            uri = part.get("href")
            if not uri:
                continue
            part.set("href", uritransform(uri))

    def transform_stream(self, instream, depth,
                         parameters=None, uritransform=None):
        """Accepts a file-like object, returns a file-like object."""
        return self.t.native_to_stream(
            self.transform(self.t.stream_to_native(instream),
                           depth,
                           parameters,
                           uritransform))

    def transform_file(self, infile, outfile,
                       parameters=None, uritransform=None):
        """Accepts two filenames, reads from *infile*, writes to *outfile*."""
        depth = self._depth(os.path.dirname(outfile),
                            self.documentroot+os.sep+"index.html")
        self.t.native_to_file(self.transform(self.t.file_to_native(infile),
                                             depth,
                                             parameters,
                                             uritransform),
                              outfile)

    def _depth(self, outfiledir, root):
        # NB: root must be a file in the root dir
        return os.path.relpath(root, outfiledir).count("..")


class TransformerEngine(object):

    def __init__(self, template, templatedirs):
        pass


class XSLTTransform(TransformerEngine):

    def __init__(self, template, templatedirs, **kwargs):
        self.format = True  # FIXME: make configurable
        self.templdir = self._setup_templates(template, templatedirs)
        worktemplate = self.templdir + os.sep + os.path.basename(template)
        assert os.path.exists(worktemplate)
        parser = etree.XMLParser(remove_blank_text=self.format)
        xsltree = etree.parse(worktemplate, parser)
        try:
            self._transformer = etree.XSLT(xsltree)
        except etree.XSLTParseError as e:
            raise errors.TransformError(str(e.error_log))

    # purpose: get all XSLT files (main and supporting) into one place
    #   (should support zipped eggs, even if setup.py don't)
    # template:     full path to actual template to be used
    # templatedirs: directory of supporting XSLT templates
    # returns:      directory name of the place where all files ended up
    def _setup_templates(self, template, templatedirs):
        workdir = mkdtemp()
        # copy everything to this temp dir
        for d in templatedirs:
            if pkg_resources.resource_isdir('ferenda', d):
                for f in pkg_resources.resource_listdir('ferenda', d):
                    fp = pkg_resources.resource_stream('ferenda', d + "/" + f)
                    dest = workdir + os.sep + f
                    with open(dest, "wb") as dest_fp:
                        dest_fp.write(fp.read())
            elif os.path.exists(d) and os.path.isdir(d):
                for f in os.listdir(d):
                    shutil.copy2(d + os.sep + f, workdir + os.sep + f)
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
            strparams['configurationfile'] = XSLT.strparam(config)
        for key, value in parameters.items():
            if key.endswith("file"):
                # relativize path of file relative to the XSL file
                # we'll be using. The mechanism could be clearer...
                value = os.path.relpath(value, self.templdir)
                if os.sep == "\\":
                    value = value.replace(os.sep, "/")
            strparams[key] = XSLT.strparam(value)
        try:
            return self._transformer(indata, **strparams)
        except etree.XSLTApplyError as e:
            raise errors.TransformError(str(e))
        if len(self._transformer.error_log) > 0:
            raise errors.TransformError(str(_transformer.error_log))

    # nativedata = lxml.etree
    def native_to_file(self, nativedata, outfile):
        res = self.html5_doctype_workaround(
            etree.tostring(nativedata, pretty_print=self.format))
        util.ensure_dir(outfile)
        with open(outfile, "wb") as fp:
            fp.write(res)

    @staticmethod
    def html5_doctype_workaround(indata):
        # FIXME: This is horrible
        if indata.startswith(b"<remove-this-tag>"):
            found = False
            endidx = -1
            while not found:
                if indata[endidx] == b"<" or indata[endidx] == 60:
                    found = True
                else:
                    endidx -= 1
            indata = b"<!DOCTYPE html>\n" + indata[17:endidx].strip()
        return indata

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
#                                   templatedirs=["res/xsl"],
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
