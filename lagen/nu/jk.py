from ferenda.sources.legal.se import JK as OrigJK
from . import SameAs

# This subclass is just so that the ResourceLoader picks up resources
# from lagen/nu/res
class JK(OrigJK, SameAs):
    pass
