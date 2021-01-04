#!/usr/share/.virtualenv/bin/python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys
import os
sys.path.append(os.path.normpath(os.getcwd() + os.sep + os.pardir))

from ferenda import manager
if __name__ == '__main__':
    manager.run(sys.argv[1:])
