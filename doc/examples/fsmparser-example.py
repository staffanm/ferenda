# begin recognizers
from ferenda import elements, FSMParser

def is_section(parser):
    chunk = parser.reader.peek()
    lines = chunk.split("\n")
    return (len(lines) == 2 and
            len(lines[0]) == len(lines[1]) and
            lines[1] == "=" * len(lines[0]))

def is_preformatted(parser):
    chunk = parser.reader.peek()
    lines=chunk.split("\n")
    not_indented = lambda x: not x.startswith("  ")
    return len(list(filter(not_indented,lines))) == 0

def is_paragraph(parser):
    return True
# end recognizers

# begin constructors
def make_body(parser):
    b = elements.Body()
    return parser.make_children(b)

def make_section(parser):
    chunk = parser.reader.next()
    title = chunk.split("\n")[0]
    s = elements.Section(title=title)
    return parser.make_children(s)
setattr(make_section,'newstate','section')
    
def make_paragraph(parser):
    return elements.Paragraph([parser.reader.next()])

def make_preformatted(parser):
    return elements.Preformatted([parser.reader.next()])
# end constructors

# begin main
transitions = {("body", is_section): (make_section, "section"),
               ("section", is_paragraph): (make_paragraph, None),
               ("section", is_preformatted): (make_preformatted, None),
               ("section", is_section): (False, None)}

text = """First section
=============

This is a regular paragraph. It will not be matched by is_section
(unlike the above chunk) or is_preformatted (unlike the below chunk),
but by the catch-all is_paragraph. The recognizers are run in the
order specified by FSMParser.set_transitions().

    This is a preformatted section.
        It could be used for source code,
    +-------------------+
    |   line drawings   |
    +-------------------+
        or what have                 you.

Second section
==============

The above new section implicitly closed the first section which we
were in. This was made explicit by the last transition rule, which
stated that any time a section is encountered while in the "section"
state, we should not create any more children (False) but instead
return to our previous state (which in this case is "body", but for a
more complex language could be any number of states)."""

p = FSMParser()
p.set_recognizers(is_section, is_preformatted, is_paragraph)
p.set_transitions(transitions)
p.initial_constructor = make_body
p.initial_state = "body"
body = p.parse(text.split("\n\n"))
# print(elements.serialize(body))

# end main
return_value = elements.serialize(body)
