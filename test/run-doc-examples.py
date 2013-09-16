from __future__ import unicode_literals, print_function
import subprocess
import os

# scripts = ("doc/fail.sh", "doc/win.sh")
# pyscripts = ("doc/intro-example.py",)
scripts = ("doc/intro-example.sh",)

failings = []

for script in scripts:
    print("%s ..." % script, end=" ")
    env = os.environ
    if script.endswith(".py"):
        env['PYTHONPATH'] = os.getcwd()
        cmdline = "python %s" % script
        shell = True
    else:
        cmdline = script
        shell = False
    process = subprocess.Popen(cmdline,
                               shell=shell,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT,
                               env=env)
    out,err = process.communicate()
    retcode = process.poll()
    if retcode:
        print("FAIL")
        failings.append({'script': script,
                         'returncode': retcode,
                         'output': out})
    else:
        print("ok")

for failing in failings:
        print("="*60)
        print("ERROR: %s (return code %s)" % (failing['script'],
                                              failing['returncode']))
        print("-"*60)
        print(failing['output'])
        print("-"*60)
        

    
