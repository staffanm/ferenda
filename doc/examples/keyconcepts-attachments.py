# begin
    def download_single(self, basefile):
        mainurl = self.document_url_template % 
        self.download_if_needed(basefile, mainurl)
        with self.store.open_downloaded(basefile) as fp:
            soup = BeautifulSoup(fp.read())
        for img in soup.find_all("img"):
            imgurl = urljoin(mainurl, img["src"])
            resp = requests.get(imgurl)
            # open eg. data/foo/downloaded/bar/hello.jpg for writing
            with self.store.open(basefile, attachment=img["src"], mode="wb") as fp:
                fp.write(resp.content)
# end
