from fabric.api import env, run, local, cd, sudo, settings, hosts
from fabric.contrib.project import rsync_project
from datetime import datetime

# run with eg. fab -H localhost,i7,colo.tomtebo.org -f tools/fabfile.py doccount

macaddress = {
    "nate": "00:25:64:BA:BF:0E",
    "sophie": "00:1A:A0:C3:CE:D1",
    "alec": "00:24:E8:0E:0A:06", # "64:66:B3:04:59:00" is the motherboard interface
    "parker": "78:2B:CB:96:33:53",
    "eliot": "10:C3:7B:6D:D9:50"
}

env.skip_bad_hosts = True

@hosts("nate", "sophie", "alec", "parker", "eliot")
def shutdown():
    with settings(warn_only=True):
        result = run('sudo shutdown -h now')

@hosts("nate", "sophie", "alec", "parker", "eliot")
def wakeup():
    local("echo Waking %s" % env.host)
    local("wakeonlan %s" % macaddress[env.host])

@hosts("nate", "sophie", "alec", "parker", "eliot")
def ping():
    with settings(warn_only=True):
        local("ping -c 1 %s|head -2" % env.host)

def doccount():
    run("curl -s http://localhost:9200/lagen/_count?pretty=true|grep count")

def copy_elastic():
   # remove all old snapshots
    snapshotids = local("curl -s http://localhost:9200/_snapshot/lagen_backup/_all?pretty=true|jq -r '.snapshots[]|.snapshot'", capture=True)
    if snapshotids.strip():
        for snapshot_id in snapshotids.split("\n"):
            assert snapshot_id
            local("curl -XDELETE http://localhost:9200/_snapshot/lagen_backup/%s" % snapshot_id)

    snapshot_id = datetime.now().strftime("%y%m%d-%H%M%S")
    # compute new snapshot id YYYYMMDD-HHMMSS
    snapshot_url = "http://localhost:9200/_snapshot/lagen_backup/%s?wait_for_completion=true" % snapshot_id

    # calculate doccount
    local_doccount = local("curl -s http://localhost:9200/lagen/_count?pretty=true|grep count", capture=True)

    # create a new snapshot with the computed id
    local('curl -f -XPUT \'%s\' -d \'{ "indices": "lagen"}\'' % snapshot_url)

#    snapshot_id = "180706-232553"
#    local_doccount = '  "count" : 3607700,'
    # rsync /tmp/elasticsearch/snapshots from local to target (must be
    # same locally and on target)
    snapshotdir = "/tmp/elasticsearch/snapshot/lagen/"
    # sudo("chown -R staffan:staffan %s" % snapshotdir)
    rsync_project(local_dir=snapshotdir,
                  remote_dir=snapshotdir,
                  delete=True,
                  default_opts="-aziO --no-perms")
    # sudo("chown -R elasticsearch:elasticsearch %s" % snapshotdir)

    # on target, curl POST to restore snapshot (close index beforehand
    # and open it after)
    run("curl -XPOST http://localhost:9200/lagen/_close")
    run("curl -f -XPOST http://localhost:9200/_snapshot/lagen_backup/%s/_restore?wait_for_completion=true" % snapshot_id)
    run("curl -XPOST http://localhost:9200/lagen/_open")

    # on target, calculate doccount and compare
    remote_doccount = run("curl -s http://localhost:9200/lagen/_count?pretty=true|grep count")
    assert local_doccount == remote_doccount

# run from nate with fab -H colo.tomtebo.org -f tools/fabfile.py copy_elastic
def copy_files():
    # NOTE: This includes themes etc in data/rsrc
    rsync_project(local_dir="/home/staffan/wds/ferenda/tng.lagen.nu/data/",
                  # remote_dir="/home/staffan/www/ferenda.lagen.nu/data", # the directory name on colo1
                  remote_dir="/home/staffan/wds/ferenda/tng.lagen.nu/data/", # the dir name on banan.kodapan.se
                  # exclude=["*downloaded*", "*archive*"],
                  exclude=["*archive*"],
                  delete=True,
                  default_opts="-aziO --no-perms")
    # we use quiet=True since this may fail on webserver-owned
    # directories (if the webserver owns the directory, we don't
    # need to chmod it).
    # run("chmod -R go+w /home/staffan/www/ferenda.lagen.nu/data", quiet=True)
    run("chmod -R go+w /home/staffan/wds/ferenda/tng.lagen.nu/data", quiet=True)

    
def git_pull():
    with cd("~/wds/ferenda"):
        run("git pull --rebase")


def upload_rdf():
    # with cd("~/www/ferenda.lagen.nu"):
    with cd("~/wds/ferenda/tng.lagen.nu"):
        run("~/.virtualenvs/frnd35/bin/python ./ferenda-build.py all relate --all --upload")

def deploy():
    copy_elastic()  # requires password b/c of sudo() so we do it first
    git_pull()
    copy_files()
    upload_rdf()
