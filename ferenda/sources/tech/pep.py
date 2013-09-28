# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os

from ferenda import DocumentRepository
from ferenda import util


class PEP(DocumentRepository):
    alias = "pep"
    start_url = "http://hg.python.org/peps"
    document_url_template = "http://hg.python.org/peps/file/tip/pep-%(basefile)s.txt"

    def download(self):
        hg_clone_path = os.sep.join(self.config.datadir, self.alias, 'clone')
        if os.path.exists(hg_clone_path):
            self.log.debug("Pulling latest changes")
            util.runcmd("hg pull", cwd=hg_clone_path)
            self.log.debug("Updating local clone")
            util.runcmd("hg update", cwd=hg_clone_path)
        else:
            hg_clone_parent = os.sep.join(self.config.datadir, self.alias)
            util.runcmd("hg clone %s clone" % self.start_url,
                        cwd=hg_clone_parent)
        new_last_rev = None
        cmd = "LANGUAGE=C hg log -v"
        util.runcmd(cmd)
        for rev in "LANGUAGE=C hg log -v":
            if not new_last_rev:
                new_last_rev = rev.id
            if rev > self.config.last_rev:
                for f in rev.files:  # rev.files only contain proper pep files
                    "hg cat -r %s > downloaded/%s-r%s.txt" % (
                        f, self.store.downloaded_path(f), rev.id)
            else:
                self.config.last_rev = new_last_rev
                break
