# ./tools/update-options.py ../lagen/nu/res/options/options.py prop sou ds
#
import sys
import os
import ast
import shutil
import datetime

sys.path.append("..")
from ferenda import util
from ferenda.manager import _load_config, _load_class, _instantiate_class, _enabled_classes

optionsfile = sys.argv[1]
assert os.path.exists(optionsfile)
repos = sys.argv[2:]
assert repos
remove = True  # set this to True manually when you're really really sure
count= {"unreferenced": 0,
        "metadataonly": 0,
        "removed": 0}

# 1 load ferenda.conf to instantiate repos with proper options
configpath = "ferenda.ini"
config = _load_config(configpath)
insts = {}
# 2 for each repo, list all available basefiles
metadataonly = []
for repo in repos:
    cls = _load_class(_enabled_classes("ferenda.ini")[repo])
    inst = _instantiate_class(cls, config)
    insts[repo] = inst
    for basefile in inst.store.list_basefiles_for("parse"):
        #   2.1 if deppath(basefile) doesn't exist or is empty
        deppath = inst.store.dependencies_path(basefile)
        if not os.path.exists(deppath) or os.path.getsize(deppath) == 0:
            print("%s %s: unreferenced document" % (repo, basefile))
            #   2.3 add (repo, basefile) to internal metadataonly-list
            metadataonly.append((repo, basefile))
count["unreferenced"] = len(metadataonly)            

removed = []
if metadataonly:
    # 3 load (eval) options file, then reopen("aw") and seek to end - 1 (don't read last '}')
    options = ast.literal_eval(util.readfile(optionsfile))
    filelen = os.path.getsize(optionsfile)

    # a filter to avoid handling documents newer than 15 years (new
    # documents can't be expected to have inbound refs, but if they
    # haven't got any inbound references after 15 years, they're
    # likely irrelevant)
    filter = lambda p, b: int(b[:4]) < datetime.date.today().year - 15
    
    with open(optionsfile, "r+") as fp:
        fp.seek(filelen - 2)
        assert(fp.read(2) == "}\n")
        fp.seek(filelen - 2)
        # 4 for each entry in metadataonly-list:
        for (repo, basefile) in metadataonly:
        #   4.1 if not present in options, fp.write '(repo,basefile): "metadataonly",\n'
            if (repo, basefile) not in options and filter(repo, basefile):
                print("%s %s: Setting to metadataonly" % (repo, basefile))
                fp.write('    ("%s", "%s"): "metadataonly",\n' % (repo, basefile))
                count["metadataonly"] += 1
                if remove:
                    inst = insts[repo]
                    removed.append((inst, basefile))
        fp.write('}\n')

for (inst, basefile) in removed:
    downloaded_path = inst.store.downloaded_path(basefile)
    storage_policy = inst.store.storage_policy
    if not os.path.exists(downloaded_path):
        # maybe the reason is that this is a compositerepo?
        # FIXME: maybe CompositeStore.downloaded_path and
        # friends should do this transparently?
        if hasattr(inst, 'get_preferred_instances'):
            subinsts = list(inst.get_preferred_instances(basefile))
            if not subinsts:
                print("%s %s: WARNING: no subinst handles this basefile" % (inst.alias, basefile))
                continue
            subinst = subinsts[0]
            downloaded_path = subinst.store.downloaded_path(basefile)
            storage_policy = subinst.store.storage_policy
    assert(os.path.exists(downloaded_path))
    print("%s %s: removing %s" % (inst.alias, basefile, downloaded_path))
    count["removed"] += 1
    if storage_policy == "dir":
        shutil.rmtree(os.path.dirname(downloaded_path))
        # print("shutil.rmtree(%s)" % os.path.dirname(downloaded_path))
    else:
        util.robust_remove(downloaded_path)
        # print("util.robust_remove(%s)" % downloaded_path)

print("%(unreferenced)s unreferenced docs, %(metadataonly)s set to 'metadataonly', %(removed)s removed" % count)

