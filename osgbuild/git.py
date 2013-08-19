"""Helper functions for a git build."""
import re
import os

from osgbuild.error import Error, GitError
from osgbuild import utils

import osgbuild.constants as constants

def is_git(package_dir):
    """Determine whether a given directory is part of a git repo."""
    pwd = os.getcwd()
    try:
        os.chdir(package_dir)
        command = ["git", "status", "--porcelain"]
        err = utils.sbacktick(command, clocale=True, err2out=True)[1]
        if err:
            return False
    finally:
        os.chdir(pwd)
    return True


def get_branch(package_dir):
    """Return the current git branch for a given directory."""
    top_dir = os.path.split(os.path.abspath(package_dir))[0]
    command = ["git", "--work-tree", top_dir, "--git-dir", os.path.join(top_dir, ".git"), "branch"]
    out, err = utils.sbacktick(command, clocale=True, err2out=True)
    if err:
        raise GitError("Exit code %d getting git branch for directory %s.  Output:\n%s" % (err, package_dir, out))
    out = out.strip()
    if not out:
        raise GitError("'git branch' returned no output.")
    return out.split()[-1]


def get_known_remote(package_dir):
    """Return the first remote in the current directory's list of remotes which
       is on osg-build's configured whitelist of remotes."""
    top_dir = os.path.split(os.path.abspath(package_dir))[0]
    command = ["git", "--work-tree", top_dir, "--git-dir", os.path.join(top_dir, ".git"), "remote", "-v"]
    out, err = utils.sbacktick(command, clocale=True, err2out=True)
    if err:
        raise GitError("Exit code %d getting git status for directory %s. Output:\n%s" % (err, package_dir, out))
    for line in out.splitlines():
        info = line.strip().split()
        if len(info) != 3:
            continue
        if info[2] != '(fetch)':
            continue
        if info[1] in constants.KNOWN_GIT_REMOTES:
            return info[0]
    raise GitError("OSG remote not found for directory %s; are remotes configurated correctly?" % package_dir)


def get_fetch_url(package_dir, remote):
    """Return a fetch url
       is on osg-build's configured whitelist of remotes."""
    top_dir = os.path.split(os.path.abspath(package_dir))[0]
    command = ["git", "--work-tree", top_dir, "--git-dir", os.path.join(top_dir, ".git"), "remote", "-v"]
    out, err = utils.sbacktick(command, clocale=True, err2out=True)
    if err:
        raise GitError("Exit code %d getting git status for directory %s. Output:\n%s" % (err, package_dir, out))
    for line in out.splitlines():
        info = line.strip().split()
        if len(info) != 3:
            continue
        if info[2] != '(fetch)':
            continue
        if info[0] == remote:
            return constants.GIT_REMOTE_MAPS.setdefault(info[1], info[1])

    raise GitError("Remote URL not found for remote %s in directory %s; are remotes configurated correctly?" % (remote, package_dir))

def get_current_branch_remote(package_dir):
    """Return the configured remote for the current branch."""
    branch = get_branch(package_dir)

    top_dir = os.path.split(os.path.abspath(package_dir))[0]
    command = ["git", "--work-tree", top_dir, "--git-dir", os.path.join(top_dir, ".git"),
               "config", "branch.%s.remote" % branch]
    out, err = utils.sbacktick(command, clocale=True, err2out=True)
    if err:
        raise GitError("Exit code %d getting git branch %s remote for directory '%s'. Output:\n%s" % \
                       (err, branch, package_dir, out))

    return out.strip()


