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

    # In order to properly handle our RDF data, we need to tell
    # ferenda which namespaces we'll be using. These will be available
    # as rdflib.Namespace objects in the self.ns dict, which means you
    # can state that something is eg. a dct:title by using
    # self.ns['dct'].title. See
    # :py:data:`~ferenda.DocumentRepository.namespaces`
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
        
        # Parsing a document consists mainly of two parts:
        # 1: First we parse the body of text and store it in doc.body
        from ferenda.elements import Body, Preformatted, Title, Heading
        from ferenda import Describer
        reader = TextReader(self.store.downloaded_path(doc.basefile))
  
        # First paragraph of an RFC is always a header block 
        header = reader.readparagraph()
        # Preformatted is a ferenda.elements class representing a
        # block of preformatted text. It is derived from the built-in
        # list type, and must thus be initialized with an iterable, in
        # this case a single-element list of strings. (Note: if you
        # try to initialize it with a string, because strings are
        # iterables as well, you'll end up with a list where each
        # character in the string is an element, which is not what you
        # want).
        preheader = Preformatted([header])
        # Doc.body is a ferenda.elements.Body class, which is also
        # is derived from list, so it has (amongst others) the append
        # method. We build our document by adding to this root
        # element.
        doc.body.append(preheader)
  
        # Second paragraph is always the title, and we don't include
        # this in the body of the document, since we'll add it to the
        # medata -- once is enough
        title = reader.readparagraph()
        
        # After that, just iterate over the document and guess what
        # everything is. TextReader.getiterator is useful for
        # iterating through a text in other chunks than single lines
        for para in reader.getiterator(reader.readparagraph):
            if is_heading(para):
                # Heading is yet another of these ferenda.elements
                # classes.
                doc.body.append(Heading([para]))
            elif is_pagebreak(para):
                # Just drop these remnants of a page-and-paper-based past
                pass
            else:
                # If we don't know that it's something else, it's a
                # preformatted section (the safest bet for RFC text).
                doc.body.append(Preformatted([para])) 

        # 2: Then we create metadata for the document and store it in
        # doc.meta (in this case using the convenience
        # ferenda.Describer class).

        desc = Describer(doc.meta, doc.uri)

        # Set the rdf:type of the document
        desc.rdftype(self.ns['rfc'].RFC)

        # Set the title we've captured as the dct:title of the document and 
        # specify that it is in English
        desc.value(self.ns['dct'].title, util.normalize_space(title), lang="en")

        # Construct the dct:identifier (eg "RFC 6991") for this document from the basefile
        desc.value(self.ns['dct'].identifier, "RFC " + basefile)
  
        # find and convert the publication date in the header to a datetime 
        # object, and set it as the dct:issued date for the document   
        re_date = re.compile("(January|February|March|April|May|June|July|August|September|October|November|December) (\d{4})").search
        # This is a context manager that temporarily sets the system
        # locale to the "C" locale in order to be able to use strptime
        # with a string on the form "August 2013", even though the
        # system may use another locale.
        with util.c_locale(): 
            dt = datetime.strptime(re_date(header).group(0), "%B %Y")
        pubdate = date(dt.year,dt.month,dt.day)
        # Note that using some python types (cf. datetime.date)
        # results in a datatyped RDF literal, ie in this case
        #   <http://localhost:8000/res/rfc/6994> dct:issued "2013-08-01"^^xsd:date
        desc.value(self.ns['dct'].issued, pubdate)
  
        # find any older RFCs that this document updates or obsoletes
        obsoletes = re.search("^Obsoletes: ([\d+, ]+)", header, re.MULTILINE)
        updates = re.search("^Updates: ([\d+, ]+)", header, re.MULTILINE)

        # Find the category of this RFC, store it as dct:subject
        cat_match = re.search("^Category: ([\w ]+?)(  |$)", header, re.MULTILINE)
        if cat_match:
            desc.value(self.ns['dct'].subject, cat_match.group(1))
            
        for predicate, matches in ((self.ns['rfc'].updates, updates),
                                   (self.ns['rfc'].obsoletes, obsoletes)):
            if matches is None:
                continue
            # add references between this document and these older rfcs, 
            # using either rfc:updates or rfc:obsoletes
            for match in matches.group(1).strip().split(", "):
                uri = self.canonical_uri(match)
                # Note that this uses our own unofficial
                # namespace/vocabulary
                # http://example.org/ontology/rfc/
                desc.rel(predicate, uri)
  
        # And now we're done. We don't need to return anything as
        # we've modified the Document object that was passed to
        # us. The calling code will serialize this modified object to
        # XHTML and RDF and store it on disk

