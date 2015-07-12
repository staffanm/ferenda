from ferenda.sources.legal.se import ARN as OrigARN
from . import SameAs

# This subclass is just so that the ResourceLoader picks up resources
# from lagen/nu/res
class ARN(OrigARN, SameAs):
    pass
