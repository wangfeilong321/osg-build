"""osg build script

Wishlist:
* Better message printing. Should distinguish between when to use logging.*
  versus print.
* Better support for multiple packages. If a single build runs into an error,
  the user should be given a choice to skip that package. Some kind of report
  afterward showing successful/failed packages should be printed.
  Should be some sanity checks on all packages before any work on any of them
  is done.
* Better exceptions. Should review where they should _really_ be used.
* Have unit tests for every chunk of code I change.
"""
# pylint: disable=W0614,W0602,C0103


# TODO Shouldn't need koji access for 'rpmbuild', but currently does since it
# gets the values for the --repo arg -- which is only used for koji builds.
# Make it so.
# TODO In some places, a dver is 'el5', in others, just '5'. Fix that -- use el5.
import logging
from optparse import OptionGroup, OptionParser, OptionValueError
import re
import os
import sys
import tempfile
import ConfigParser

from osgbuild.constants import *
from osgbuild.error import UsageError, KojiError
from osgbuild import kojiinter
from osgbuild import mock
from osgbuild import srpm
from osgbuild import svn
from osgbuild import git
from osgbuild import utils

__version__ = '@VERSION@'

# logging. Can't use root logger because its loglevel can't be changed once set
log = logging.getLogger('osgbuild')
log.setLevel(logging.INFO)
log_consolehandler = logging.StreamHandler()
log_consolehandler.setLevel(logging.INFO)
log_formatter = logging.Formatter("%(levelname)s:osg-build:%(message)s")
log_consolehandler.setFormatter(log_formatter)
log.addHandler(log_consolehandler)
log.propagate = False

#-------------------------------------------------------------------------------
# Main function
#-------------------------------------------------------------------------------
def main(argv):
    """Main function."""

    buildopts, package_dirs, task = init(argv)
    koji_obj = None
    mock_obj = None
    vcs = None

    if task in ['koji', 'mock']:
        for dver in buildopts['enabled_dvers']:
            targetopts = buildopts['targetopts_by_dver'][dver]
            targetopts['koji_target'] = targetopts['koji_target'] or target_for_repo_hint(targetopts['repo'], dver)
            targetopts['koji_tag'] = targetopts['koji_tag'] or tag_for_repo_hint(targetopts['repo'], dver)
    # checks
    if task == 'koji' and buildopts['vcs']:
        # verify working dirs
        for pkg in package_dirs:
            if git.is_git(pkg):
                vcs = git
            else:
                vcs = svn
            if not vcs.verify_working_dir(pkg):
                print "Exiting"
                return 1
            vcs.verify_correct_branch(pkg, buildopts)
    else:
        # verify package dirs
        for pkg in package_dirs:
            if not os.path.isdir(pkg):
                raise UsageError(pkg + " isn't a directory!")
            if ((not os.path.isdir(os.path.join(pkg, "osg"))) and
                    (not os.path.isdir(os.path.join(pkg, "upstream")))):
                raise UsageError(pkg +
                    " isn't a package directory "
                    "(must have either osg/ or upstream/ dirs or both)")

    if (task == 'koji' and not buildopts['scratch'] and not buildopts['vcs']):
        raise UsageError("Non-scratch Koji builds must be from SVN!")

    if task == 'koji' and len(package_dirs) >= BACKGROUND_THRESHOLD:
        buildopts['background'] = True

    # main loop
    # HACK
    task_ids = []
    task_ids_by_results_dir = dict()
    for pkg in package_dirs:
        for dver in buildopts['enabled_dvers']:
            dver_buildopts = buildopts.copy()
            dver_buildopts.update(buildopts['targetopts_by_dver'][dver])

            mock_obj = None
            koji_obj = None
            if task == 'koji':
                koji_obj = kojiinter.KojiInter(dver_buildopts)
            if task == 'mock':
                if dver_buildopts['mock_config_from_koji']:
                    # HACK: We don't want to log in to koji just to get a mock config
                    dver_buildopts_ = dver_buildopts.copy()
                    dver_buildopts_['dry_run'] = True
                    koji_obj = kojiinter.KojiInter(dver_buildopts_)
                mock_obj = mock.Mock(dver_buildopts, koji_obj)

            if buildopts['vcs'] and task == 'koji':
                task_ids.append(vcs.koji(pkg, koji_obj, dver_buildopts))
            else:
                builder = srpm.SRPMBuild(pkg,
                                         dver_buildopts,
                                         mock_obj=mock_obj,
                                         koji_obj=koji_obj)
                builder.maybe_autoclean()
                method = getattr(builder, task)
                if task == 'koji':
                    task_ids_by_results_dir.setdefault(builder.results_dir, [])
                    task_id = method()
                    task_ids.append(task_id)
                    task_ids_by_results_dir[builder.results_dir].append(task_id)
                else:
                    method()
    # end of main loop
    # HACK
    task_ids = filter(None, task_ids)
    if kojiinter.KojiInter.backend and task_ids:
        print "Koji task ids are:", task_ids
        for tid in task_ids:
            print HTTPS_KOJI_HUB + "/koji/taskinfo?taskID=" + str(tid)
        if not buildopts['no_wait']:
            ret = kojiinter.KojiInter.backend.watch_tasks(task_ids)
            # TODO This is not implemented for the KojiShellInter backend
            # Not implemented for SVN builds since results_dir is undefined for those
            if buildopts['getfiles']:
                if buildopts['vcs']:
                    log.warning("--getfiles is only for SRPM builds")
                elif not isinstance(kojiinter.KojiInter.backend, kojiinter.KojiLibInter):
                    log.warning("--getfiles is only implemented on the KojiLib backend")
                else:
                    for destdir, tids in task_ids_by_results_dir.iteritems():
                        kojiinter.KojiInter.backend.download_results(tids, destdir)
                        log.info("Results and logs downloaded to %s", destdir)
            try:
                return int(ret)
            except (TypeError, ValueError):
                pass
        else:
            if buildopts['getfiles']:
                log.warning("Cannot use both --getfiles and --nowait")

    return 0
