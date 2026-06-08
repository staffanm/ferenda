#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
# use when developing the ferenda source code
# import os
# sys.path.append(os.path.normpath(os.getcwd() + os.sep + os.pardir))

from ferenda import manager
if __name__ == '__main__':
    manager.run(sys.argv[1:])


