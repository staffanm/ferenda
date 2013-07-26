#!/usr/bin/env python

import sys
import os
from ferenda import manager


if len(sys.argv) > 1 and sys.argv[1] == '-preflight':
    manager.preflight_check('http://localhost:8080/openrdf-sesame')
else:
    if sys.argv[1] == '-force':
        sys.argv = sys.argv[1:]
        manager.setup(force=True)
    else:
        manager.setup()