# end parse1
        # Now do it again
        reader.seek(0)
        reader.readparagraph()
        reader.readparagraph()
        doc.body = Body()
        doc.body.append(preheader)
        # doc.body.append(Title([util.normalize_space(title)]))
# begin parse2                                   
        from ferenda.elements import Section, Subsection, Subsubsection

        # More heuristic rules: Section headers start at the beginning
        # of a line and are numbered. Subsections and subsubsections
        # have dotted numbers, optionally with a trailing period, ie
        # '9.2.' or '11.3.1'
        def is_section(p):
            return re.match(r"\d+\. +[A-Z]", p)

        def is_subsection(p):
            return re.match(r"\d+\.\d+\.? +[A-Z]", p)

        def is_subsubsection(p):
            return re.match(r"\d+\.\d+\.\d+\.? +[A-Z]", p)

        def split_sectionheader(p):
            # returns a tuple of title, ordinal, identifier
            ordinal, title = p.split(" ",1)
            ordinal = ordinal.strip(".")
            return title.strip(), ordinal, "RFC %s, section %s" % (basefile, ordinal)

        # Use a list as a simple stack to keep track of the nesting
        # depth of a document. Every time we create a Section,
        # Subsection or Subsubsection object, we push it onto the
        # stack (and clear the stack down to the appropriate nesting
        # depth). Every time we create some other object, we append it
        # to whatever object is at the top of the stack. As your rules
        # for representing the nesting of structure become more
        # complicated, you might want to use the
        # :class:`~ferenda.FSMParser` class, which lets you define
        # heuristic rules (recognizers), states and transitions, and
        # takes care of putting your structure together.
        stack = [doc.body]

        for para in reader.getiterator(reader.readparagraph):
            if is_section(para):
                title, ordinal, identifier = split_sectionheader(para)
                s = Section(title=title, ordinal=ordinal, identifier=identifier)
                stack[1:] = [] # clear all but bottom element
                stack[0].append(s) # add new section to body
                stack.append(s)    # push new section on top of stack
            elif is_subsection(para):
                title, ordinal, identifier = split_sectionheader(para)
                s = Subsection(title=title, ordinal=ordinal, identifier=identifier)
                stack[2:] = [] # clear all but bottom two elements
                stack[1].append(s) # add new subsection to current section
                stack.append(s)
            elif is_subsubsection(para):
                title, ordinal, identifier = split_sectionheader(para)
                s = Subsubsection(title=title, ordinal=ordinal, identifier=identifier)
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

# begin annotations
    sparql_annotations = "rfc-annotations.rq"
# end annotations
# begin xslt
    xslt_template = "rfc.xsl"
# end xslt

    def toc_predicates(self):
        return [self.ns['dct'].title,
                self.ns['dct'].issued,
                self.ns['dct'].subject,
                self.ns['dct'].identifier]


if __name__ == '__main__':
    from ferenda import manager, LayeredConfig
    manager.setup_logger("DEBUG")
    d = RFCs(downloadmax=10, force=True)
#    # d.download()
#    for basefile in d.list_basefiles_for("parse"):
#        d.parse(basefile)
#    RFCs.setup("relate", LayeredConfig(d.get_default_options()))
#    for basefile in d.list_basefiles_for("relate"):
#        d.relate(basefile)
#    RFCs.teardown("relate", LayeredConfig(d.get_default_options()))
#    manager.makeresources([d])
#    for basefile in d.list_basefiles_for("generate"):
#    d.generate("6998")
    d.toc()
    
    