# end of main()


def init(argv):
    """Initialization. Get build options and packages.

    """
    options, args = parse_cmdline_args(argv)

    if options.version:
        print_version_and_exit()
    # if loglevel is passed on the command line, make it take into effect
    # immediately so we see debug messages in get_buildopts
    if options.loglevel:
        set_loglevel(options.loglevel)

    task = get_task(args)
    buildopts = get_buildopts(options, task)
    set_loglevel(buildopts.get('loglevel', 'INFO'))

    package_dirs = args[1:]
    if not package_dirs:
        guess = guess_pkg_dir(os.getcwd())
        log.info("Package dir not specified, using %s", guess)
        package_dirs.append(guess)

    return (buildopts, package_dirs, task)
# end of init()


__koji_targets_cache = None
def valid_koji_targets():
    """Return a list of valid koji targets (to be used for building up the list
    of values for the --repo argument).
    """
    global __koji_targets_cache # pylint:disable=W0603
    if not __koji_targets_cache:
        # HACK
        try:
            koji_obj = kojiinter.KojiShellInter(dry_run=True, koji_wrapper=True)
            __koji_targets_cache = koji_obj.get_targets()
        except KojiError, err:
            log.warning(str(err))
    return __koji_targets_cache

def valid_dvers(targets):
    """Return a list of valid dvers as derived from a list of koji targets.
    targets: a list of koji targets returned by valid_koji_targets"""
    dvers = set()
    for target in targets:
        dver = get_dver_from_string(target)
        if dver:
            dvers.add(dver)
    return sorted(dvers)

__repo_hints_cache = None
def repo_hints(targets):
    """Return the valid arguments for --repo and the target and tag hints
    associated with them.  Most of the repo_hints are already specified in
    REPO_HINTS_STATIC, but we need to determine which 'versioned' osg targets
    (e.g. osg-3.1-el5) exist.

    'targets' is a list of koji targets and can be obtained from
    valid_koji_targets().

    """
    global __repo_hints_cache # pylint:disable=W0603

    if not __repo_hints_cache:
        __repo_hints_cache = REPO_HINTS_STATIC.copy()
        if targets:
            for target in targets:
                osg_match = re.match(r'osg-(\d+\.\d+)-el\d+', target)
                if osg_match:
                    osgver = osg_match.group(1)
                    __repo_hints_cache[osgver] = __repo_hints_cache['osg-%s' % osgver] = {'target': 'osg-%s-el%%s' % osgver, 'tag': 'osg-el%s'}

    return __repo_hints_cache


