About lagen.nu
==============

Lagen.nu is a non-profit, volunteer-run web site which provides access
to legal information concerning the swedish legal system. It contains
all statutes published in the main collection of statutory law, SFS
(Svensk Författningssamling), as well as an archive of case law from
the swedish supreme court, the supreme administrative court, and a
number of special courts. It also contains commentary on a number of
the most important statutes, as well as important legal terms. These
commentaries are written by law students and practicing lawyers.

Collaborative commentaries
--------------------------

Like any legal system, swedish law can be daunting at first. The style
of writing has changed considerably from the oldest laws (from 1736)
to the ones written today, and the terms used, as well as the
structure of the regulation, often needs to be explained to be
understandable. This understanding is what a typical legal education
provides. But since the law applies to everyone - not just the legally
trained - there is a need for an explaination of the statutory
text. We provide this in the form of a law commentary for the most
important statutes.

For each important section of these statutes, a brief
explaination of the section is written. This can include
descriptions of terms used, guidelines for balancing opposing
interests, notes on how the section have been referred to in
important legal cases, and hypothetical examples of it's
application. It frequently refers to other parts of the law that
one needs to be aware of when analysing the particular section.

Legal terms are often used in many different section
commentaries. In these cases, it's often more effective to just
mention the term in the section commentary and link it to a
separate page, containing a more detailed description of the term,
so that a reader not familiar with the term can learn more about
it. This also keeps the statutory law commentary brief for readers
who are familiar with the term.

The commentaries have so far mainly been written by law
students. The text of the commentaries are licensed under the
Creative Commons Attribution-Share Alike license. Anyone who is
knowledgeable about a certain statute is welcome to apply for
writing its commentary.

