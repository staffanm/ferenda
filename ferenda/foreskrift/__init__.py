"""Myndighetsföreskrifter (Swedish agency regulations) vertical.

~100 agencies publish binding regulations into their own författningssamling
(FFFS, AFS, NFS, …). There is no central API -- lagrummet.se is a link
directory -- so harvest is irreducibly per-agency. But the *publishing
architectures* are few: an agency is configuration over a shared harvest engine
(:mod:`harvest`), not a bespoke pipeline. Regulations and their consolidated
versions are first-class primitives (:mod:`model`).
"""