def set_loglevel(level_str):
    """Sets the log level from a string. level_str should match one of the
    constants defined in logging"""
    global log, log_consolehandler

    try:
        loglevel = int(getattr(logging, level_str.upper()))
    except (TypeError, AttributeError):
        raise UsageError("Invalid log level")

    log.setLevel(loglevel)
    log_consolehandler.setLevel(loglevel)


def parse_cmdline_args(argv):
    """Parse the arguments given on the command line. Return a tuple containing
    options:    the options object, containing the keyword arguments
    args:       a list containing the positional arguments left over

    """
    parser = OptionParser("""
   %prog TASK PACKAGE1 <PACKAGE2..n> [options]

Valid tasks are:
koji         Build using koji
lint         Discover potential package problems using rpmlint
mock         Build using mock(1) on the local machine
prebuild     Preprocess the package, create SRPM to be submitted, and stop
prepare      Use rpmbuild -bp to unpack and patch the package
quilt        Preprocess the package and run 'quilt setup' on the spec file to
             unpack the source files and prepare a quilt(1) series file.
rpmbuild     Build using rpmbuild(8) on the local machine
""")
    parser.add_option(
        "-a", "--autoclean", action="store_true",
        help="Clean out the following directories before each build: "
        "'%s', '%s', '%s', '%s'" % (
            WD_RESULTS, WD_PREBUILD, WD_UNPACKED, WD_UNPACKED_TARBALL))
    parser.add_option(
        "--no-autoclean", action="store_false", dest="autoclean",
        help="Disable autoclean")
    parser.add_option(
        "-c", "--cache-prefix",
        help="The prefix for the software cache to take source files from. "
        "The following special caches exist: "
        "AFS (%s), VDT (%s), and AUTO (AFS if avaliable, VDT if not). "
        "The default cache is AUTO." % (AFS_CACHE_PREFIX, WEB_CACHE_PREFIX))
    parser.add_option(
        "-C", "--config-file",
        help="The file to get configuration for this script.")
    parser.add_option(
        "--el5", action="callback", callback=parser_targetopts_callback,
        type=None,
        dest="redhat_release",
        help="Build for RHEL 5-compatible. Equivalent to --redhat-release=5")
    parser.add_option(
        "--el6", action="callback", callback=parser_targetopts_callback,
        type=None,
        dest="redhat_release",
        help="Build for RHEL 6-compatible. Equivalent to --redhat-release=6")
    parser.add_option(
        "--el7", action="callback", callback=parser_targetopts_callback,
        type=None,
        dest="redhat_release",
        help="Build for RHEL 7-compatible. Equivalent to --redhat-release=7")
    parser.add_option(
        "--loglevel",
        help="The level of logging the script should do. "
        "Valid values are: DEBUG,INFO,WARNING,ERROR,CRITICAL")
    parser.add_option(
        "-q", "--quiet", action="store_const", const="warning", dest="loglevel",
        help="Display less information. Equivalent to --loglevel=warning")
    parser.add_option(
        "--redhat-release", action="callback",
        callback=parser_targetopts_callback,
        dest="redhat_release",
        type="string",
        help="The version of the distribution to build the package for. "
        "Valid values are: 5 (for RHEL 5), 6 (for RHEL 6), 7 (for RHEL 7). "
        "If not specified, will build for all releases (koji task) or the "
        "platform you are running this on (other tasks)")
    parser.add_option(
        "-t", "--target-arch",
        help="The target architecture to build for."
        " Ignored in non-scratch koji builds")
    parser.add_option(
        "-v", "--verbose", action="store_const", const="debug", dest="loglevel",
        help="Display more information. Equivalent to --loglevel=debug")
    parser.add_option(
        "--version", action="store_true",
        help="Show version and exit.")
    parser.add_option(
        "-w", "--working-directory",
        help="The base directory to use for temporary files made by the "
        "script. If it is 'TEMP', a randomly-named directory under /tmp "
        "is used.")

    prebuild_group = OptionGroup(parser,
                                 "prebuild task options")
    prebuild_group.add_option(
        "--full-extract", action="store_true",
        help="Fully extract all source files.")

    rpmbuild_mock_group = OptionGroup(parser, 
                                      "rpmbuild and mock task options")
    rpmbuild_mock_group.add_option(
        "--distro-tag",
        help="The distribution tag to append to the end of the release. "
        "(Default: osg.el5, osg.el6 or osg.el7, depending on --redhat-release)")

    mock_group = OptionGroup(parser,
                             "mock task options")
    mock_group.add_option(
        "--mock-clean", action="store_true", dest="mock_clean",
        help="Clean the mock buildroot after building (default)")
    mock_group.add_option(
        "--no-mock-clean", action="store_false", dest="mock_clean",
        help="Do not clean the mock buildroot after building")
    mock_group.add_option(
        "-m", "--mock-config",
        help="The location of the mock config file. "
        "defaults to AUTO to use an autogenerated file "
        "recommended for OSG builds")
    mock_group.add_option(
        "--mock-config-from-koji",
        help="Use a mock config based on a koji buildroot (build tag, "
        "such as osg-3.2-el5-build).")

    koji_group = OptionGroup(parser,
                             "koji task options")
    koji_group.add_option(
        "--background", action="store_true",
        help="Run build at a lower priority")
    koji_group.add_option(
        "--dry-run", action="store_true",
        help="Do not invoke koji, only show what would be done.")
    koji_group.add_option(
        "--getfiles", "--get-files", action="store_true", dest="getfiles",
        help="Download finished products and logfiles")
    koji_group.add_option(
        "--koji-backend", dest="koji_backend",
        help="The back end to use for invoking koji. Valid values are: "
        "'shell', 'kojilib'. If not specified, will try to use kojilib and use "
        "shell as a fallback.")
    koji_group.add_option(
        "-k", "--kojilogin", "--koji-login", dest="kojilogin",
        help="The login you use for koji (most likely your CN, e.g."
        "'Matyas Selmeci 564109')")
    koji_group.add_option(
        "--koji-target",
        action="callback",
        callback=parser_targetopts_callback,
        type="string",
        help="The koji target to use for building. "
        "It is recommended to use the --repo option instead of this when the "
        "desired repo is available.")
    koji_group.add_option(
        "--koji-tag",
        action="callback",
        callback=parser_targetopts_callback,
        type="string",
        help="The koji tag to add packages to. The special value TARGET "
        "uses the destination tag defined in the koji target. "
        "It is recommended to use the --repo option instead of this when the "
        "desired repo is available.")
    koji_group.add_option(
        "--koji-target-and-tag", "--ktt",
        action="callback",
        callback=parser_targetopts_callback,
        type="string",
        dest="ktt",
        metavar='ARG',
        help="Specifies both the koji tag to add packages to and the target "
        "to use for building. '--ktt ARG' is equivalent to '--koji-target ARG "
        " --koji-tag ARG'. "
        "It is recommended to use the --repo option instead of this when the "
        "desired repo is available.")
    koji_group.add_option(
        "--koji-wrapper", action="store_true", dest="koji_wrapper",
        help="Use the 'osg-koji' koji wrapper if using the 'shell' backend. "
        "(Default)")
    koji_group.add_option(
        "--no-koji-wrapper", action="store_false", dest="koji_wrapper",
        help="Do not use the 'osg-koji' koji wrapper if using the 'shell' "
        "backend, even if found.")
    koji_group.add_option(
        "--no-wait", "--nowait", action="store_true", dest="no_wait",
        help="Do not wait for the build to finish")
    koji_group.add_option(
        "--wait", action="store_false", dest="no_wait",
        help="Wait for the build to finish")
    koji_group.add_option(
        "--regen-repos", action="store_true",
        help="Perform a regen-repo on the build and destination repos after "
        "each koji build. Allows doing builds that depend on each other. "
        "Use sparingly, as this slows down builds and uses more disk space on "
        "koji-hub.")
    koji_group.add_option(
        "--scratch", action="store_true",
        help="Perform a scratch build")
    koji_group.add_option(
        "--no-scratch", "--noscratch", action="store_false", dest="scratch",
        help="Do not perform a scratch build")
    koji_group.add_option(
        "--vcs", "--svn", action="store_true", dest="vcs",
        help="Build package directly from SVN/git "
        "(default for non-scratch builds)")
    koji_group.add_option(
        "--no-vcs", "--novcs", "--no-svn", "--nosvn", action="store_false", dest="vcs",
        help="Do not build package directly from SVN/git "
        "(default for scratch builds)")
    koji_group.add_option(
        "--upcoming", action="callback",
        callback=parser_targetopts_callback,
        type=None,
        help="Target build for the 'upcoming' osg repos.")
    koji_group.add_option(
        "--repo", action="callback",
        callback=parser_targetopts_callback,
        type="string", dest="repo",
        help="Specify a set of repos to build to (osg-3.1 (or just 3.1), "
        + ", ".join(REPO_HINTS_STATIC.keys())
        + ")")
    for grp in [prebuild_group, rpmbuild_mock_group, mock_group, koji_group]:
        parser.add_option_group(grp)

    options, args = parser.parse_args(argv[1:])

    return (options, args)
