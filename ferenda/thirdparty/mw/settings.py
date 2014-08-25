# Copyright 2013 semantics GmbH
# Written by Marcus Brinkmann <m.brinkmann@semantics.de>

from __future__ import print_function, absolute_import, division

import re

MSGCAT = {
    "toc": {"en": "Contents",
            "de": "Inhaltsverzeichnis"},
    "missing" : {"en": "page does not exist",
                 "de": "Seite nicht vorhanden"}
}


class AttrDict(dict):
    def __init__(self, *args, **kwargs):
        super(AttrDict, self).__init__(*args, **kwargs)
        self.__dict__ = self


class Namespace(AttrDict):
    def canonical_name(self, lang):
        return self.name.get(lang, None)

    def __repr__(self):
        return "Namespace<" + self.name.get("en", "(Main)") + ">"


default_namespaces = [
    {"prefix": "",
     "ident": 0,
     "name": {}},

    {"prefix": "talk",
     "ident": 1,
     "name": {"en": "Talk",
              "de": "Diskussion"}},

    {"prefix": "user",
     "ident": 2,
     "name": {"en": "User",
              "de": "Benutzer"}},

    {"prefix": "project talk",
     "ident": 3,
     "name": {"en": "User talk",
              "de": "Benutzer Diskussion"}},

    {"prefix": "project",
     "ident": 4,
     "name": {"en": "Project",
              "de": "Wikipedia"}},

    {"prefix": "project talk",
     "ident": 5,
     "name": {"en": "Project talk",
              "de": "Wikipedia Diskussion"}},

    {"prefix": "template",
     "ident": 10,
     "name": {"en": "Template",
              "de": "Vorlage"}},

    {"prefix": "template talk",
     "ident": 11,
     "name": {"en": "Template talk",
              "de": "Vorlage Diskussion"}},

    {"prefix": "help",
     "ident": 12,
     "name": {"en": "Help",
              "de": "Hilfe"}},

    {"prefix": "help talk",
     "ident": 13,
     "name": {"en": "Help talk",
              "de": "Hilfe Diskussion"}}
]


class Namespaces(object):
    def __init__(self, namespaces, lang="en"):
        self.lang = lang
        self.namespaces = namespaces
        self._refresh()

    def _refresh(self):
        namespaces = self.namespaces
        self._by_id = dict([(ns.ident, ns) for ns in namespaces])
        self._by_name = dict([(ns.prefix, ns) for ns in namespaces])
        lang = self.lang
        if lang != "en":
            self._by_loc_name = dict([(ns.name[lang].lower(), ns)
                                      for ns in namespaces if lang in ns.name])

    def find(self, namespace, allow_ids=True):
        ns = None
        if allow_ids is True:
            ns = self._by_id.get(namespace, None)
        if ns is None:
            ns = self._by_name.get(namespace, None)
        if ns is None:
            lang = self.lang
            if lang != "en":
                ns = self._by_loc_name.get(namespace, None)
        return ns

    def _remove(self, namespace):
        _namespace = self.find(namespace)
        if ns is not None:
            self.namespace = [ns for ns in self.namespace if ns != _namespace]

    def remove(self, namespace):
        self._remove(namespace.ident)
        self.update()

    def add(self, namespace):
        self._remove(namespace.ident)
        self.namespaces.append(namespace)
        self.refresh()

    def canonical_name(self, namespace):
        return namespace.canonical_name(self.lang)


whitespace_re = re.compile("\s+")


class Settings(object):
    def __init__(self, lang="en"):
        self.language = lang
        self.capital_links = True
        self.namespaces = Namespaces([Namespace(ns)
                                      for ns in default_namespaces], lang=lang)
        self.msgcat = MSGCAT

        # wgMaxTocLevel.  This is the maximum heading level that is
        # included in the TOC, assuming that the first heading is h2
        # (h1 is reserved for the page title) and no heading level is
        # skipped.  In other words, a max_toc_level of 3 means the
        # first two heading levels are included in the TOC.
        self.max_toc_level = 999

    def canonical_page_name(self, name, default_namespace=""):
        """Return the namespace (or None) and the canonical page name."""
        namespace = None

        name = name.replace("_", " ")
        name = name.strip()
        whitespace_re.subn(" ", name)

        colpos = name.find(":")
        if colpos >= 0:
            ns = name[:colpos]
            ns = ns.lower().strip()
            ns = self.namespaces.find(ns, allow_ids=False)
            if ns is not None:
                # namespace = self.namespaces.canonical_name(ns)
                namespace = ns
                name = name[colpos+1:]
                name = name.strip()

        if namespace is None:
            if not isinstance(default_namespace, Namespace):
                default_namespace = self.namespaces.find(default_namespace.lower(), allow_ids=False)
            namespace = default_namespace

        if self.capital_links:
            name = name[:1].upper() + name[1:]
        return namespace, name

    def expand_page_name(self, namespace, pagename):
        if namespace.prefix == "":
            return pagename
        ns = self.namespaces.canonical_name(namespace)
        return ns + ":" + pagename

    def get_msg(self, msg):
        return self.msgcat[msg][self.language]

    def test_page_exists(self, name):
        """Name can be a (namespace, name) tuple, too."""
        # By default, don't show any red links (links to missing
        # pages).
        return True

    def make_url(self, name, **kwargs):
        """Create an URL for page NAME (opt. namespace, name tuple).
        KWARGS are GET parameters."""

        if type(name) == tuple:
            name = self.expand_page_name(name[0], name[1])
        # FIXME: Escape?
        name = name.replace(" ", "_")

        if len(kwargs) == 0:
            url = "/wiki/" + name
        else:
            url = "/index.php?title=" + name
            # We need a stable order for testing.
            args = ["action", "section", "redlink"]
            for arg in args:
                if arg in kwargs:
                    url = url + "&" + arg + "=" + kwargs[arg]
        return url
