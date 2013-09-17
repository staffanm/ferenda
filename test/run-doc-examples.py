from __future__ import unicode_literals, print_function
import subprocess
import os

#scripts = ("doc/intro-example.py",
#           "doc/intro-example.sh")
scripts = ("doc/firststeps.docsh",)

failings = []

for script in scripts:
    print("%s ..." % script, end=" ")
    env = os.environ
    if script.endswith(".py"):
        env['PYTHONPATH'] = os.getcwd()
        cmdlines = ["python %s" % script]
        shell = True
    elif script.endswpth(".docsh"): # doctest-like shell script. Is
                                    # there a expect-like tool in
                                    # python?
        cmdlines = []
        shell = True
        for line in open(script):
            if line.startswith("$ "):
                cmdline = line[2:]
                
        
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
        

    
