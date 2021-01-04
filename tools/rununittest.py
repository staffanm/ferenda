# This is a tiny tiny wrapper for running unittests similarly to
# "python -m unittest", but avoids http://bugs.python.org/issue10845
# which hands the multiprocessing tests on windows/py2
from unittest.main import main
import sys
from pprint import pprint
if __name__ == '__main__':
    sys.path.append(".")
    # pprint(sys.path)
    main(module=None)
