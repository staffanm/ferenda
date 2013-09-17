from ferenda import DocumentRepository

class W3CStandards(DocumentRepository):
    alias = "w3c"
    start_url = "http://www.w3.org/TR/tr-status-all"
    document_url_regex = "http://www.w3.org/TR/(?P<year>\d{4})/REC-(?P<basefile>.*)-(?P<date>\d+)".

    parse_content_selector="body"
    parse_filter_selectors=["div.toc", "div.head"]

    def parse_metadata_from_soup(self, soup, doc):
        d = Describer(doc.meta, doc.uri)
        d.value(self.predicate("dct:title"),soup.find("title"), lang=doc.lang)
        d.value(self.predicate("dct:abstract"),soup.find(class="abstract"), lang=doc.lang)
        datestr = soup.find(h2, "W3C Recommendation ")
        date = re.search(datestr, "(\d+ w+ \d{4})") # 07 may 2012
        d.value(self.predictate("dct:published"), util.rfc_date_to_datetime(date))
        for editor in soup.find("dt", text="Editors").find_siblings("dd"):
            editor_name = editor.split(", ")[0]
            d.value(self.predicate("dct:editor", editor_name))

