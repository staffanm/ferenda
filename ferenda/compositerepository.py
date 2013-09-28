# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os

from . import DocumentRepository, DocumentStore


class CompositeStore(DocumentStore):

    def __init__(self, datadir, downloaded_suffix=".html", storage_policy="file", docrepos=[]):
        self.datadir = datadir  # docrepo.datadir + docrepo.alias
        self.downloaded_suffix = downloaded_suffix
        self.storage_policy = storage_policy
        self.docrepos = docrepos

    def list_basefiles_for(self, action, basedir=None):
        if not basedir:
            basedir = self.datadir
        if action == "parse":
            documents = set()
            for inst in self.docrepos:
                for basefile in inst.store.list_basefiles_for("parse"):
                    if basefile not in documents:
                        documents.add(basefile)
                        yield basefile
        else:
            for basefile in inst.store.list_basefiles_for(action):
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
                                              docrepos=self._instances)

    def download(self):
        for c in self.subrepos:
            inst = self.get_instance(c, self.myoptions)
            inst.download()

    # NOTE: this impl should NOT use the @managedparsing decorator
    def parse(self, basefile):
        start = time()
        self.log.debug("%s: Starting", basefile)
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

    def copy_parsed(self, basefile, instance):
        # If the distilled and parsed links are recent, assume that
        # all external resources are OK as well
        if (util.outfile_is_newer([instance.distilled_path(basefile)],
                                  self.distilled_path(basefile)) and
            util.outfile_is_newer([instance.parsed_path(basefile)],
                                  self.parsed_path(basefile))):
            self.log.debug(
                "%s: External resources are (probably) up-to-date" % basefile)
            return

        cnt = 0
        for attachment in instance.store.list_attachments(doc.basefile, "parsed"):
            cnt += 1
            src = instance.store.parser_path(basename, attachment=attachment)
            target = self.store.parsed_path(basename, attachment=attachment)
            util.link_or_copy(src, target)

        util.link_or_copy(instance.distilled_path(basefile),
                          self.distilled_path(basefile))

        util.link_or_copy(instance.parsed_path(basefile),
                          self.parsed_path(basefile))

        if cnt:
            self.log.debug("%s: Linked %s external resources from %s to %s" %
                           (basefile,
                            cnt,
                            os.path.dirname(instance.parsed_path(basefile)),
                            os.path.dirname(self.parsed_path(basefile))))
