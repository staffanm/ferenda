Advanced topics
===============


CompositeDocumentRepository
---------------------------

In some cases, a document collection may available from multiple
sources, with varying degrees of completeness and/or quality. For
example, in a collection of US patents, some patents may be available
in structured XML with good metadata through a easy-to-use API, some
in tag-soup style HTML with no metadata, requiring screenscraping, and
some in the form of TIFF files that you scanned yourself. The
implementation of both download() and parse() will differ wildly for
these sources. You'll have something like this:

.. code-block:: py

  class XMLPatents(DocRepository)
      alias = "patxml"
  
      def download(self, basefile = None):
          download_from_api()
  
      def parse(self,doc):
          transform_patent_xml_to_xhtml(doc.basefile)
  
  class HTMLPatents(DocRepository)
      alias = "pathtml"
    
      def download(self, basefile = None):
          screenscrape()
  
      def parse(self,doc):
          analyze_tagsoup(doc)
  
  class ScannedPatents(DocRepository):
      alias = "patscan"
  
      # Assume that we, when we scanned the documents, placed them in their
      # correct place under download
      def download(self, *args): pass
  
      def parse(self,doc):
          ocr_and_structure(doc)
  
But since the result of all three parse() implementations are
XHTML1.1+RDFa documents (possibly with varying degrees of data
fidelity), the implementation of generate() will be substantially the
same. Furthermore, you probably want to present a unified document
collection to the end user, presenting documents derived from
structured XML if they're available, documents derived from tagsoup
HTML if an XML version wasn't available, and finally documents derived
from your scanned documents if nothing else is available.

The class CompositeRepository makes this possible. You specify a
number of sub-docrepos using a class property.

.. code-block:: py

  class CompositePatents(CompositeRepository):
      alias = "pat"
      subrepos = (XMLPatents, HTMLPatents, ScannedPatents)
  
      def generate(self, basefile):
          # Code to transform XHTML1.1+RDFa documents, regardless of 
          # wheter these are derived from structured XML, tagsoup HTML
          # or scanned TIFFs
          do_the_work()
  
A CompositeRepository can act as a proxy for your specialized repositories::

  ./ferenda-build.py patents.CompositePatents enable
  ./ferenda-build.py pat download # calls download() for all subrepos
  ./ferenda-build.py pat parse 5723765 # selects the best subrepo that has  patent 5,723,765, calls parse() for that, then copies the result to pat/parsed/ 5723765 (or links)
  ./ferenda-build.py pat generate 5723765 # uses the pat/parsed/5723765 data. From here on, we're just like any other docrepo.
  
Note that patents.XMLPatents and the other subrepos are never
enabled/registered, just called behind-the-scenes by
patents.CompositePatents.

If you prefer scanned TIFFs over tagsoup HTML, simple change the order
in the subrepos class property.

Patch files
-----------

Applied on downloaded or intermediate files. 
* Reason for patching: Correcting bad data, privacy, handling
difficult and rare parse situations
* Creating a patch: devel mkpatch <path-to-intermediate-file>
* Managing patches: like other project assets
* Resulting metadata in RDFa files

External resources
------------------
Not everything from the downloaded document content may be reproduced
in the XHTML 1.1 serialization that parse() creates. In particular,
images must be stored separately. In some cases (such as documents
produced by PDFDocumentRepository) CSS files are also created in the
parsing process. All such files are external resources, and stored
alongside the main XHTML file. They are enumerated through the
`list_external_resources()` method.

External annotations
--------------------
From wikis and other places. Just create a DocRepo for which documents
are downloaded, parsed but not generated. eg. wiki/parsed/pat/5723765
may be a commentary on the '765 patent, that explicitly refers to that
using its canonical uri. When `./ferenda-build.py pat generate
5723765` runs, the content of that commentary is pulled into the
annotation file and displayed alongside the main document text.

