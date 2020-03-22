#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os
import sys
sys.path.append("..")

try:
    from ferenda.manager import make_wsgi_app, find_config_file, load_config
    application = make_wsgi_app(load_config(find_config_file()))
except ImportError as e:
    exception_data = str(e)

    def application(environ, start_response):
        msg = """500 Internal Server Error: %s

        sys.path: %r
        os.getcwd(): %s""" % (exception_data, sys.path, os.getcwd())
        msg = msg.encode('ascii')
        start_response("500 Internal Server Error", [
            ("Content-Type", "text/plain"),
            ("Content-Length", str(len(msg)))
        ])
        return iter([msg])