# end of parse_cmdline_args()


def get_dver_from_string(s):
    """Get the EL major version from a string containing it.
    Return None if not found."""
    if not s:
        return None
    match = re.search(r'\bel(\d+)\b', s)
    if match is not None:
        return match.group(1)
    else:
        return None

def target_for_repo_hint(repo_hint, dver):
    hints = repo_hints(valid_koji_targets())
    if repo_hint in hints:
        return hints[repo_hint]['target'] % dver
    else:
        raise UsageError("'%s' is not a valid repo.\nValid repos are: %s" % (repo_hint, ", ".join(sorted(hints.keys()))))

def tag_for_repo_hint(repo_hint, dver):
    return repo_hints(valid_koji_targets())[repo_hint]['tag'] % dver

def parser_targetopts_callback(option, opt_str, value, parser, *args, **kwargs): # unused-args: pylint:disable=W0613
    """Handle options in the 'targetopts_by_dver' set, such as --koji-tag,
    --redhat-release, etc.

    targetopts_by_dver is a dict keyed by redhat release (aka 'distro
    version' or 'dver' for short) The values of targetopts_by_dver are dicts
    containing the options to use for building with that dver. For example,
    targetopts_by_dver['5']['koji_tag'] is the koji tag to use when building
    for EL 5.

    enabled_dvers is the set of dvers to actually build for, which the --el5,
    --el6, --el7 and --redhat-release arguments affect. dvers may also be
    implicitly turned on by other arguments, e.g. specifying
    --koji-tag=el5-foobar will implicitly turn on el5 builds.

    Also handle --ktt (--koji-tag-and-target), which sets both koji_tag
    and koji_target, and --upcoming, and --repo, which sets the target for both dvers.

    """
    # for options that have aliases, option.dest gives us the
    # canonical name of the option (as opposed to opt_str, which gives us
    # what the user typed in)
    opt_name = option.dest

    # We create parser.values.targetopts_by_dver here instead of ahead of time
    # because parser.values is None until we actually run parser.parse_args().
    if not getattr(parser.values, 'targetopts_by_dver', None):
        parser.values.targetopts_by_dver = dict()
        for dver in DVERS:
            parser.values.targetopts_by_dver[dver] = DEFAULT_BUILDOPTS_BY_DVER[dver].copy()
    targetopts_by_dver = parser.values.targetopts_by_dver

    # We also have enabled_dvers for determining which dvers to build for.
    if not getattr(parser.values, 'enabled_dvers', None):
        parser.values.enabled_dvers = set()
    enabled_dvers = parser.values.enabled_dvers

    dver = None
    if value is None:
        value = ''
    if opt_name == 'redhat_release':
        if opt_str == '--el5':
            enabled_dvers.add('5')
        elif opt_str == '--el6':
            enabled_dvers.add('6')
        elif opt_str == '--el7':
            enabled_dvers.add('7')
        elif opt_str == '--redhat-release':
            if value in DVERS:
                enabled_dvers.add(value)
            else:
                raise OptionValueError("Invalid redhat release value: %r" % value)
    elif opt_name == 'koji_tag' and value == 'TARGET': # HACK
        for dver in targetopts_by_dver:
            targetopts_by_dver[dver]['koji_tag'] = 'TARGET'
    elif opt_str == '--upcoming':
        for dver in DVERS:
            targetopts_by_dver[dver]['repo'] = 'upcoming'
            targetopts_by_dver[dver]['koji_target'] = target_for_repo_hint('upcoming', dver)
            targetopts_by_dver[dver]['koji_tag'] = tag_for_repo_hint('upcoming', dver)
    elif opt_str == '--repo':
        for dver in DVERS:
            targetopts_by_dver[dver]['repo'] = value
            targetopts_by_dver[dver]['koji_target'] = target_for_repo_hint(value, dver)
            targetopts_by_dver[dver]['koji_tag'] = tag_for_repo_hint(value, dver)
    else:
        dver = get_dver_from_string(value)

        if not dver:
            raise OptionValueError('Unable to determine redhat release in parameter %r: %r' % (opt_str, value))

        if dver not in enabled_dvers:
            enabled_dvers.add(dver)
            print "Implicitly enabled building for el%s due to %r argument %r" % (dver, opt_str, value)

        if opt_name == 'ktt':
            targetopts_by_dver[dver]['koji_tag'] = value
            targetopts_by_dver[dver]['koji_target'] = value
        else:
            targetopts_by_dver[dver][opt_name] = value
        if not verify_release_in_targetopts_by_dver(targetopts_by_dver[dver]):
            raise OptionValueError('Inconsistent redhat release in parameter %s: %s' % (opt_str, value))
