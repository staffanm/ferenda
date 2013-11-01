# -*- coding: utf-8 -*-
from __future__ import unicode_literals

class Test(object):
    store = DocumentStore(datadir="data/base")

    def do(self, basefile):
        util.ensure_dir(self.store.downloaded_path(basefile))
# begin path
        path = self.store.downloaded_path(basefile)
        with open(path, mode="wb") as fp:
            fp.write(b"...")
# end path

# begin open
        with self.store.open_downloaded(path, mode="wb") as fp:
            fp.write(b"...")
# end open
        return True
t = Test()
return_value = t.do("it")