def is_uncommitted(package_dir):
    """Return True if there are uncommitted changes or files in the git working dir."""
    top_dir = os.path.split(os.path.abspath(package_dir))[0]
    command = ["git", "--work-tree", top_dir, "--git-dir", os.path.join(top_dir, ".git"), "status", "--porcelain"]
    out, err = utils.sbacktick(command, clocale=True, err2out=True)
    if err:
        raise GitError("Exit code %d getting git status for directory %s. Output:\n%s" % (err, package_dir, out))
    if out:
        print "The following uncommitted changes exist:"
        print out
        print "Please commit these first."
        return True

    remote = get_current_branch_remote(package_dir)

    branch = get_branch(package_dir)
    branch_ref = "refs/heads/%s" % branch
    origin_ref = "refs/remotes/%s/%s" % (remote, branch)

    command = ["git", "--work-tree", top_dir, "--git-dir", os.path.join(top_dir, ".git"), "show-ref"]
    out, err = utils.sbacktick(command, clocale=True, err2out=True)
    if err:
        raise GitError("Exit code %d getting git references for directory %s.  Output:\n%s", (err, package_dir, out))
    branch_hash = ''
    origin_hash = ''
    for line in out.splitlines():
        info = line.split()
        if len(info) != 2:
            continue
        if info[1] == branch_ref:
            branch_hash = info[0]
        if info[1] == origin_ref:
            origin_hash = info[0]

    if not branch_hash and not origin_hash:
        raise GitError("Could not find either local or remote hash for directory %s." % package_dir)
    if branch_hash != origin_hash:
        raise GitError("Local hash (%s) does not match remote hash "
            "(%s) for directory %s.  Perhaps you need to perform 'git push'?" % \
            (branch_hash, origin_hash, package_dir))

    return False


def is_outdated(package_dir):
    """Return True if the package has been changed since the revision in the
    local git repo.

    """
    remote = get_current_branch_remote(package_dir)
    branch = get_branch(package_dir)
    branch_ref = "refs/heads/%s" % branch
    branch_hash = ''

    top_dir = os.path.split(os.path.abspath(package_dir))[0]
    command = ["git", "--work-tree", top_dir, "--git-dir", os.path.join(top_dir, ".git"), "show-ref"]
    out, err = utils.sbacktick(command, clocale=True, err2out=True)
    if err:
        raise GitError("Exit code %d getting git references for directory %s.  Output:\n%s", (err, package_dir, out))
    for line in out.splitlines():
        info = line.strip().split()
        if len(info) != 2:
            continue
        if info[1] == branch_ref:
            branch_hash = info[0]
            break
    if not branch_hash:
        raise GitError("Unable to determine local branch's hash.")

    out, err = utils.sbacktick(["git", "--work-tree", top_dir, "--git-dir", os.path.join(top_dir, ".git"),
                                "ls-remote", "--heads", remote])
    if err:
        raise GitError("Exit code %d getting remote git status for directory %s. Output:\n%s" % (err, package_dir, out))

    remote_hash = ''
    for line in out.splitlines():
        info = line.strip().split()
        if len(info) != 2:
            continue
        if info[1] == branch_ref:
            remote_hash = info[0]
            break
    if not remote_hash:
        raise GitError("Unable to determine remote branch's hash.")

    if remote_hash == branch_hash:
        return False

    print "Remote hash (%s) does not match local hash (%s) for branch %s." % (remote_hash, branch_hash, branch)
    return True


def verify_working_dir(pkg):
    """Verify if a package working directory has uncommitted changes or is
    outdated and ask the user what to do. Return True if it's ok to continue.

    """
    if is_uncommitted(pkg):
        if not utils.ask_yn("""\
Package working directory %s has uncommitted changes that will not be included
in the git build.
Continue (yes/no)?""" % pkg):
            return False
    if is_outdated(pkg):
        if not utils.ask_yn("""\
Package working directory %s is out of date and its contents may not reflect
what will be built.
Continue (yes/no)?""" % pkg):
            return False
    return True


