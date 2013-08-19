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
import logging
from optparse import OptionGroup, OptionParser, OptionValueError
import re
import os
import sys
import tempfile
import ConfigParser

from osgbuild.constants import *
from osgbuild.error import UsageError
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
    if task == 'allbuild':
        log.warning("The 'allbuild' task is deprecated. The 'koji' task now "
                    "builds on all supported distro versions by default.")
    # checks
    if task == 'allbuild' or (task == 'koji' and buildopts['vcs']):
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

    # main loop 
    # HACK
    task_ids = []
    task_ids_by_results_dir = dict()
    for pkg in package_dirs:
        if task == 'allbuild':
            # allbuild is special--we ignore most options and use
            # a slightly different set of defaults.
            for dver in DVERS:
                dver_buildopts = buildopts.copy()
                dver_buildopts.update(DEFAULT_BUILDOPTS_BY_DVER[dver])
                for key in ALLBUILD_ALLOWED_OPTNAMES:
                    dver_buildopts[key] = buildopts[key]

                vcs.koji(pkg, kojiinter.KojiInter(dver_buildopts), dver_buildopts)

        else:
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
            kojiinter.KojiInter.backend.watch_tasks(task_ids)
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

    if len(args) < 2:
        raise UsageError('Need package directories for this task!')

    package_dirs = args[1:]

    return (buildopts, package_dirs, task)
# end of init()


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
allbuild     Build out of SVN using koji for all supported platforms into the
             default tags/targets for each platform (deprecated)
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
        "Valid values are: 5 (for RHEL 5), 6 (for RHEL 6). "
        "If not specified, will build for all releases (koji task) or the "
        "platform you are running this on (other tasks)")
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
        "(Default: osg.el5 or osg.el6, depending on --redhat-release)")
    rpmbuild_mock_group.add_option(
        "-t", "--target-arch",
        help="The target architecture to build for ")

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
        "such as el5-osg-build).")

    koji_group = OptionGroup(parser,
                             "koji task options")
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
        help="The koji target to use for building. Default: "
        "el5-osg or el6-osg depending on --redhat-release")
    koji_group.add_option(
        "--koji-tag",
        action="callback",
        callback=parser_targetopts_callback,
        type="string",
        help="The koji tag to add packages to. The special value TARGET "
        "uses the destination tag defined in the koji target. Default: "
        "el5-osg or el6-osg depending on --redhat-release")
    koji_group.add_option(
        "--koji-target-and-tag", "--ktt",
        action="callback",
        callback=parser_targetopts_callback,
        type="string",
        dest="ktt",
        metavar='ARG',
        help="Specifies both the koji tag to add packages to and the target "
        "to use for building. '--ktt ARG' is equivalent to '--koji-target ARG "
        " --koji-tag ARG'.")
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
        "--vcs", action="store_true", dest="vcs",
        help="Build package directly from SVN/git "
        "(default for non-scratch builds)")
    koji_group.add_option(
        "--no-vcs", "--novcs", action="store_false", dest="vcs",
        help="Do not build package directly from SVN "
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
        help="Specify a set of repos to build to (osg, upcoming, hcc, uscms).")
    for grp in [prebuild_group, rpmbuild_mock_group, mock_group, koji_group]:
        parser.add_option_group(grp)

    options, args = parser.parse_args(argv[1:])

    return (options, args)
# end of parse_cmdline_args()


def get_dver_from_string(s):
    """Get the EL major version from a string containing it.
    Return None if not found."""
    match = re.search(r'\bel(\d+)\b', s)
    if match is not None:
        return match.group(1)
    else:
        return None


def parser_targetopts_callback(option, opt_str, value, parser, *args, **kwargs):
    """Handle options in the 'targetopts_by_dver' set, such as --koji-tag,
    --redhat-release, etc.
    
    targetopts_by_dver is a dict keyed by redhat release (aka 'distro
    version' or 'dver' for short) The values of targetopts_by_dver are dicts
    containing the options to use for building with that dver. For example,
    targetopts_by_dver['5']['koji_tag'] is the koji tag to use when building
    for EL 5.

    enabled_dvers is the set of dvers to actually build for, which the --el5,
    --el6 and --redhat-release arguments affect. dvers may also be
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
        elif opt_str == '--redhat-release':
            if value in DVERS:
                enabled_dvers.add(value)
            else:
                raise OptionValueError("Invalid redhat release value: %r" % value)
    elif opt_name == 'koji_tag' and value == 'TARGET': # HACK
        for dver in targetopts_by_dver:
            targetopts_by_dver[dver]['koji_tag'] = 'TARGET'
    elif opt_str == '--upcoming': # Also HACK
        for dver in DVERS:
            targetopts_by_dver[dver]['koji_target'] = 'el%s-osg-upcoming' % dver
    elif opt_str == '--repo':
        target_hint = ''
        if value == 'upcoming': target_hint = 'el%s-osg-upcoming'
        elif value == 'osg': target_hint = 'el%s-osg'
        elif value == 'hcc': target_hint = 'hcc-el%s'
        elif value == 'uscms': target_hint = 'uscms-el%s'
        for dver in DVERS:
            targetopts_by_dver[dver]['koji_target'] = target_hint % dver
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

    valid_tasks = ['allbuild', 'koji', 'lint', 'mock', 'prebuild', 'prepare',
                   'quilt', 'rpmbuild']

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

    # Hack: if the task is "allbuild", use a different set of
    # defaults, ignore the config file, and most options.
    if task == 'allbuild':
        buildopts = ALLBUILD_BUILDOPTS.copy()
        for optname in ALLBUILD_ALLOWED_OPTNAMES:
            optval = getattr(options, optname, None)
            if optval is not None:
                buildopts[optname] = optval
        return buildopts

    # otherwise...
    buildopts = DEFAULT_BUILDOPTS_COMMON.copy()

    cfg_items = read_config_file(options.config_file)
    buildopts.update(cfg_items)

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
            buildopts['enabled_dvers'] = set(DVERS)
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


def all(iterable):
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

if __name__ == '__main__':
    sys.exit(main(sys.argv))

