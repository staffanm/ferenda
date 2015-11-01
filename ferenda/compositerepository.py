# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import time
from collections import defaultdict

from ferenda import DocumentRepository, DocumentStore
from ferenda import util, errors
from ferenda.compat import OrderedDict

class CompositeStore(DocumentStore):

    """Custom store for CompositeRepository objects."""

    def __init__(self, datadir, downloaded_suffix=".html",
                 storage_policy="file",
                 docrepo_instances=None):
        self.datadir = datadir  # docrepo.datadir + docrepo.alias
        self.downloaded_suffix = downloaded_suffix
        self.storage_policy = storage_policy
        if not docrepo_instances:
            docrepo_instances = OrderedDict()
        self.docrepo_instances = docrepo_instances
        self.basefiles = defaultdict(set)

    def list_basefiles_for(self, action, basedir=None):
        if not basedir:
            basedir = self.datadir
        if action == "parse":
            documents = set()
            for cls, inst in self.docrepo_instances.items():
                for basefile in inst.store.list_basefiles_for("parse"):
                    self.basefiles[cls].add(basefile)
                    if basefile not in documents:
                        documents.add(basefile)
                        yield basefile
        else:
            for basefile in super(CompositeStore,
                                  self).list_basefiles_for(action):
                yield basefile


