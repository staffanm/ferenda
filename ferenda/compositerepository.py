# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os

from layeredconfig import LayeredConfig

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
            for cls, inst in self.docrepo_instances.items():
                for basefile in inst.store.list_basefiles_for("parse"):
                    if basefile not in documents:
                        # print("Adding %s from instance %s" % (basefile, inst))
                        documents.add(basefile)
                        yield basefile
        else:
            for basefile in super(CompositeStore, self).list_basefiles_for(action):
                yield basefile


class CompositeRepository(DocumentRepository):
    subrepos = ()  # list of classes
    documentstore_class = CompositeStore

    def get_instance(self, instanceclass):
        if instanceclass not in self._instances:
            if hasattr(self, '_config'):
                config = self.config
            else:
                config = None
            inst = instanceclass(config)
            # if we don't have a config object yet, the created
            # instance is just temporary -- don't save it
            if hasattr(self, '_config'):
                self._instances[instanceclass] = inst
            return inst
        else:
            return self._instances[instanceclass]

    def __init__(self, config=None, **kwargs):
        self._instances = {}
        # after this, self.config WILL be set (regardless of whether a
        # config object was provided or not
        super(CompositeRepository, self).__init__(config, **kwargs)

        for c in self.subrepos: # populate self._instances
            self.get_instance(c)

        cls = self.documentstore_class
        self.store = cls(self.config.datadir + os.sep + self.alias,
                         downloaded_suffix=self.downloaded_suffix,
                         storage_policy=self.storage_policy,
                         docrepo_instances=self._instances)

        for c in self.subrepos: # populate self._instances
            self.get_instance(c)

        cls = self.documentstore_class
        self.store = cls(self.config.datadir + os.sep + self.alias,
                         downloaded_suffix=self.downloaded_suffix,
                         storage_policy=self.storage_policy,
                         docrepo_instances=self._instances)
        
    def get_default_options(self):
        # 1. Get options from superclass (NB: according to MRO...)
        opts = super(CompositeRepository, self).get_default_options()
        # 2. Add extra options that ONLY exists in subrepos
        for c in self.subrepos:
            for k, v in self.get_instance(c).get_default_options().items():
                if k not in opts:
                    opts[k] = v
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
        # behaviour of not logging anythin at severity INFO if no real
        # work was done), it does not noticably affect performance
        force = (self.config.force is True or
                 self.config.parseforce is True)
        if not force:
            for c in self.subrepos:
                inst = self.get_instance(c)
                needed = inst.parseneeded(basefile)
                if not needed and os.path.exists(self.store.parsed_path(basefile)):
                    self.log.debug("%s: Skipped" % basefile)
                    return True # signals everything OK

        with util.logtime(self.log.info, "%(basefile)s OK (%(elapsed).3f sec)",
                          {'basefile': basefile}):
            ret = False
            for c in self.subrepos:
                inst = self.get_instance(c)
                try:
                    # each parse method should be smart about whether
                    # to re-parse or not (i.e. use the @managedparsing
                    # decorator).
                    ret = inst.parse(basefile)

                except Exception as e: # Any error thrown (errors.ParseError or something else) means we try next subrepo
                    self.log.debug("%s: parse with %s failed: %s" % (basefile, inst.qualified_class_name(), str(e)))
                    ret = False
                if ret:
                    break
            if ret:
                self.copy_parsed(basefile, inst)
        if ret:
            return ret
        else:
            raise errors.ParseError("No instance of %r was able to parse %s" % (self.subrepos, basefile))


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
