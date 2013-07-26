#!/usr/bin/env python
import sys, os
sys.path.append(os.path.normpath(os.getcwd()+os.sep+os.pardir))

from ferenda import manager
manager.run(sys.argv[1:])

