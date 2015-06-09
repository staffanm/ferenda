from ferenda.sources.legal.se import ARN as OrigARN

# This subclass is just so that the ResourceLoader picks up resources
# from lagen/nu/res
class ARN(OrigARN):
    pass
