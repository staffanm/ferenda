"""Dependency-free Council-of-Europe article-fragment grammar.

Kept in its own leaf module (no imports from the rest of the package) so both
producers of a CoE provision fragment can share one implementation: `lib.coe`
(the treaty/HUDOC identity, which also needs `catalog.BASE`) and the citation
engine `lib.lagrum` (which links "artikel N i europakonventionen"). Importing
`lib.coe` from `lib.lagrum` would close the cycle
lagrum -> coe -> catalog -> markdown -> lagrum, so the pure part lives here
instead (rule:second-use-goes-to-lib).
"""


def article_fragment(article, paragraph=None, letter=None):
    """A CoE provision -> its artifact fragment: article "A8", paragraph "A8P1",
    lettered point "A8P1La". The addressing an ECHR/CoE treaty artifact mints."""
    fragment = "A%s" % str(article).lstrip("0")
    if paragraph:
        fragment += "P%s" % str(paragraph).lstrip("0")
    if letter:
        fragment += "L%s" % str(letter).lower()
    return fragment