# end of parser_targetopts_callback()



def get_task(args):
    """Return the task the user specified in the first positional argument,
    if it is a valid task. Allow the user to enter only the first few
    characters if the task is unambiguous. Raise UsageError if task is
    unspecified, invalid, or ambiguous.

    """
    if len(args) < 1:
        raise UsageError('Need task!')
    task = args[0]

    valid_tasks = ['koji', 'lint', 'mock', 'prebuild', 'prepare', 'quilt', 'rpmbuild']

    matching_tasks = [x for x in valid_tasks if x.startswith(task)]

    if len(matching_tasks) > 1:
        raise UsageError('Ambiguous task. Matching tasks are:' + ", ".join(matching_tasks))
    elif not matching_tasks:
        raise UsageError('No valid task')
    else:
        real_task = matching_tasks[0]

    return real_task
# end of get_task()


def get_buildopts(options, task):
    """Return a dict of the build options to use, based on the config file and
    command-line arguments.

    The format of the config file is simple: there's one section, [options],
    and the canonical name of every command-line argument can be used as an
    option.

    This has two implications: first, you should be able to override any option
    with a subsequent option. Second, I can't set a 'default' value for any of
    the options in the OptionParser object, because I need to distinguish
    between the option not having been specified, and the option explicitly
    being the default.

    """

    buildopts = DEFAULT_BUILDOPTS_COMMON.copy()

    cfg_items = read_config_file(options.config_file)
    buildopts.update(cfg_items)

    # Backward compatibility for 'svn' option:
    buildopts['vcs'] = buildopts.get('vcs') or buildopts.get('svn')

    # Overrides from command line
    for optname in options.__dict__.keys():
        optval = getattr(options, optname, None)
        if optval is not None:
            buildopts[optname] = optval

    # Special case for working_directory being TEMP
    if buildopts['working_directory'].upper() == 'TEMP':
        buildopts['working_directory'] = (tempfile.mkdtemp(prefix='osg-build-'))
        log.debug('Working directory is %s', buildopts['working_directory'])

    # Special case for cache_prefix being AFS or VDT
    if buildopts['cache_prefix'].upper() == 'AFS':
        buildopts['cache_prefix'] = AFS_CACHE_PREFIX
    elif buildopts['cache_prefix'].upper() == 'VDT':
        buildopts['cache_prefix'] = WEB_CACHE_PREFIX
    elif buildopts['cache_prefix'].upper() == 'AUTO':
        if os.path.exists(AFS_CACHE_PATH):
            buildopts['cache_prefix'] = AFS_CACHE_PREFIX
        else:
            buildopts['cache_prefix'] = WEB_CACHE_PREFIX

    # If nothing has set targetopts_by_dver, set it here
    if not buildopts.get('targetopts_by_dver', None):
        buildopts['targetopts_by_dver'] = dict()
        for dver in DVERS:
            buildopts['targetopts_by_dver'][dver] = DEFAULT_BUILDOPTS_BY_DVER[dver].copy()

    # Which distro versions are we building for? If not specified on the
    # command line, either build for all (koji) or the dver of the local machine
    # (others)
    enabled_dvers = getattr(options, 'enabled_dvers', None)
    if not enabled_dvers:
        if task == 'koji':
            buildopts['enabled_dvers'] = set(DEFAULT_DVERS)
        else:
            buildopts['enabled_dvers'] = set([get_local_machine_dver()])

    # Hack: make --mock-config on command line override
    # --mock-config-from-koji from config file
    if options.mock_config is not None:
        buildopts['mock_config_from_koji'] = None

    # If set, --mock-config-from-koji overrides --mock-config
    if buildopts['mock_config_from_koji']:
        buildopts['mock_config'] = None

    if buildopts['vcs'] is None and task == 'koji':
        if buildopts['scratch']:
            buildopts['vcs'] = False
        else:
            buildopts['vcs'] = True

    return buildopts
