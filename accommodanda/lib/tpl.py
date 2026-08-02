"""The Jinja environment for every HTML producer in the system.

Lives in its own module (not lib.render) so that everything which renders a
fragment can reach the environment without importing the renderer: modules
render.py itself imports (lib.feeds), the API's own screens (api/*), and each
source's page renderer, which builds its own environment over its own
templates/ with lib/templates/ as the fallback.

Autoescape means plain values escape themselves and pre-rendered fragments
pass through as markupsafe.Markup; StrictUndefined makes a missing context
variable a hard error instead of a silent empty string (rule:fail-fast).
"""

from typing import cast

from jinja2 import ChoiceLoader, Environment, PackageLoader, StrictUndefined

# masthead nav: label, browse route, the page kinds that mark it current.
# Environment data (page.html's masthead reads it), so it lives with the
# environments rather than in render.py.
MAST_NAV = (("Lagar", "/sfs/", ("Författning",)),
            ("Rättsfall", "/dom/", ("Rättsfall",)),
            ("Förarbeten", "/forarbete/", ("Proposition", "SOU", "Ds",
             "Kommittédirektiv", "Förordningsmotiv", "Skrivelse", "Lagrådsremiss",
             "Sveriges internationella överenskommelser", "Förarbete")),
            ("Myndigheter", "/myndigheter/", ("Föreskrift", "Myndigheter",
             "JO-beslut", "JK-beslut", "ARN-beslut", "Myndighetsavgörande",
             "Rättsligt ställningstagande")),
            ("EU-rätt", "/eurlex/", ("EU-förordning", "EU-direktiv", "EU-beslut",
             "EU-domstolen", "Fördrag", "EU-rättsakt", "Riktlinje",
             "Rekommendation", "Artikel 29-gruppens vägledning")),
            ("Folkrätt", "/folkratt/", ("Folkrätt", "Europarådets fördrag",
             "Internationell humanitär rätt", "FN-fördrag", "Europadomstolen",
             "Internationella brottmålsdomstolen")),
            ("Begrepp", "/begrepp/", ("Begrepp",)),
            ("Om", "/om/", ("Om",)),
            ("Nyheter", "/dataset/sitenews/feed/", ("Nyheter",)))


def environment(package, path="templates"):
    """A Jinja environment over `package`'s template directory with the
    system-wide policy (autoescape + StrictUndefined). A vertical's own
    templates resolve first, with lib/templates/ as fallback so a vertical
    page template can `{% extends "page.html" %}` -- the vertical→lib
    direction the import rules already allow; lib templates can never reach
    a vertical's (rule:lib-never-imports-vertical)."""
    loader = PackageLoader(package, path)
    if package != "accommodanda.lib":
        loader = ChoiceLoader([loader, PackageLoader("accommodanda.lib",
                                                     "templates")])
    env = Environment(loader=loader, autoescape=True,
                      undefined=StrictUndefined)
    # runtime-wise a plain dict[str, Any]; jinja2 ships no stubs, so ty infers
    # the value type from the library's own default-namespace literals and
    # rejects any other value shape -- widen it to what it really is
    cast("dict[str, object]", env.globals)["MAST_NAV"] = MAST_NAV
    return env


ENV = environment("accommodanda.lib")
