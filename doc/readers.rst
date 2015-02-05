Reading files in various formats
================================

The first step of parsing a document is often getting actual text from
a file. For plain text files, this is not a difficult process, but for
eg. Word and PDF documents some sort of library support is useful.

Ferenda contains three different classes that all deal with this
problem. They do not have a unified interface, but instead contain
different methods depending on the structure and capabilities of the
file format they're reading.

Reading plain text files
------------------------

The :py:class:`~ferenda.TextReader` class works sort of like a regular
file object, and can read a plain text file line by line, but contains
extra methods for reading files paragraph by paragraph or page by
page. It can also produce generators that yield the file contents
divided into arbitrary chunks, which is suitable as input for
:py:class:`~ferenda.FSMParser`.

Microsoft Word documents
------------------------

The :py:class:`~ferenda.WordReader` class can read both old-style
``.doc`` files and newer, XML-based ``.docx`` files. The former
requires that `antiword <http://www.winfield.demon.nl/>`_ is
installed, but the latter has no additional dependencies.

This class does not present any interface for actually reading the
word document -- instead, it converts the document to a XML file which
is either based on the ``docbook`` output of ``antiword``, or the raw
OOXML found inside of the ``.docx`` file.

PDF documents
-------------

:py:class:`~ferenda.PDFReader` reads PDF documents and makes them
available as a list of pages, where each page contains a list of
:py:class:`~ferenda.pdfreader.Textbox` objects, which in turn contains
a list of :py:class:`~ferenda.pdfreader.Textelement` objects.

Its :py:meth:`~ferenda.PDFReader.textboxes` method is a flexible way
of getting a generator of suitable text chunks. By passing a "glue"
function to that method, you can specify exact rules on which rows of
text should be combined to form larger suitable chunks
(eg. paragraphs). This stream of chunks can be fed directly as input
to :py:class:`~ferenda.FSMParser`.



Handling non-PDFs and scanned documents
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The class can also handle any other type of document (such as
Word/OOXML/WordPerfect/RTF) that OpenOffice or LibreOffice handles by
first converting it to PDF using the ``soffice`` command line
tool. This is done by specifiying the ``convert_to_pdf`` parameter.

If the PDF contains only scanned pages (without any OCR information),
the pages can be run through the ``tesseract`` command line tool. You
need to provide the main language of the document as the ``ocr_lang``
parameter, and you need to have installed the tesseract language files
for that language.


Analyzing PDF documents
^^^^^^^^^^^^^^^^^^^^^^^

When processing a PDF file, the information contained in eg a
:py:class:`~ferenda.pdfreader.Textbox` object (position, size, font)
is useful to determine what kind of content it might be, eg. if it's
set in a header-like font, it probably signals the start of a section,
and if it's a digit-like text set in a small font outside of the main
content area, it's probably a page number.

Information about eg page margins, header styles etc can be hardcoded
in your processing code, but it's also possible to use the companion
class :py:class:`~ferenda.PDFAnalyzer` can be used to statistically
analyze a complete document and then make educated guesses about these
metrics. It can also output histogram plots and an annotated version
of the original PDF file with lines marking the identified margins,
styles and text chunks (given a provided "glue" function identical to
the one provided to :py:meth:`~ferenda.PDFReader.textboxes`)

The class is designed to be overridden if your document has particular
rules about eg. header styles or additional margin metrics.
