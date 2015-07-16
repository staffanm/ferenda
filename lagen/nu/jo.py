from ferenda.sources.legal.se import JO as OrigJO
from . import SameAs

# This subclass is just so that the ResourceLoader picks up resources
# from lagen/nu/res
class JO(OrigJO, SameAs):
    pass
