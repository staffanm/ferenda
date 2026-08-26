"""Which treaties in the corpus an ICJ decision applies.

This is why the source is here. The corpus already held the instruments the
Court interprets -- the Genocide Convention, the VCLT, UNCLOS, the ICCPR, CAT,
the Refugee Convention -- and every one of those pages had no inbound link at
all: nothing in the corpus cited them. An ICJ judgment is the document that
does, and saying so is what turns a status page into cited law.

The matching is `lib.treatyref`'s, shared with `icc`. Nothing is added here:
the Court's own "the Statute" means the Statute of the Court, which the corpus
does not hold, so it correctly resolves to nothing -- where the same words in
an ICC decision mean the Rome Statute and `icc.treaties` says so.
"""

from ..lib import treatyref


def references(text):
    """The instruments this decision cites, article-level where it names one.

    Article-level became possible when `untc` gained the treaty text: until
    then the MTDSG status page had no article to anchor to, and a citation to
    "Article II of the Genocide Convention" could only reach the instrument.
    """
    return treatyref.references(text)


def refs(text):
    """The same citations as inline-linkable `lagrum.Ref` spans, for the runs
    the artifact structure stores -- so "Article II of the Genocide
    Convention" is a link where the reader meets it, not only a relation in
    the rail."""
    return treatyref.refs(text)
