# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# begin makedoc
from ferenda.elements import Body, Heading, Paragraph, Footnote

doc = Body([Heading(["About Doc 43/2012 and it's interpretation"],predicate="dcterms:title"),
            Paragraph(["According to Doc 43/2012",
                       Footnote(["Available at http://example.org/xyz"]),
                       " the bizbaz should be frobnicated"])
           ])
# end makedoc

# begin derived-class
from ferenda.elements import CompoundElement, OrdinalElement

class Preamble(CompoundElement): pass
class PreambleRecital(CompoundElement,OrdinalElement):
    tagname = "div"
    rdftype = "eurlex:PreambleRecital"

doc = Preamble([PreambleRecital("Un",ordinal=1)],
               [PreambleRecital("Deux",ordinal=2)],
               [PreambleRecital("Trois",ordinal=3)])
# end derived-class

# begin as-xhtml
from ferenda.elements import SectionalElement
p = SectionalElement(["Some content"],
                     ordinal = "1a",
                     identifier = "Doc pt 1(a)",
                     title="Title or name of the part")
body = Body([p])
from lxml import etree               
etree.tostring(body.as_xhtml("http://example.org/doc"))
# end as-xhtml
return_value = etree.tostring(body.as_xhtml("http://example.org/doc"),
                              pretty_print=True)
