from fabric.api import run, local, cd, sudo
from fabric.contrib.project import rsync_project
from datetime import datetime

# run with eg. fab -H localhost,i7,colo.tomtebo.org -f tools/fabfile.py doccount

def doccount():
    run("curl -s http://localhost:9200/lagen/_count?pretty=true|grep count")

# run from i7 with fab -H colo.tomtebo.org -f tools/fabfile.py copy_elastic
def copy_elastic():
    snapshot_id = datetime.now().strftime("%y%m%d-%H%M%S")
    # compute new snapshot id YYYYMMDD-HHMMSS
    snapshot_url = "http://localhost:9200/_snapshot/lagen_backup/%s?wait_for_completion=true" % snapshot_id

    # calculate doccount
    local_doccount = local("curl -s http://localhost:9200/lagen/_count?pretty=true|grep count", capture=True)

    # create a new snapshot with the computed id
    local('curl -f -XPUT \'%s\' -d \'{ "indices": "lagen"}\'' % snapshot_url)
    
    # rsync /tmp/elasticsearch/snapshots from local to target (must be
    # same locally and on target)
    snapshotdir = "/tmp/elasticsearch/snapshot/lagen/"
    sudo("chown -R staffan:staffan %s" % snapshotdir)
    rsync_project(local_dir=snapshotdir,
                  remote_dir=snapshotdir,
                  delete=True,
                  default_opts="-azi")
    sudo("chown -R elasticsearch:elasticsearch %s" % snapshotdir)

    # on target, curl POST to restore snapshot (close index beforehand
    # and open it after)
    run("curl -XPOST http://localhost:9200/lagen/_close")
    run("curl -f -XPOST http://localhost:9200/_snapshot/lagen_backup/%s/_restore?wait_for_completion=true" % snapshot_id)
    run("curl -XPOST http://localhost:9200/lagen/_open")

    # on target, calculate doccount and compare
    remote_doccount = run("curl -s http://localhost:9200/lagen/_count?pretty=true|grep count")
    assert local_doccount == remote_doccount


# run from i7 with fab -H colo.tomtebo.org -f tools/fabfile.py copy_elastic
def copy_files():
    # NOTE: This includes themes etc in data/rsrc
    rsync_project(local_dir="/mnt/diskstation-home/staffan/wds/ferenda/tng.lagen.nu/data/",
                  remote_dir="/home/staffan/www/ferenda.lagen.nu/data",
                  exclude=["*downloaded*", "*archive*"],
                  delete=True,
                  default_opts="-azi")


def git_pull():
    with cd("~/wds/ferenda"):
        run("git pull --rebase")


def upload_rdf():
    with cd("~/www/ferenda.lagen.nu"):
        run("~/.virtualenvs/lagen.nu/bin/python ./ferenda-build.py all relate --all --upload")


def deploy():
    copy_elastic()  # requires password b/c of sudo() so we do it first
    git_pull()
    copy_files()
    upload_rdf()
