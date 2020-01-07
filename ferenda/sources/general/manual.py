# the idea of the "manual" repo is to handle all "one-off" documents
# or repositories that are too small to warrant the authoring of a
# custom scraper, parser etc. Instead, the user uploads PDF or Word
# files (that are internally converted to PDF) which places them in
# the "downloaded" directory. The user should also be able to enter
# some basic metadata (what kind of document there is, it's identifier
# and/or title, possible date, possible dcterms:subject). The document
# type and dcterms:subject should be selectable from a
# editable. Perhaps the identity of the uploading user (if there is
# one specified in an Authorization header). 

# a close usecase is the "curated" selection from an existing repo. In
# that case, the user should in some way be able to specify the
# identifier for a series of documents that are handled by existing
# repos. The existing repos then downloads just those documents, not
# all documents available. When specifying the identifier(s) it should
# also be possible to specify dcterms:subject for these.

# in both cases, the dcterms:subjects should then be used in toc
# generation and in other places where it makes sense
