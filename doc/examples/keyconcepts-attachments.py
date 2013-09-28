# -*- coding: utf-8 -*-
from __future__ import unicode_literals
# begin part-1
class TestDocrepo(DocumentRepository):

    storage_policy = "dir"
    
    def download_single(self, basefile):
        mainurl = self.document_url_template % {'basefile': basefile}
        self.download_if_needed(basefile, mainurl)
        with self.store.open_downloaded(basefile) as fp:
            soup = BeautifulSoup(fp.read())
        for img in soup.find_all("img"):
            imgurl = urljoin(mainurl, img["src"])
            resp = requests.get(imgurl)
# end part-1
            resp.content = b""  # this is cheating, but excluded from doc
# begin part-2
            # open eg. data/foo/downloaded/bar/hello.jpg for writing
            with self.store.open_downloaded(basefile,
                                            attachment=img["src"],
                                            mode="wb") as fp:
                fp.write(resp.content)
# end part-2
    
    def download_if_needed(self, basefile, mainurl):
        with self.store.open_downloaded(basefile, "w") as fp:
            fp.write("""<html>
            <body>
            <img src="hello.jpg"/>
            </body>
            </html>""")
            return True
    
d = TestDocrepo()
d.download_single("hello")
return_value = True
