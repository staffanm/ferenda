Testing your repo
=================

Derive from :py:class:`~ferenda.testutil.RepoTester` and set the
``repoclass`` property, call
:py:func:`~ferenda.testutil.parametrize_repotester` in your top-level
test code, and create a bunch of files in
``files/repo/[alias]/{source,download,distilled,parsed}``. Easy!
