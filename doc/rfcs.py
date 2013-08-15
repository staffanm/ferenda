# begin download1
import re
from itertools import islice
from datetime import datetime, date

import requests

from ferenda import DocumentRepository, TextReader
from ferenda import util

class RFCs(DocumentRepository):
    alias = "rfc"
    start_url = "http://www.ietf.org/download/rfc-index.txt"
    document_url_template = "http://tools.ietf.org/rfc/rfc%(basefile)s.txt"
    downloaded_suffix = ".txt"
# end download1
    
# begin download2
    def download(self):
        self.log.debug("download: Start at %s" %  self.start_url)
        indextext = requests.get(self.start_url).text
        reader = TextReader(string=indextext)  # see TextReader class
        iterator = reader.getiterator(reader.readparagraph)
        if not isinstance(self.config.downloadmax, (int, type(None))):
            self.config.downloadmax = int(self.config.downloadmax)
            
        for basefile in islice(self.download_get_basefiles(iterator),
                        self.config.downloadmax):
            self.download_single(basefile)

    def download_get_basefiles(self, source):
        for p in reversed(list(source)):
            if re.match("^(\d{4}) ",p): # looks like a RFC number
                if not "Not Issued." in p: # Skip RFC known to not exist
                    basefile = str(int(p[:4]))  # eg. '0822' -> '822'
                    yield basefile
                        
                               
# end download2

# begin parse1
    # In order to properly handle our RDF data, we need to tell ferenda which namespaces
    # we'll be using. See :py:data:`~ferenda.DocumentRepository.namespaces`
    namespaces = ('rdf',  # always needed
                  'dct',  # title, identifier, etc
                  'bibo', # Standard and DocumentPart classes, chapter prop
                  'xsd',  # datatypes
                  'foaf', # rfcs are foaf:Documents for now
                  ('rfc','http://example.org/ontology/rfc/')
                  )

    from ferenda.decorators import managedparsing

    @managedparsing
    def parse(self, doc):
        # some very simple heuristic rules for determining 
        # what an individual paragraph is
   
        def is_heading(p):
            # If it's on a single line and it isn't indented with spaces
            # it's probably a heading.
            if p.count("\n") == 0 and not p.startswith(" "):
                return True
  
        def is_pagebreak(p):
            # if it contains a form feed character, it represents a page break
            return "\f" in p
        
   
        # create body of document
        from ferenda.elements import Body, Preformatted, Title, Heading
        from ferenda import Describer
        reader = TextReader(self.store.downloaded_path(doc.basefile))
  
        # First paragraph of an RFC is always a header block 
        header = reader.readparagraph()
        doc.body.append(Preformatted([header]))
  
        # Second is always the title
        title = reader.readparagraph()
        doc.body.append(Title([title.strip()]))
        # After that, just iterate over the document and guess what
        # everything is.
        for para in reader.getiterator(reader.readparagraph):
            if is_heading(para):
                doc.body.append(Heading([para]))
            elif is_pagebreak(para):
                # Just drop these remnants of a page-and-paper-based past
                pass
            else:
                doc.body.append(Preformatted([para])) 

        # create metadata for document
        desc = Describer(doc.meta, doc.uri)
        
        # Set the title we've captured as the dct:title of the document and 
        # specify that it is in English
        desc.value(self.ns['dct'].title, title.strip(), lang="en")
  
        # find and convert the publication date in the header to a datetime 
        # object, and set it as the dct:published date for the document   
        re_date = re.compile("(January|February|March|April|May|June|July|August|September|October|November|December) (\d{4})").search
        with util.c_locale(): # in case locale settings are different
            dt = datetime.strptime(re_date(header).group(0), "%B %Y")
        pubdate = date(dt.year,dt.month,dt.day)
        desc.value(self.ns['dct'].published, pubdate)
  
        # find any older RFCs that this document updates or obsoletes
        obsoletes = re.search("^Obsoletes: ([\d+, ]+)", header, re.MULTILINE)
        updates = re.search("^Updates: ([\d+, ]+)", header, re.MULTILINE)

        for predicate, matches in ((self.ns['rfc'].updates, updates),
                                   (self.ns['rfc'].obsoletes, obsoletes)):
            if matches is None:
                continue
            # add references between this document and these older rfcs, 
            # using either rfc:updates or rfc:obsoletes
            for match in matches.group(1).strip().split(", "):
                uri = self.canonical_uri(match)
                desc.rel(predicate, uri)
  
        # No need to return anything -- we've modified the Document object that 
        # was passed to us, the calling code will use this modified object and 
        # serialize it to XHTML and RDF
# end parse1
        reader.seek(0)
        reader.readparagraph()
        reader.readparagraph()
# begin parse2                                   
        from ferenda.elements import Section, Subsection, Subsubsection
        def is_section(p):
            return re.match("\d+\. +[A-Z]", p)

        def is_subsection(p):
            return re.match("\d+\.\d+\.? +[A-Z]", p)

        def is_subsubsection(p):
            return re.match("\d+\.\d+\.\d+\.? +[A-Z]", p)

        stack = [Body()]
        doc.body = stack[0]
        for para in reader.getiterator(reader.readparagraph):
            if is_section(para):
                ordinal, title = para.split(" ",1)
                s = Section(title=title, ordinal=ordinal)
                stack[1:] = [] # clear all but bottom element
                stack[0].append(s) # add new section to body
                stack.append(s)    # push new section on top of stack
            elif is_subsection(para):
                ordinal, title = para.split(" ",1)
                s = Subsection(title=title, ordinal=ordinal)
                stack[2:] = [] # clear all but bottom two elements
                stack[1].append(s) # add new subsection to current section
                stack.append(s)
            elif is_subsection(para):
                ordinal, title = para.split(" ",1)
                s = Subsection(title=title, ordinal=ordinal)
                stack[3:] = [] # clear all but bottom three
                stack[2].append(s) # add new subsubsection to current subsection
                stack.append(s)
            elif is_heading(para):
                stack[-1].append(Heading([para]))
            elif is_pagebreak(para):
                pass
            else:
                pre = Preformatted([para])
                stack[-1].append(pre)
# end parse2                                   

# begin citation1                                   
        from pyparsing import Word, CaselessLiteral, nums
        section_citation = (CaselessLiteral("section") + Word(nums+".").setResultsName("Sec")).setResultsName("SecRef")
        rfc_citation = ("[RFC" + Word(nums).setResultsName("RFC") + "]").setResultsName("RFCRef")
        section_rfc_citation = (section_citation + "of" + rfc_citation).setResultsName("SecRFCRef")
# end citation1                                   

# begin citation2
        def rfc_uriformatter(parts):
            uri = ""
            if 'RFC' in parts:
                 uri += self.canonical_uri(parts['RFC'])
            if 'Sec' in parts:
                 uri += "#S" + parts['Sec']
            return uri
# end citation2                                   

# begin citation3
        from ferenda import CitationParser, URIFormatter
        citparser = CitationParser(section_rfc_citation, 
                                   section_citation,
                                   rfc_citation)
        citparser.set_formatter(URIFormatter(("SecRFCRef", rfc_uriformatter),
                                             ("SecRef", rfc_uriformatter),
                                             ("RFCRef", rfc_uriformatter)))
        citparser.parse_recursive(doc.body)
# end citation3                                   

if __name__ == '__main__':
    from ferenda import manager
    manager.setup_logger("DEBUG")
    d = RFCs(downloadmax=10, force=True)
    d.download()
    for basefile in d.list_basefiles_for("parse"):
        d.parse(basefile)
    
    
