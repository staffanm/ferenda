Testing your docrepo
====================

The module :py:mod:`~ferenda.testutil` contains an assortment of
classes and functions that can be useful when testing code written
against the Ferenda API.

Extra assert methods
--------------------

The :py:class:`ferenda.testutil.FerendaTestCase` is intended to be
used by your :py:class:`unittest.TestCase` based testcases. Your
testcase inherits from both ``TestCase`` and ``FerendaTestCase``, and
thus gains new assert methods:

=====================================  ======================
Method                                 Description
=====================================  ======================
:py:meth:`.assertEqualGraphs`          Compares two
                                       :py:class:`~rdflib.graph.Graph` objects
:py:meth:`.assertEqualXML`             Compares two XML documents (in string or
                                       :py:mod:`lxml.etree` form)
:py:meth:`.assertEqualDirs`            Compares the files and contents of those
                                       files in two directories
:py:meth:`.assertAlmostEqualDatetime`  Compares two datetime objects to a
                                       specified precision
=====================================  ======================

Creating parametric tests
-------------------------

A parametric test case is a single unit of test code that, during test
execution, is run several times with different arguments
(parameters). The function :py:func:`ferenda.testutil.parametrize`
creates a single new testcase, based upon a template method, and binds
the specified parameters to the template method. Each testcase is
uniquely named based on the given parameters. Since each invocation
creates a new test case method, specific parameters can be tested in
isolation, and the normal unittest test runner reports exactly which
parameters the test succeeds or fails with.

Often, the parameters to the test is best stored in files. The
function :py:func:`ferenda.testutil.file_parametrize` creates one
testcase, based upon a template method, for each file found in a
specified directory.

RepoTester
----------

Functional tests are written to test a specific functionality of a
software system as a whole. This means that functional tests excercize
a larger portion of the code and is focused on what the behaviour
(output) of the code should be, given a particular input. A typical
repository has at least three large units of code that benefits from a
functional-level testing: Code that performs downloading of documents,
code that extracts metadata from downloaded documents, and code that
generates structured XHTML documents from the downloaded documents.

The :py:class:`~ferenda.testutil.RepoTester` contains generic,
parametric test for all three of these. In order to use them, you
create test data in some directory of your choice, create a subclass
of ``RepoTester`` specifying the location of your test data and the
docrepo class you want to test: and finally call
:py:func:`~ferenda.testutil.parametrize_repotester` in your top-level
test code to set up one test for each test data file that you've
created.

.. literalinclude:: repotester.py

For each download test, you need to create a JSON file under the
``source`` directory of your docroot, eg:
``myrepo/tests/files/source/basic.json``. The content of that file
should be a dict of ...

.. literalinclude:: repotester-basic.json

See docs for :py:class:`~ferenda.testutil.RepoTester.download_test`,
:py:class:`~ferenda.testutil.RepoTester.distill_test` and
:py:class:`~ferenda.testutil.RepoTester.parse_test` to learn which
files need to be created where in order to create a full test.


Py23DocChecker
--------------

This is a small helper to enable you to write doctest-style tests that
run unmodified under python 2 and 3. The main problem with
cross-version compatible doctests is with functions that return
(unicode) strings. These are formatted ``u'like this'`` in Python 2,
and ``'like this'`` in Python 3. Writing doctests for functions that
return unicode strings requires you to choose one of these syntaxes,
and the result will fail on the other platform. By strictly running
doctests from within the :py:mod:`unittest` framework through the
``load_tests`` mechanism, and loading your doctests in this way, the
tests will work even under Python 2::

    from ferenda.testutil import Py23DocChecker
    def load_tests(loader,tests,ignore):
        tests.addTests(doctest.DocTestSuite(mymodule, checker=Py23DocChecker()))
        return tests
    

testparser
----------

This is a simple helper that tests FSMParse based parsers.
