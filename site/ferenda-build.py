#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys
# use when developing the ferenda source code
import os
parentdir = os.path.normpath(os.getcwd() + os.sep + os.pardir)
sys.path.append(parentdir)

from ferenda import manager
if __name__ == '__main__':
    manager.run(sys.argv[1:])