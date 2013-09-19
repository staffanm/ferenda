# begin makedoc
from ferenda.elements import Body, Heading, Paragraph, Footnote

doc = Body([Heading(["About Doc 43/2012 and it's interpretation"],predicate="dct:title"),
            Paragraph(["According to Doc 43/2012",
                       Footnote(["Available at http://example.org/xyz"]),
                       " the bizbaz should be frobnicated"])
           ])
# end makedoc

# begin derived-class
from ferenda.elements import CompoundElement, OrderedElement

class Preamble(CompoundElement): pass
class PreambleRecital(CompoundElement,OrderedElement):
    tagname = "div"
    rdftype = "eurlex:PreambleRecital"

doc = Preamble([PreambleRecital("Un",ordinal=1)],
               [PreambleRecital("Deux",ordinal=2)],
               [PreambleRecital("Trois",ordinal=3)])
# end derived-class

# begin as-xhtml
p = SectionalElement(["Some content"],
                     ordinal = "1a",
                     identifier = "Doc pt 1(a)"
                     title="Title or name of the part")
body = Body([p])
etree.tostring(body.as_xhtml("http://example.org/doc")
# end as-xhtml
