"""The ``site`` vertical: lagen.nu's editorial chrome -- the curated frontpage
law list, the ``/om/*`` about pages, and the sitenews feed -- authored as
markdown in the ``lagen-wiki`` content repo (``site/``), parsed to JSON
artifacts and rendered into the generated static site during ``generate``.

Unlike the document verticals it carries no citation graph, so it is registered
as a source (parse + generate) but is never related/indexed/dumped -- like
``remisser``, it is absent from ``build.ARTIFACTS``.
"""
