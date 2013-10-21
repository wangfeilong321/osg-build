"""Fetch sources from the upstream cache and combine them with sources from
the osg/ dir in the package.

"""

# pylint: disable=W0614
import fnmatch
import logging
import glob
import re
import os
import shutil
import urllib2

from osgbuild.constants import *
from osgbuild.error import Error, GlobNotFoundError
from osgbuild import utils

log = logging.getLogger('osgbuild')
log.propagate = False

def process_dot_source(cache_prefix, sfilename, destdir):
    """Read a .source file, fetch any files mentioned in it from the
    cache.

    """
    utils.safe_makedirs(destdir)
    downloaded = []
    try:
        sfile = open(sfilename, 'r')
        for lineno, line in enumerate(sfile):
            line = line.strip()
            if line.startswith('#'):
                continue
            if line == '':
                continue
            basename = os.path.basename(line)
            if line.startswith('/'):
                uri = "file://" + line
                log.warning(
                    "An absolute path has been given in %s line %d. "
                    "It is recommended to use only paths relative to %s"
                    "in your source files.", sfilename, lineno+1,
                    cache_prefix)
            elif not re.match(r'/|\w+://', line): # relative path
                uri = os.path.join(cache_prefix, line)
            else:
                uri = line

            log.info('Retrieving ' + uri)
            try:
                handle = urllib2.urlopen(uri)
            except urllib2.URLError, err:
                raise Error("Unable to download %s\n%s" % (uri, str(err)))
            filename = os.path.join(destdir, basename)
            try:
                desthandle = open(filename, 'w')
                desthandle.write(handle.read())
            except EnvironmentError, err:
                raise Error("Unable to save downloaded file to %s\n%s" % (filename, str(err)))
            downloaded.append(filename)
    finally:
        sfile.close()

    return downloaded
# end of process_dot_source()


def full_extract(unpacked_dir, archives_downloaded, destdir):
    """Extract downloaded archives plus archives inside downloaded SRPMs"""
    archives_in_srpm = []
    if os.path.isdir(unpacked_dir):
        for fname in glob.glob(os.path.join(unpacked_dir, '*')):
            if os.path.isfile(fname):
                archives_in_srpm.append(os.path.abspath(fname))
    utils.safe_makedirs(destdir)
    old_dir = os.getcwd()
    os.chdir(destdir)
    for fname in archives_downloaded + archives_in_srpm:
        log.info("Extracting " + fname)
        utils.super_unpack(fname)
    os.chdir(old_dir)
    log.info('Extracted files to ' + destdir)


def extract_srpms(srpms_downloaded, destdir):
    """Extract SRPMs to destdir"""
    abs_srpms_downloaded = [os.path.abspath(x) for x in srpms_downloaded]
    utils.safe_makedirs(destdir)
    old_dir = os.getcwd()
    os.chdir(destdir)
    for srpm in abs_srpms_downloaded:
        log.info("Unpacking SRPM " + srpm)
        utils.super_unpack(srpm)
    os.chdir(old_dir)


def copy_with_filter(files_list, destdir):
    """Copy files in files_list to destdir, skipping backup files and
    directories.

    """
    for fname in files_list:
        base = os.path.basename(fname)
        if (base in [WD_RESULTS,
                     WD_PREBUILD,
                     WD_UNPACKED,
                     WD_UNPACKED_TARBALL] or
                base.endswith('~') or
                os.path.isdir(fname)):
            log.debug("Skipping file " + fname)
        else:
            log.debug("Copying file " + fname)
            shutil.copy(fname, destdir)


def fetch(package_dir,
          destdir=None,
          cache_prefix=WEB_CACHE_PREFIX,
          unpacked_dir=None,
          want_full_extract=False,
          unpacked_tarball_dir=None):
    """Process *.source files in upstream/ directory, downloading upstream
    sources mentioned in them from the software cache. Unpack SRPMs if
    there are any. Override upstream files with those in the osg/
    directory. Return the path to the downloaded spec file. 

    """
    if destdir is None:
        destdir = package_dir
    if unpacked_dir is None:
        unpacked_dir = destdir
    if unpacked_tarball_dir is None:
        unpacked_tarball_dir = destdir

    abs_package_dir = os.path.abspath(package_dir)

    upstream_dir = os.path.join(abs_package_dir, 'upstream')
    osg_dir = os.path.join(abs_package_dir, 'osg')

    # Process upstream/*.source files
    dot_sources = glob.glob(os.path.join(upstream_dir, '*.source'))
    downloaded = []
    for src in dot_sources:
        log.debug('Processing .source file %s', src)
        for fname in process_dot_source(cache_prefix, src, destdir):
            downloaded.append(os.path.abspath(fname))

    # Process downloaded SRPMs
    srpms = fnmatch.filter(downloaded, '*.src.rpm')
    if srpms:
        extract_srpms(srpms, unpacked_dir)
    if unpacked_dir != destdir:
        for f in glob.glob(os.path.join(unpacked_dir, '*')):
            log.debug('Copying unpacked file ' + f)
            shutil.copy(f, destdir)

    # Copy non *.source files in upstream
    other_sources = [x for x in glob.glob(os.path.join(upstream_dir, '*'))
                     if (not fnmatch.fnmatch(x, '*.source')
                         and os.path.isfile(x))]
    copy_with_filter(other_sources, destdir)

    # Extract any archives we downloaded plus any archives in the SRPM
    if want_full_extract:
        full_extract(unpacked_dir, downloaded, unpacked_tarball_dir)

    # Override downloaded files with what's in osg/
    copy_with_filter(glob.glob(os.path.join(osg_dir, '*')),
                     destdir)

    # Return list of spec files
    spec_glob = os.path.join(destdir, '*.spec')
    spec_filenames = glob.glob(spec_glob)
    if not spec_filenames:
        raise GlobNotFoundError(spec_glob)
    if len(spec_filenames) > 1:
        log.warning("Multiple spec files found; using %r", spec_filenames[0])

    return spec_filenames[0]
# end of fetch


