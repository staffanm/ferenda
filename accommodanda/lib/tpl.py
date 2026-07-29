"""The Jinja environment for every HTML producer in the system.

Lives in its own module (not lib.render) so that modules render.py itself
imports (lib.feeds) and modules that must never import render (api.app --
render imports it for the in-process browse client) can still render their
fragments from lib/templates/.

Autoescape means plain values escape themselves and pre-rendered fragments
pass through as markupsafe.Markup; StrictUndefined makes a missing context
variable a hard error instead of a silent empty string (rule:fail-fast).
"""

from jinja2 import Environment, PackageLoader, StrictUndefined


def environment(package, path="templates"):
    """A Jinja environment over `package`'s template directory with the
    system-wide policy (autoescape + StrictUndefined). Verticals with their
    own markup (site, stats) build theirs from this so the policy can't
    drift; they compose with the shared chrome by calling lib.render.page()
    on their rendered bodies, not by template inheritance -- lib templates
    stay lib's."""
    return Environment(loader=PackageLoader(package, path),
                       autoescape=True, undefined=StrictUndefined)


ENV = environment("accommodanda.lib")
