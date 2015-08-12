"""Global constants for osg-build"""
import os
import sys

WD_RESULTS = '_build_results'
WD_PREBUILD = '_final_srpm_contents'
WD_UNPACKED = '_upstream_srpm_contents'
WD_UNPACKED_TARBALL = '_upstream_tarball_contents'
WD_QUILT = '_quilt'
AFS_CACHE_PATH = '/p/vdt/public/html/upstream'
AFS_CACHE_PREFIX = 'file://' + AFS_CACHE_PATH
WEB_CACHE_PREFIX = 'http://vdt.cs.wisc.edu/upstream'
DEFAULT_CONFIG_FILE = os.path.expanduser("~/.osg-build.ini")
ALT_DEFAULT_CONFIG_FILE = os.path.expanduser("~/.vdt-build.ini")

KOJI_USER_CONFIG_DIR = os.path.expanduser("~/.koji")
OSG_KOJI_USER_CONFIG_DIR = os.path.expanduser("~/.osg-koji")
KOJI_CLIENT_CERT = os.path.join(KOJI_USER_CONFIG_DIR, "client.crt")

KOJI_CONF = "osg-koji-site.conf"
OLD_KOJI_CONF = "osg-koji.conf"
DATA_DIR = "/usr/share/osg-build"

KOJI_HUB = "http://koji-hub.batlab.org"
HTTPS_KOJI_HUB = "https://koji-hub.batlab.org"

DATA_FILE_SEARCH_PATH = [sys.path[0],
                         os.path.join(sys.path[0], "data"),
                         DATA_DIR]

SVN_ROOT = "https://vdt.cs.wisc.edu/svn"
SVN_REDHAT_PATH = "/native/redhat"

SVN_RESTRICTED_BRANCHES = {
    r'^trunk$'                             : 'main',
    r'^branches/upcoming$'                 : 'upcoming',
    r'^branches/osg-internal$'             : 'internal',
    r'^branches/osg-(?P<osgver>\d+\.\d+)$' : 'versioned'}
KOJI_RESTRICTED_TARGETS = {
    r'^osg-(el\d+)$'                       : 'main',
    r'^osg-upcoming-(el\d+)$'              : 'upcoming',
    r'^osg-(el\d+)-internal$'              : 'internal',
    r'^osg-(?P<osgver>\d+\.\d+)-(el\d+)$'  : 'versioned'}
GIT_RESTRICTED_BRANCHES = {
    r'^(\w*/)?master$'                     : 'main',
    r'^(\w*/)?upcoming$'                   : 'upcoming',
    r'^(\w*/)?internal$'                   : 'internal',
    r'^(\w*/)?osg-(?P<osgver>\d+\.\d+)$'   : 'versioned'}

CSL_KOJI_DIR = "/p/vdt/workspace/koji-1.6.0"

OSG_REMOTE = 'https://github.com/opensciencegrid/Software-Redhat.git'
HCC_REMOTE = 'https://github.com/unlhcc/hcc-packaging.git'
HCC_AUTH_REMOTE = 'git@github.com:unlhcc/hcc-packaging.git'

KNOWN_GIT_REMOTES = [HCC_REMOTE,
                     HCC_AUTH_REMOTE,
                     OSG_REMOTE]
# Map the authenticated URL to an anonymous checkout URL.
GIT_REMOTE_MAPS = {HCC_AUTH_REMOTE: HCC_REMOTE}

DEFAULT_BUILDOPTS_COMMON = {
    'autoclean': True,
    'background': False,
    'cache_prefix': 'AUTO',
    'dry_run': False,
    'full_extract': False,
    'getfiles': False,
    'koji_backend': None,
    'kojilogin': None,
    'koji_wrapper': True,
    'mock_clean': True,
    'mock_config': 'AUTO',
    'mock_config_from_koji': None,
    'no_wait': False,
    'redhat_releases': None,
    'regen_repos': False,
    'repo': 'osg',
    'scratch': False,
    'vcs': None,
    'target_arch': None,
    'working_directory': '.',
}

DEFAULT_BUILDOPTS_BY_DVER = {
    '5': {
        'distro_tag': 'osg.el5',
        'koji_tag': None,
        'koji_target': None,
        'redhat_release': '5',
        'repo': 'osg',
    },
    '6': {
        'distro_tag': 'osg.el6',
        'koji_tag': None,
        'koji_target': None,
        'redhat_release': '6',
        'repo': 'osg',
    },
    '7': {
        'distro_tag': 'osg.el7',
        'koji_tag': None,
        'koji_target': None,
        'redhat_release': '7',
        'repo': 'osg',
    }
}

REPO_HINTS_STATIC = {
    'osg': {'target': 'osg-el%s', 'tag': 'osg-el%s'},
    'upcoming': {'target': 'osg-upcoming-el%s', 'tag': 'osg-el%s'},
    'internal': {'target': 'osg-el%s-internal', 'tag': 'osg-el%s'},
    'hcc': {'target': 'hcc-el%s', 'tag': 'hcc-el%s'},
    'uscms': {'target': 'uscms-el%s', 'tag': 'uscms-el%s'},
    'condor': {'target': 'condor-el%s', 'tag': 'condor-el%s'},
    'perfsonar': {'target': 'perfsonar-el%s', 'tag': 'perfsonar-el%s'},
}

DEFAULT_DVERS = ['6', '7']
DEFAULT_DVERS_BY_REPO = {
    '3.2': ['5', '6'],
    'osg-3.2': ['5', '6'],
    '3.3': ['6', '7'],
    'osg-3.3': ['6', '7'],
    'internal': ['5', '6', '7'],
}
DVERS = DEFAULT_BUILDOPTS_BY_DVER.keys()

BUGREPORT_EMAIL = "osg-software@opensciencegrid.org"

BACKGROUND_THRESHOLD = 5