# end of get_buildopts()


def read_config_file(given_cfg_file=None):
    """Return a dict of items read from a config file. If given_cfg_file
    is None, uses one of the default config file locations.

    """
    cfg_file = None
    if given_cfg_file:
        cfg_file = given_cfg_file
    else:
        if os.path.exists(DEFAULT_CONFIG_FILE):
            cfg_file = DEFAULT_CONFIG_FILE
        else:
            log.debug("Didn't find default config at %s", DEFAULT_CONFIG_FILE)
            if os.path.exists(ALT_DEFAULT_CONFIG_FILE):
                cfg_file = ALT_DEFAULT_CONFIG_FILE

    if cfg_file and os.path.isfile(cfg_file):
        try:
            cfg = ConfigParser.ConfigParser()
            cfg.read(cfg_file)
            log.debug("Read default config from %s", cfg_file)
            return cfg.items('options')
        except ConfigParser.Error, err:
            log.warning("Error reading configuration from %s: %s", cfg_file, str(err))
    else:
        return {}


def print_version_and_exit():
    """Print version and exit"""
    # '@'+'VERSION'+'@' is so sed will leave it alone during 'make dist'
    if __version__ == '@' + 'VERSION' + '@':
        print "osg-build SVN"
        out, ret = utils.sbacktick("svn info " + sys.argv[0], err2out=True)
        if ret:
            print "no info"
        else:
            print "SVN info:\n" + out
    else:
        print "osg-build " + __version__
    sys.exit(0)


