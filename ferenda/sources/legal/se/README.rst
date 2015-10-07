Misc notes about these docrepos
===============================

All docrepos in this package should inherit from
SwedishLegalSource. Any custom DocumentStore should inherit from
SwedishLegalStore. These classes have a bunch of extra functionality
compared to DocumentRepository, which help with writing consistent
document repos containing swedish law related documents. Some of that
functionality might migrate to DocumentRepository in due time if it's
found to be generic enough.

* self.minter helps with creating URIs from RDF properties
* self.patch_if_needed works with file handles, not text blobs
* The parse step has a more fine-grained structure with more points to
  override
  

General structure of the parse step
-----------------------------------

This is a more fine-grained version of the structure in
DocumentRepository.parse. All URI-generating functions (primarily
canonical_uri, but also all parts that generate URIs to other docs)
should use self.minter.

Where DocumentRepository.parse calls soup_from_basefile,
parse_metadata_from_soup and parse_document_from_soup in that order,
SwedishLegalSource.parse uses a slightly different call hierarcy::

 canonical_uri(basefile) -> str
     metadata_from_basefile(basefile) -> dict
     attributes_to_resource(dict) ->rdflib.Resource
 parse(doc) -> bool
     parse_open(basefile) -> file
         downloaded_to_intermediate(basefile) -> file
         patch_if_needed(file) -> file
     parse_metadata(file, basefile) -> rdflib.Resource
         extract_head(file, basefile) -> object
         extract_metadata(object, basefile) -> dict
	     [metadata_from_basefile(basefile) -> dict]
         sanitize_metadata(dict, basefile) -> dict
             sanitize_identifier(str) -> str
         polish_metadata(dict) -> rdflib.Resource
             attributes_to_resource(dict) -> rdflib.Resource
         infer_metadata(rdflib.Resource, basefile) -> rdflib.Resource
     parse_body(file, basefile) -> elements.Body
         extract_body(file, basefile) -> object
         sanitize_body(object) -> object
         get_parser(basefile) -> callable
         tokenize(object) -> iterable
	 callable(iterable) -> elements.Body
         visitor_functions() -> callables
         visit_node(elements.Body, callable, state) -> state
	     callable(elements.CompoundElement, state) -> state
     postprocess_doc(doc)
     parse_entry_update(doc)

Metadata about a document is generally first captured/extracted as simple
key/value pairs stored in a dict. The keys are either derived from
EBNF parsing rules ("sfs", "chapter" etc) or are string-based CURIEs
with well-known prefixes ("rpubl:arsutgava"). These attribute dicts
are refined to a full RDF graph by attributes_to_resource(), at which time 
plain-text identifiers are switched to URI resources as applicable, etc. 
Afterwards, infer_metadata uses this graph (and basefile) to infer additional 
missing triplets. (A derived class in the lagen.nu package might override 
infer_metadata to add owl:sameAs statements.