def verify_package_dir(package_dir):
    """Check if package_dir points to a valid package dir (i.e. contains
    at least an osg/ dir or an upstream/ dir) and is in a git repo.
    """
    top_dir = os.path.split(os.path.abspath(package_dir))[0]
    command = ["git", "--work-tree", top_dir, "--git-dir", os.path.join(top_dir, ".git"),
               "rev-parse", "--show-toplevel"]
    out, err = utils.sbacktick(command, clocale=True, err2out=True)
    if err:
        raise GitError("Exit code %d getting git top-level directory of %s. Output:\n%s" % (err, package_dir, out))
    if top_dir != out.strip():
        raise GitError("Specified package directory (%s) is not a top-level directory in the git repo (%s)." % \
                       (package_dir, top_dir))
    command = ["git", "--work-tree", top_dir, "--git-dir", os.path.join(top_dir, ".git"), "ls-files", "osg", "upstream"]
    out, err = utils.sbacktick(command, clocale=True, err2out=True)
    if err:
        raise GitError("Exit code %d getting git subdirectories of %s. Output:\n%s" % (err, package_dir, out))
    for line in out.split("\n"):
        if line.startswith('osg/') or line.startswith('upstream/'):
            return True
    return False


def verify_git_svn_commit(package_dir):
    """Verify the last commit in the git repo actually came from git-svn."""
    top_dir = os.path.split(os.path.abspath(package_dir))[0]
    command = ["git", "--work-tree", top_dir, "--git-dir", os.path.join(top_dir, ".git"), "log", "-n", "1"]
    out, err = utils.sbacktick(command, clocale=True, err2out=True)
    if err:
        raise GitError("Exit code %d getting git log for directory %s. Output:\n%s" % (err, package_dir, out))

    for line in out.splitlines():
        if line.find("git-svn-id:") >= 0:
            return

    raise GitError("Last git commit not from SVN - possible inconsistency between git and SVN!")


def verify_correct_remote(package_dir):
    """Verify the current branch remote is one of the known remotes."""
    remote = get_current_branch_remote(package_dir)
    known_remote = get_known_remote(package_dir)
    if remote != known_remote:
        raise GitError("Remote %s for directory %s is not an officially known remote." % (remote, package_dir))


def verify_correct_branch(package_dir, buildopts):
    """Check that the user is not trying to build from trunk into upcoming, or
    vice versa.
    """
    branch = get_branch(package_dir)
    remote = get_known_remote(package_dir)

    verify_correct_remote(package_dir)

    # We only have branching rules for OSG repos
    if remote != constants.OSG_REMOTE:
        return

    verify_git_svn_commit(package_dir)

    for dver in buildopts['enabled_dvers']:
        target = buildopts['targetopts_by_dver'][dver]['koji_target']
        if target.endswith('osg-upcoming'):
            if branch.find("master") >= 0:
                raise Error("""\
Error: Incorrect branch for koji build
Not allowed to build into the upcoming targets from
master branch!  You must switch branches or build targets.""")
        elif target.find("osg") >= 0:
            if branch.find("upcoming"):
                raise Error("""\
Error: Incorrect branch for koji build
Only allowed to build packages from the upcoming branch
into the upcoming targets.  Either switch the branch to master,
 or pass the --upcoming flag.""")


def koji(package_dir, koji_obj, buildopts):
    """koji task with a git build."""
    package_dir = os.path.abspath(package_dir)
    verify_package_dir(package_dir)
    package_name = os.path.basename(package_dir)
    if not re.match("\w+", package_name): # sanity check
        raise Error("Package directory '%s' gives invalid package name '%s'" % (package_dir, package_name))
    if not buildopts.get('scratch'):
        koji_obj.add_pkg(package_name)

    remote = get_fetch_url(package_dir, get_known_remote(package_dir))
    top_dir = os.path.split(os.path.abspath(package_dir))[0]
    command = ["git", "--work-tree", top_dir, "--git-dir", os.path.join(top_dir, ".git"),
               "log", "-1", "--pretty=format:%H"]
    out, err = utils.sbacktick(command, clocale=True, err2out=True)
    if err:
        raise GitError("Exit code %d getting git hash for directory %s. Output:\n%s" % (err, package_dir, out))
    rev = out.strip()
 
    return koji_obj.build_git(remote,
                              rev,
                              package_name)


