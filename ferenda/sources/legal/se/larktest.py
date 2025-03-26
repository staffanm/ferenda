from lark import Lark, UnexpectedToken

grammar = r"""
start: (lagrum | _unwanted)+

lagrum: chapter_section_ref
      | section_ref

chapter_section_ref: chapter_ref_id CM section_ref
CM: "kap."
SM: "§"

section_ref: section_ref_id SM

chapter_ref_id: ref_id
section_ref_id: ref_id

ref_id: NUMBER LCASE_LETTER?

_unwanted: UNWANTED_TEXT
UNWANTED_TEXT: /(?!\d+\s*kap\.|\d+\s*§).+/s

%import common.NUMBER
%import common.LCASE_LETTER
%import common.WS
%ignore WS
"""

# Create the Lark parser
parser = Lark(grammar, parser="lalr")

# Test inputs
inputs = [
    "14 kr",
    "14 kap.",
    "14 kap. 2 §",
    "random text",
]

# Test the parser
for text in inputs:
    print(f"Input: {text}")
    try:
        tree = parser.parse(text)
        print(tree.pretty())
    except UnexpectedToken as e:
        print(f"Parse error: {e}")