def all(iterable): # disable "redefined-builtin" check: pylint: disable=W0622
    """Return True if all elements of the iterable are true (or if it's empty).
    This is a builtin in Python 2.5+, but doesn't exist in 2.4.

    """
    for element in iterable:
        if not element:
            return False
    return True



def verify_release_in_targetopts_by_dver(targetopts_by_dver):
    """Verify that the values for distro_tag, koji_target and koji_tag are
    consistent. If consistent, return the release; else, return None.
    Also return None if none of the values are specified.
    """
    redhat_release, distro_tag, koji_target, koji_tag = (
        targetopts_by_dver.get('redhat_release'),
        targetopts_by_dver.get('distro_tag'),
        targetopts_by_dver.get('koji_target'),
        targetopts_by_dver.get('koji_tag'))
    if koji_tag == 'TARGET': # HACK
        koji_tag = None
    def same_or_none2(a, b):
        return (a == b) or a is None or b is None
    def same_or_none(*args):
        return all((same_or_none2(args[x], args[y]) for x in range(len(args)) for y in range(x, len(args))))

    # Verify consistency
    dist_rel = get_dver_from_string(distro_tag)
    target_rel = get_dver_from_string(koji_target)
    tag_rel = get_dver_from_string(koji_tag)

    if not same_or_none(redhat_release, dist_rel, tag_rel, target_rel):
        return None

    rel = redhat_release or dist_rel or target_rel or tag_rel
    return rel


def get_local_machine_dver():
    "Return the distro version (i.e. major redhat release) of the local machine or None"
    redhat_release_contents = utils.slurp('/etc/redhat-release')
    try:
        match = re.search(r'release (\d)', redhat_release_contents)
        return match.group(1)
    except (TypeError, AttributeError):
        return None


def guess_pkg_dir(start_dir):
    guess_dir = os.path.realpath(os.path.expanduser(start_dir))
    if os.path.basename(guess_dir) == 'osg' or os.path.basename(guess_dir) == 'upstream':
        return os.path.dirname(guess_dir)
    guess_dirlist = guess_dir.split('/')
    if guess_dirlist[0] == '':
        guess_dirlist[0] = '/'
    for udir in [WD_RESULTS, WD_PREBUILD, WD_UNPACKED, WD_UNPACKED_TARBALL, WD_QUILT]:
        try:
            idx = guess_dirlist.index(udir)
            return os.path.join(*guess_dirlist[0:idx])
        except ValueError:
            continue

    return guess_dir


if __name__ == '__main__':
    sys.exit(main(sys.argv))