class CompositeRepository(DocumentRepository):

    """Acts as a proxy for a list of sub-repositories.

    Calls the download() method for each of the included
    subrepos. Parse calls each subrepos parse() method in order until
    one succeeds, unless config.failfast is True. In that case any
    errors from the first subrepo is re-raised.

    """

    subrepos = ()  # list of classes

    """List of respository classes to use."""
    documentstore_class = CompositeStore
    extrabases = ()
    """List of mixin classes to add to each subrepo class."""

    def get_instance(self, instanceclass):
        if instanceclass not in self._instances:
            if hasattr(self, '_config'):
                config = self.config
            else:
                config = None

            # FIXME: this instance will be using a default
            # ResourceLoader, eg if a subrepo is at foo/bar.py, only
            # foo/bar/res will we in that resourceloaders path. This
            # causes problems, primarily if our CompositeRepository is
            # subclassed to somewhere else, eg subclass/bar.py -- we
            # might want to use resources at subclass/res instead.
            inst = instanceclass(config)
            # if we don't have a config object yet, the created
            # instance is just temporary -- don't save it
            if hasattr(self, '_config'):
                self._instances[instanceclass] = inst
            return inst
        else:
            return self._instances[instanceclass]

    def __init__(self, config=None, **kwargs):
        self._instances = OrderedDict()
        # after this, self.config WILL be set (regardless of whether a
        # config object was provided or not
        super(CompositeRepository, self).__init__(config, **kwargs)

        newsubrepos = []
        for c in self.subrepos:  # populate self._instances
            if self.extrabases:
                bases = [x for x in self.extrabases if x not in c.__bases__]
                bases.extend(c.__bases__)
                c = type(c.__name__, tuple(bases), dict(c.__dict__))
                newsubrepos.append(c)
            if self.loadpath:
                c.loadpath = self.loadpath
            self.get_instance(c)
        if newsubrepos:
            self.subrepos = newsubrepos
        cls = self.documentstore_class

        self.store = cls(self.config.datadir + os.sep + self.alias,
                         downloaded_suffix=self.downloaded_suffix,
                         storage_policy=self.storage_policy,
                         docrepo_instances=self._instances)


    @classmethod
    def get_default_options(cls):
        # 1. Get options from superclass (NB: according to MRO...)
        opts = super(CompositeRepository, cls).get_default_options()
        # 2. Add extra options that ONLY exists in subrepos
        for c in cls.subrepos:
            for k, v in c.get_default_options().items():
                if k not in opts:
                    opts[k] = v
        # 3. add the extra 'failfast' option
        opts['failfast'] = False
        return opts

    def download(self, basefile=None):
        for c in self.subrepos:
            inst = self.get_instance(c)
            # make sure that our store has access to our now
            # initialized subrepo objects
            if c not in self.store.docrepo_instances:
                self.store.docrepo_instances[c] = inst
            try:
                ret = inst.download(basefile)
            except Exception as e:  # be resilient
                self.log.error("download for c failed: %s" % e)
                ret = False
            if basefile and ret:
                # we got the doc we want, we're done!
                return

    # NOTE: this impl should NOT use the @managedparsing decorator
    def parse(self, basefile):
        # first, check if we really need to parse. If any subrepo
        # returns that parseneeded is false and we have parsed file in
        # the mainrepo, then we're done. This is mainly to avoid the
        # log message below (to be in line with expected repo
        # behaviour of not logging anything at severity INFO if no real
        # work was done), it does not noticably affect performance
        force = (self.config.force is True or
                 self.config.parseforce is True)
        if not force:
            for c in self.subrepos:
                inst = self.get_instance(c)
                needed = inst.parseneeded(basefile)
                if not needed and os.path.exists(self.store.parsed_path(basefile)):
                    self.log.debug("%s: Skipped" % basefile)
                    return True  # signals everything OK

        start = time.time()
        ret = False

        # We only try those subrepos that have the possibility of
        # parsing basefile, ie they have the correct downloaded
        # file. CompositeStore stores a set of existing downloaded
        # files when its list_basefiles_for method is called, so we
        # make sure to do that if needed.
        if not self.store.basefiles:
            x = list(self.store.list_basefiles_for("parse"))

        for c in self.subrepos:
            if basefile in self.store.basefiles[c]:
                inst = self.get_instance(c)
                try:
                    # each parse method should be smart about whether
                    # to re-parse or not (i.e. use the @managedparsing
                    # decorator).
                    ret = inst.parse(basefile)
                # Any error thrown (errors.ParseError or something
                # else) means we try next subrepo -- unless we want to
                # fail fast with a nice stacktrace during debugging.
                except Exception as e:
                    if self.config.failfast:
                        raise
                    else:
                        self.log.debug("%s: parse with %s failed: %s" %
                                       (basefile,
                                        inst.qualified_class_name(),
                                        str(e)))
                        ret = False
                if ret:
                    break
        if ret:
            self.copy_parsed(basefile, inst)
            self.log.info("%(basefile)s OK (%(elapsed).3f sec)",
                          {'basefile': basefile,
                           'elapsed': time.time() - start})
            return ret
        else:
            # subrepos should only contain those repos that actually
            # had a chance of parsing (basefile in
            # self.store.basefiles[c])
            subrepos_lbl = ", ".join([self.get_instance(x).qualified_class_name()
                                      for x in self.subrepos if basefile in self.store.basefiles[x]])
            raise errors.ParseError(
                "No instance of %s was able to parse %s" %
                (subrepos_lbl, basefile))

    def copy_parsed(self, basefile, instance):
        # If the distilled and parsed links are recent, assume that
        # all external resources are OK as well
        if (util.outfile_is_newer([instance.store.distilled_path(basefile)],
                                  self.store.distilled_path(basefile)) and
            util.outfile_is_newer([instance.store.parsed_path(basefile)],
                                  self.store.parsed_path(basefile))):
            self.log.debug("%s: Attachments are (likely) up-to-date" % basefile)
            return

        util.link_or_copy(instance.store.distilled_path(basefile),
                          self.store.distilled_path(basefile))

        util.link_or_copy(instance.store.parsed_path(basefile),
                          self.store.parsed_path(basefile))

        cnt = 0
        if instance.store.storage_policy == "dir":
            for attachment in instance.store.list_attachments(basefile, "parsed"):
                cnt += 1
                src = instance.store.parsed_path(basefile, attachment=attachment)
                target = self.store.parsed_path(basefile, attachment=attachment)
                util.link_or_copy(src, target)
            if cnt:
                self.log.debug("%s: Linked %s attachments from %s to %s" %
                               (basefile,
                                cnt,
                                os.path.dirname(instance.store.parsed_path(basefile)),
                                os.path.dirname(self.store.parsed_path(basefile))))
