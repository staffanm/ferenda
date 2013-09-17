# begin basic-properties
from ferenda import DocumentRepository

class W3CStandards(DocumentRepository):
    alias = "w3c"
    start_url = "http://www.w3.org/TR/tr-status-all"
    document_url_regex = "http://www.w3.org/TR/(?P<year>\d{4})/REC-(?P<basefile>.*)-(?P<date>\d+)"
# end basic-properties

# begin parse-properties
    parse_content_selector="body"
    parse_filter_selectors=["div.toc", "div.head"]
# end parse-properties

# begin metadata
    def parse_metadata_from_soup(self, soup, doc):
        from rdflib import Namespace
        from ferenda import Describer
        from ferenda import util
        import re
        DCT = Namespace("http://purl.org/dc/terms/")
        d = Describer(doc.meta, doc.uri)
        d.value(DCT.title, soup.find("title"), lang=doc.lang)
        d.value(DCT.abstract, soup.find(True, "abstract"), lang=doc.lang)
        datestr = soup.find("h2", "W3C Recommendation ").text
        date = re.search(r"(\d+ w+ \d{4})", datestr) # eg "07 may 2012"
        d.value(DCT.issued, util.rfc_date_to_datetime(date))
        for editor in soup.find("dt", text="Editors").find_siblings("dd"):
            editor_name = editor.split(", ")[0]
            d.value(DCT.editor, editor_name)
# end metadata