The actual writing is done using the Mediawiki system, the same
web-based wiki application that Wikipedia uses. The text is
written according to certain conventions (such as prefixing the
commentary for an individual section with a headline consisting of
that section's number). When saving the text of a legal
commentary, it gets weaved together with the statutory law text
and presented alongside of it. The text of pages that describe
legal terms are combined with legal cases and statutes using or
defining the same term.

Swedish legal information
-------------------------

As a civil law country, swedish law is primarily concerned with
statutory law. The main legislative powers are the parliament
(Riksdagen) and the government (Regeringen) - each of these
institutions can adopt statutes which are published in the main
official collection of statutory law, the Svensk
Författningssamling (SFS). The statutes enacted by the parliament
are referred to as laws, and the statutes enacted by the
government as ordinances.

Whenever a particular statute is changed, this is done by
adopting a new statute (the change statute) that states what
sections of the old statute (the base statute) are to be changed,
and how. In SFS, only these base statutes and change statutes are
published. In practice, consolidated versions (texts where the
actual texts of the base statutes have been changed according to
subsequent change statutes) are used by lawyers and courts, but
these texts are not officially binding.

Lagen.nu uses consolidated versions of the statutes, available
from the governments legal databases. These versions, which are in
a non-structured plain text version, is parsed and analysed to get
a XML version of the text that represents the true structure of
the statue, divided into chapters, sections, paragraphs and so on.

Court decisions are also an important part of swedish law,
particularly the decisions from the supreme courts. The National
Courts Administration makes available an archive of over 10 000
court decisions. These are available with the full text of the
verdict as well as some metadata (such as which statutory law
sections the verdict is based upon, earlier cases referred to, and
keywords for the issues in the case).

There are other sources of legal information in the Swedish
system - particularly preparatory works for the statutes are often
used when interpreting the statutes themselves, and courts often
explicitly refer to these preparatory works. Certain
administrative agencies have the power to create binding statutes
concerning issues in their area. Some administrative agencies have
the power to make legally binding decisions for certain issues,
and these decisions are often referred to, particularly when doing
legal investigations in areas where there's a dearth of supreme
court decisions (such as consumer rights - not many consumers have
the time and resources to appeal a case all the way to the supreme
court). These sources are not yet present at lagen.nu.

Browsing and navigation features
--------------------------------

Statutes
^^^^^^^^

There are about 1500 laws and 2000 ordinances in the swedish
legal system. Some of these can be quite long (the longest, the
income tax law, has around 1500 individual sections and close to
120 000 words), but since individual sections frequently refer to
each other, each law is presented as a single web page.

To the left, each law have a treeview-like control containing
the entire table of contents for the law, sectioned into chapters
and headlines. In addition to this, the text of each section is
parsed and references to other parts of the law (or other laws)
are identified, and hyperlinked. Together, this makes navigating
large amounts of statute text reasonably quick.

Statutes are divided in sections (and, for larger statutes,
chapters and divisions). To the right of each individual section
are a number of boxes containing information about that
section.

* If available, a commentary box explains the text of the
  section and gives examples of it's application.
* If any of the legal cases (see below) refer to the section,
  they are listed in another box.
* If another section of the same
  or any other statute refer to the section, these sections are
  mentioned in another box.
* And finally, if the section have been changed, a box lists
  all the change statutes that have modified this section
  throughout it's history, with links to more information
  (including a PDF of the actual change statute).

Cases
^^^^^

There are over 10 000 cases available on the web site, ranging
back to 1981. The cases are from the swedish supreme court, the
supreme administrative court, as well as the special courts used
for certain legal disputes (such as labour law, environmental law,
marketing law etc).

Each case is presented in full text, with hyperlinked
references to each individual statute section that is mentioned in
the verdict, and other metadata. Of particular interest is the
usage of keywords -- when preparing the case for publication, the
National Courts Administration provides it with a series of
keywords, often specific legal terms that was referred to in the
verdict. This makes it possible to order the cases by keyword, for
example, see all cases that deal with issues of occupational
safety and health.

Legal terms
^^^^^^^^^^^

When parsing statutory text, passages that define a particular
term are recognized. This information is combined with the
information about which keywords are used in which legal cases, as
well as text from the legal information wiki (mentioned above), to
form a single page that provides an overview of the term, its
definition and usage. Links to this page appear whenever the term
is used in commentary, as a keyword for a legal case, or in the
statutory law text. Around 4500 terms are currently present in the
system.

Reuseable
---------

We actively want people to use and reuse the legal information
and functionality found at lagen.nu. We make this possible in four
different ways.

Linking
^^^^^^^

Being a public web site, we strive to make it easy to link to
any content on lagen.nu. As each statute has it's own unique
number (the SFS number), we use this to construct the URL for that
statute - i.e. The Copyright Act (1960:729) has the URL
``https://lagen.nu/1960:729``. Furthermore, any
individual section can be referred to using named anchors, so to
URL for section 12 of the copyright act is
``https://lagen.nu/1960:729#P12``. This is a documented
part of our interface and guaranteed not to change, so anyone
linking to the site can be sure that the link will work
indefinitely. And of course, noone needs to ask permission to link
to us.

Structured data
^^^^^^^^^^^^^^^

The actual statutory text are not copyrighted. The text of the
legal cases are copyrighted, but may be reproduced by anyone as
long as the text is not improperly changed and the author (in this
case the National Courts Administration) is credited.

Lagen.nu makes these texts available in a structured, XML-based format
(specifically XHTML with embedded RDFa metadata). The files are all
available by requesting the MIME type ``application/xhtml+xml``, ie.::

  curl -H "Accept: application/xhtml+xml" https://lagen.nu/1998:204


They can also be downloaded in bulk. The metadata used in the system
(for example titles, dates, case numbers, and links between cases and
statutes) is expressed using RDF, and the entire metadata set
(comprising over a million RDF triples) can be downloaded in bulk as
well.

Commentaries
^^^^^^^^^^^^

The commentaries are all licensed under the Creative Commons
Attribution-Share Alike license, which should enable re-use of
these in practically any scenario.

Code
^^^^

To run a web site like lagen.nu on a volounteer budget, a lot of
things need to be automated. There is a fairly complex code base to do
things like downloading all statutes and legal cases, parsing the raw
data, structuring it and formatting it for presentation. This is done
mainly in python (around 25000 lines) and XSLT (around 2500
lines). The code is licensed under a BSD-style license and can be
fetched from `GitHub <https://github.com/staffanm/ferenda/>`_ -
everybody who wishes to build a similar site, or just to find out how
we have done certain things, are welcome to check it out.

Contact
-------

If you have any questions, please contact staffan@lagen.nu. 
