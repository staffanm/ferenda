#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os
import sys
sys.path.append("..")

try:
    from ferenda.manager import make_wsgi_app

    # FIXME: should we chdir to os.path.dirname(__file__) instead?
    inifile = os.path.join(os.path.dirname(__file__), "ferenda.ini")
    application = make_wsgi_app(inifile=inifile)
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
