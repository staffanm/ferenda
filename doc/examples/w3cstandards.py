import os
import requests
import lxml.html
import re
os.environ['FERENDA_DOWNLOADMAX'] = "3"
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

    def remote_url(self, basefile):
        # we have to download starturl to find the appropriate
        # remote_url (basefile isn't enough)
        resp = requests.get(self.start_url)
        tree = lxml.html.document_fromstring(resp.text)
        tree.make_links_absolute(self.start_url, resolve_base_href=True)
        for element, attribute, link, pos in tree.iterlinks():
            m = re.match(self.document_url_regex, link)
            if m and m.group("basefile") == basefile:
                return link
        return None
                
# begin metadata
    def parse_metadata_from_soup(self, soup, doc):
        from rdflib import Namespace
        from ferenda import Describer
        from ferenda import util
        import re
        DCT = Namespace("http://purl.org/dc/terms/")
        d = Describer(doc.meta, doc.uri)
        d.value(DCT.title, soup.find("title").text, lang=doc.lang)
        d.value(DCT.abstract, soup.find(True, "abstract"), lang=doc.lang)
        # find the issued date -- assume it's the first thing that looks
        # like a date on the form "22 August 2013"
        re_date = re.compile(r'(\d+ \w+ \d{4})')
        datenode = soup.find(text=re_date)
        datestr = re_date.search(datenode).group(1)
        d.value(DCT.issued, util.strptime(datestr, "%d %B %Y"))
        editors = soup.find("dt", text=re.compile("Editors?:"))
        for editor in editors.find_next_siblings("dd"):
            editor_name = editor.text.strip().split(", ")[0]
            d.value(DCT.editor, editor_name)
# end metadata
