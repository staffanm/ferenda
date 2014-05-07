"""
"""
__version__ = "0.2-dev"
from rdflib.py3compat import PY3
if PY3:
    str = str
    bytes = bytes
else:
    _str = unicode
    bytes = str
    str = _str
    
