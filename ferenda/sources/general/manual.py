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

class ManualHandler(RequestHandler):

    @property
    def rules(self):
        return [Rule('/manual/add', endpoint=self.handle_add)] + super(ManualHandler.self).rules


    @login_required
    def handle_add(self, request, **values):
        if request.method == 'GET':
            return self.render_template("""
<div>
  <form method="POST">
    <div class="form-group">
      <label for="title">Document title
        <input type="text" id="title" name="title" class="form-control"/>
      </label>
      <label for="identifier">Document identifier (if applicable)
        <input type="text" id="identifier" name="identifier" class="form-control>
      </label>
      <label class="form-check-label<input type="file" name="doc">
</form>
</div>""", "Add new document")
        elif request.method == 'POST':
            # TBW...
            pass
