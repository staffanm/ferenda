from ferenda.sources import DocumentRepository
class RFCs(DocumentRepository):
    alias = "rfc"
    start_url = "http://www.ietf.org/download/rfc-index.txt"
    document_url_regex = "http://tools.ietf.org/rfc/rfc(?P<basefile>).txt"

    @recordlastdownload
    def download(self):
        self.log.debug("download: Start at %s" %  self.start_url)
        indextext = requests.get(self.start_url).text
        reader = TextReader(ustring=indextext)  # see TextReader class
        for p in reader.getiterator(reader.readparagraph):
            if re.match("^(\d{4}) ",p):
                if not "Not Issued." in p: # Skip RFC we know don't exist
                    basefile = str(int(p[:4]))  # eg. '0822' -> '822' 
                    self.download_single(basefile)
