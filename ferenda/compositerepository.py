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

    def get_instance(self, instanceclass, options={}):
        if not instanceclass in self._instances:
            if options:
                inst = instanceclass(**options)
            else:
                inst = instanceclass()
                inst.config = self.config 
            self._instances[instanceclass] = inst
        return self._instances[instanceclass]

    def __init__(self, **kwargs):
        self._instances = {}
        self.myoptions = kwargs
        super(CompositeRepository, self).__init__(**kwargs)
        # At this point, self._instances is empty. And we can't really
        # populate it at this time, because we need access to the
        # config object that manager._run_class will set after
        # __init__ finishes, in order to properly initialize all our
        # subrepos.

        # when using API, we won't need an externally-provided config
        # object, as whatever is passed in as **kwargs will populate
        # an internallly-constructed config object. So if that's the
        # case, let's go ahead and create instances and make a store
        # right now.
        if self.myoptions:
            for c in self.subrepos: # populate self._instances
                self.get_instance(c, self.myoptions)
                
            cls = self.documentstore_class
            self.store = cls(self.config.datadir + os.sep + self.alias,
                             downloaded_suffix=self.downloaded_suffix,
                             storage_policy=self.storage_policy,
                             docrepo_instances=self._instances)
        else:
            # no **kwargs were provided, delay the creation of
            # self._instances (and self.store) until we have a proper
            # config object (in the config.setter decorated stuff
            pass
            

    @property
    def config(self):
        return self._config

    @config.setter
    def config(self, config):
        self._config = config

        for c in self.subrepos: # populate self._instances
            self.get_instance(c)

        cls = self.documentstore_class
        self.store = cls(self.config.datadir + os.sep + self.alias,
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
                inst = self.get_instance(c, self.myoptions)
                needed = inst.parseneeded(basefile)
                if not needed and os.path.exists(self.store.parsed_path(basefile)):
                    self.log.debug("%s: Skipped" % basefile)
                    return True # signals everything OK

        with util.logtime(self.log.info, "%(basefile)s OK (%(elapsed).3f sec)",
                          {'basefile': basefile}):
            ret = False
            for c in self.subrepos:
                inst = self.get_instance(c, self.myoptions)
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
