# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os

from ferenda import DocumentRepository, DocumentStore
from ferenda import util, errors

class CompositeStore(DocumentStore):

    def __init__(self, datadir, downloaded_suffix=".html",
                 storage_policy="file",
                 docrepo_instances=None):
        self.datadir = datadir  # docrepo.datadir + docrepo.alias
        self.downloaded_suffix = downloaded_suffix
        self.storage_policy = storage_policy
        if not docrepo_instances:
            docrepo_instances = {}
        self.docrepo_instances = docrepo_instances

    def list_basefiles_for(self, action, basedir=None):
        if not basedir:
            basedir = self.datadir
        if action == "parse":
            documents = set()
            # assert self.docrepo_instances, "No docrepos are defined!"
            for cls, inst in self.docrepo_instances.items():
                for basefile in inst.store.list_basefiles_for("parse"):
                    if basefile not in documents:
                        documents.add(basefile)
                        yield basefile
        else:
            for basefile in super(CompositeStore, self).list_basefiles_for(action):
                yield basefile


class CompositeRepository(DocumentRepository):
    subrepos = ()  # list of classes
    documentstore_class = CompositeStore

    _instances = {}

    def get_instance(self, instanceclass, options={}):
        if not instanceclass in self._instances:
            inst = instanceclass(**options)
            inst.config = self.config  # FIXME: this'll override **options...
            self._instances[instanceclass] = inst
        return self._instances[instanceclass]

    def __init__(self, **kwargs):
        self.myoptions = kwargs
        super(CompositeRepository, self).__init__(**kwargs)
        # FIXME: At this point, self._instances is empty. And we can't
        # really populate it, because we need access to the config
        # object that manager._run_class will set after __init__
        # finishes... The best fix from this class POV would be to
        # have config be a (special) kwargs parameter, but that
        # violates the DocumentRepository API...
        self.store = self.documentstore_class(self.config.datadir + os.sep + self.alias,
                                              downloaded_suffix=self.downloaded_suffix,
                                              storage_policy=self.storage_policy,
                                              docrepo_instances=self._instances)

    def download(self):
        for c in self.subrepos:
            inst = self.get_instance(c, self.myoptions)
            # make sure that our store has access to our now
            # initialized subrepo objects
            if c not in self.store.docrepo_instances:
                self.store.docrepo_instances[c] = inst
            inst.download()

    # NOTE: this impl should NOT use the @managedparsing decorator
    def parse(self, basefile):
        with util.logtime(self.log.info, "%(basefile)s OK (%(elapsed).3f sec)",
                          {'basefile': basefile}):
            ret = False
            for c in self.subrepos:
                inst = self.get_instance(c, self.myoptions)
                try:
                    # each parse method should be smart about whether to re-parse
                    # or not (i.e. use the @managedparsing decorator)
                    ret = inst.parse(basefile)
                except errors.ParseError:  # or others
                    ret = False
                if ret:
                    break
            if ret:
                self.copy_parsed(basefile, inst)
        return ret

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
