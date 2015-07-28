"""A package promotion script for OSG"""



import os
import re
import sys
import logging
import ConfigParser

if sys.version_info < (2, 6):
    sys.stderr.write("osg-promote requires Python 2.6 or greater")
    sys.exit(1)

from osgbuild import constants
from osgbuild import error
from osgbuild import kojiinter
from osgbuild import utils
from osgbuild.utils import printf, print_table
from optparse import OptionParser

from collections import namedtuple

DEFAULT_ROUTE = 'testing'
INIFILE = 'promoter.ini'

DVERS_OFF_BY_DEFAULT = ['el7']
DVERS_BY_OSGVER = {
    '3.1': ['el5', 'el6'],
    '3.2': ['el5', 'el6'],
    '3.3': ['el6', 'el7'],
}


# logging. Can't use root logger because its loglevel can't be changed once set
log = logging.getLogger('osgpromote')
log.setLevel(logging.INFO)
log_consolehandler = logging.StreamHandler()
log_consolehandler.setLevel(logging.INFO)
log_formatter = logging.Formatter("%(message)s")
log_consolehandler.setFormatter(log_formatter)
log.addHandler(log_consolehandler)
log.propagate = False


class KojiTagsAreMessedUp(Exception):
    """Raised when Koji tags are in an inconsistent or unusable state.

    This can happen if one half of a route does not exist for a given dver
    (e.g. osg-3.1-el5-development exists but not osg-3.1-el5-testing).
    """


Route = namedtuple('Route', ['from_tag_hint', 'to_tag_hint', 'repo', 'dvers', 'extra_dvers'])
# Route = namedtuple('Route', ['from_tag_hint', 'to_tag_hint', 'repo'])


class Build(object):
    def __init__(self, name, version, release_no_dist, repo, dver):
        self.name = name
        self.version = version
        self.release_no_dist = release_no_dist
        self.repo = repo
        self.dver = dver

    @staticmethod
    def new_from_nvr(nvr):
        name, version, release = split_nvr(nvr)
        release_no_dist, repo, dver = split_repo_dver(release)

        return Build(name, version, release_no_dist, repo, dver)

    @property
    def vr_no_dist(self):
        return '-'.join([self.version, self.release_no_dist])

    @property
    def vr(self):
        return '.'.join([self.vr_no_dist, self.dist]).strip('.')

    @property
    def nvr(self):
        return '-'.join([self.name, self.vr])

    @property
    def nvr_no_dist(self):
        return '-'.join([self.name, self.vr_no_dist])

    @property
    def dist(self):
        return '.'.join([self.repo, self.dver]).strip('.')


class Reject(object):
    REASON_DISTINCT_ACROSS_DISTS = "Build versions matching %(pkg_or_build)s distinct across dist tags"
    REASON_NOMATCHING_FOR_DIST = "No build matching %(pkg_or_build)s for dist %(dist)s"

    def __init__(self, pkg_or_build, dist, reason):
        self.pkg_or_build = pkg_or_build
        self.dist = dist
        self.reason = reason

    def __str__(self):
        return self.reason % {'pkg_or_build': self.pkg_or_build, 'dist': self.dist}

#
# Utility functions
#


def split_nvr(build):
    """Split an NVR into a (Name, Version, Release) tuple"""
    match = re.match(r"(?P<name>.+)-(?P<version>[^-]+)-(?P<release>[^-]+)$", build)
    if match:
        return match.group('name'), match.group('version'), match.group('release')
    else:
        return '', '', ''


def split_repo_dver(build, known_repos=None):
    """Split out the dist tag from the NVR of a build, returning a tuple
    containing (NVR (without dist tag), repo, dver).
    For example, split_repo_dver("foobar-1-1.osg32.el5") returns
    ("foobar-1-1", "osg32", "el5").
    The empty string is returned for any of the components that aren't present.

    Since the dist tag isn't a well-defined entity, I'm using the following
    heuristic to distinguish it:
        1. If the dist tag exists, it's at the end of the release.
            (only known exception: gridsite-1.7.15-4.osg.3. This is not
            handled, but it's not worth the extra code)
        2. If the dver is in the release, it is at the end of the dist tag.
        3. The dist tag contains at most 2 components, separated by '.'
    If known_repos is specified (as a list), then the repo component must be
    one of the strings in that list. Otherwise, the repo component must start
    with [a-z]. This is a workaround to prevent misidentifying the repo
    tag on a release like "1.11".

    """
    build_no_dist = build
    repo = ""
    dver = ""

    build_no_dist_pat = r"(?P<build_no_dist>.+)"
    repo_pat = r"(?P<repo>[a-z]\w+)"
    dver_pat = r"(?P<dver>el\d+)"
    if known_repos is not None:
        repo_pat = r"(?P<repo>" + "|".join(known_repos) + ")"

    # order matters since later patterns are less specific and would match more
    pat_1_repo_and_dver = re.compile(build_no_dist_pat + r"\." + repo_pat + r"\." + dver_pat + "$")
    pat_2_dver_only = re.compile(build_no_dist_pat + r"\." + dver_pat + "$")
    pat_3_repo_only = re.compile(build_no_dist_pat + r"\." + repo_pat + "$")

    match = (pat_1_repo_and_dver.match(build) or
             pat_2_dver_only.match(build) or
             pat_3_repo_only.match(build))

    if match:
        groupdict = match.groupdict()
        build_no_dist, repo, dver = groupdict['build_no_dist'], groupdict.get('repo', ''), groupdict.get('dver', '')

    return build_no_dist, repo, dver


def _parse_list_str(list_str):
    # split string on spaces or commas
    items = re.split(r'[ ,]', list_str)
    # remove empty strings from the list
    filtered_items = filter(None, items)
    return filtered_items


def load_routes(inifile):
    """Load dict of routes (key is the route name, value is a Route object
    from a .ini file.

    A section called "route X" creates a route named "X".
    Required attributes in a route section are:
    - from_tag_hint: the name of the koji tag to promote from, with '%s'
      where the dver would be
    - to_tag_hint: the name of the koji tag to promote to, with '%s' where
      the dver would be
    - repotag: the 'repo' part of the dist tag, e.g. 'osg33' for a dist tag
      like '.osg33.el5'
    - dvers: a comma or space separated list of distro versions (dvers)
      supported by default for the tags in the route, e.g. "el5 el6"
    The optional attribute is:
    - extra_dvers: a comma or space separated list of dvers that are supported
      by the tags in the route but should not be on by default

    A section called "aliases" defined alternate names for a route.
    The key of each attribute in the section is the new name, and the value
    is the old name, e.g. "testing=3.2-testing".  Aliases to aliases cannot be
    defined.

    """
    config = ConfigParser.RawConfigParser()

    config.read([os.path.join(x, INIFILE) for x in constants.DATA_FILE_SEARCH_PATH])
    if not config.sections():
        raise error.FileNotFoundError(INIFILE, constants.DATA_FILE_SEARCH_PATH)

    routes = {}

    for sec in config.sections():
        if not sec.startswith('route '):
            continue
        routename = sec.split(None, 1)[1]
        try:
            from_tag_hint = config.get(sec, 'from')
            to_tag_hint = config.get(sec, 'to')
            repotag = config.get(sec, 'repotag')
            dvers = _parse_list_str(config.get(sec, 'dvers'))
        except ConfigParser.NoOptionError as err:
            raise error.Error("Malformed config file: {0!s}".format(err))
        extra_dvers = []
        if config.has_option(sec, 'extra_dvers'):
            extra_dvers = _parse_list_str(config.get(sec, 'extra_dvers'))

        routes[routename] = Route(from_tag_hint, to_tag_hint, repotag, dvers, extra_dvers)

    if config.has_section('aliases'):
        for newname, oldname in config.items('aliases'):
            try:
                routes[newname] = routes[oldname]
            except KeyError as err:
                raise error.Error("Alias {0} to {1} failed: {1} does not exist".format(newname,oldname))

    return routes


class Promoter(object):
    """For promoting sets of packages.
    Usage is to add packages or builds via add_promotion and then call
    do_promotions to actually promote.
    do_promotions should not be called twice.

    """
    def __init__(self, kojihelper, route_dvers_pairs):
        """kojihelper is an instance of KojiHelper. routes is a list of Route objects. dvers is a list of strings.
        """
        self.tag_pkg_args = {}
        self.rejects = []
        self.kojihelper = kojihelper
        self.route_dvers_pairs = route_dvers_pairs
        self.repos = set(route.repo for route, _ in self.route_dvers_pairs)

    def add_promotion(self, pkg_or_build, ignore_rejects=False):
        """Run get_builds() for 'pkg_or_build', using from_tag_hint as the
        tag hint.
        Returns nothing; builds to promote are added to tag_pkg_args, which
        is a dict keyed by tag (actual tag, not tag hint) of koji builds that
        should be added to that tag.

        """
        tag_build_pairs = []
        for route, dvers in self.route_dvers_pairs:
            builds = self.get_builds(route, dvers, pkg_or_build, ignore_rejects)
            for build_dver in builds:
                to_tag = route.to_tag_hint % build_dver
                tag_build_pairs.append((to_tag, builds[build_dver]))

        if not ignore_rejects and self.any_distinct_across_dists(tag_build_pairs):
            self.rejects.append(Reject(pkg_or_build, None, Reject.REASON_DISTINCT_ACROSS_DISTS))
        else:
            for tag, build in tag_build_pairs:
                self.tag_pkg_args.setdefault(tag, [])
                self.tag_pkg_args[tag].append(build)

    def _get_build(self, tag_hint, repo, dver, pkg_or_build):
        """Get a single build (as a Build object) out of the tag given by
        tag_hint % dver that matches pkg_or_build. This only returns builds
        where the Release field contains a dist tag with both a repo and a dver
        (e.g. osg31.el5).

        If given a build where the dist tag does not match the repo and/or
        dver, will strip off the dist tag, put on the one that is appropriate
        for the repo and dver, and return _that_ build.

        If given a package (i.e. just a name and not an NVR, then the latest
        build for the tag is returned (latest meaning most recently added to
        that tag)).

        Return None if no matching build was found.

        """
        tag = self._get_valid_tag_for_dver(tag_hint, dver)
        pkg_or_build_no_dist = split_repo_dver(pkg_or_build, self.repos)[0]
        # Case 1: pkg_or_build is a build, in which case take off its dist tag
        # and put the dist tag specified dist tag on, then find a build for that.
        build_nvr_1 = self.kojihelper.get_build_in_tag(tag, ".".join([pkg_or_build_no_dist, repo, dver]))
        # Case 2: pkg_or_build is a package, in which case putting a dist tag
        # on doesn't help--just find the latest build in the tag.
        build_nvr_2 = self.kojihelper.get_build_in_tag(tag, pkg_or_build_no_dist)

        build_nvr = build_nvr_1 or build_nvr_2 or None

        if build_nvr:
            build_obj = Build.new_from_nvr(build_nvr)
            return build_obj

    def get_builds(self, route, dvers, pkg_or_build, ignore_rejects=False):
        """Get a dict of builds keyed by dver for pkg_or_build.
        Uses _get_build to get the build matching pkg_or_build for the given
        route and all given dvers.

        If matching builds are found for one but not all dvers then all builds
        will be rejected, unless ignore_rejects is True. Rejections are added
        to self.rejects.

        In case of a rejection (or no matching packages found at all), an
        empty dict is returned.

        """
        # TODO Both this and add_promotion currently handle rejections.
        # I think this should be refactored such that: get_builds goes through
        # all routes, not just one, and rejection handling should be done in
        # add_promotion.
        tag_hint = route.from_tag_hint
        repo = route.repo
        builds = {}
        # Find each build for all dvers matching pkg_or_build
        for dver in dvers:
            dist = "%s.%s" % (repo, dver)
            build = self._get_build(tag_hint, repo, dver, pkg_or_build)
            if not build:
                if not ignore_rejects:
                    self.rejects.append(Reject(pkg_or_build, dist, Reject.REASON_NOMATCHING_FOR_DIST))
                    return {}
                else:
                    continue
            builds[dver] = build

        return builds

    def any_distinct_across_dists(self, tag_build_pairs):
        distinct_nvrs = set()
        for _, build in tag_build_pairs:
            distinct_nvrs.add(build.nvr_no_dist)
        return len(distinct_nvrs) > 1

    def _get_valid_tag_for_dver(self, tag_hint, dver):
        """Find tag_hint % dver in koji's list of tags (as queried via
        kojihelper). Return the tag if found; raise KojiTagsAreMessedUp if
        not.

        RouteDiscovery should have already validated the route being used, but
        this is an extra layer of protection to catch mistakes.

        """
        tag = tag_hint % dver
        if not self.kojihelper.get_first_tag('exact', tag):
            raise KojiTagsAreMessedUp("Can't find tag %s in koji" % tag)
        return tag

    def do_promotions(self, dry_run=False, regen=False):
        """Tag all builds selected to be tagged in self.tag_pkg_args.
        self.tag_pkg_args is a list of (tag, [builds]) pairs.

        If dry_run is True, no actual tagging happens.  If
        regen is True, then each repository that gets modified will be
        regenerated after all tagging is done.

        Will not attempt to tag builds already in the destination tag.

        Return builds successfully promoted.

        """
        printf("--- Tagging builds")
        tasks = dict()
        for tag, builds in self.tag_pkg_args.items():
            for build in builds:
                try:
                    # Make sure the build isn't already in tag
                    if build.nvr in self.kojihelper.get_tagged_builds(tag):
                        printf("Skipping %s, already in %s", build.nvr, tag)
                        continue
                except KeyError:
                    pass

                # Launch the builds
                if not dry_run:
                    task_id = self.kojihelper.tag_build(tag, build.nvr)
                    tasks[task_id] = (tag, build)
                else:
                    printf("tagBuild('%s', '%s')", tag, build.nvr)

        promoted_builds = dict(self.tag_pkg_args)
        if not dry_run:
            promoted_builds = self.watch_builds(tasks)

            if regen:
                print "--- Regenerating repos"
                self.kojihelper.regen_repos(tags_to_regen=self.tag_pkg_args.keys())
        return promoted_builds

    def watch_builds(self, tasks):
        """Helper for do_promotions(). Watch builds being promoted and return
        successful ones.
        'tasks' is a dict of koji tasks keyed by the koji task ID
        Promoted builds are returned as a dict keyed by 'build_no_dver' (i.e.
        the NVR with the dver chopped off), containing dicts keyed by dver,
        containing the build (i.e. NVR as string).

        """
        self.kojihelper.watch_tasks(list(tasks.keys()))

        promoted_builds = {}
        for task_id, (tag, build) in tasks.items():
            if self.kojihelper.get_task_state(task_id) == 'CLOSED':
                promoted_builds.setdefault(tag, [])
                promoted_builds[tag].append(build)
            else:
                printf("* Error promoting build %s", build.nvr)

        return promoted_builds


# don't care if it has too many methods: pylint: disable=R0904
class KojiHelper(kojiinter.KojiLibInter):
    """Extra utility functions for dealing with Koji"""
    tags_cache = []
    tagged_builds_cache = {}
    tagged_packages_cache = {}

    def __init__(self, do_login):
        "Connect to koji-hub. Authenticate if 'do_login' is True."
        super(KojiHelper, self).__init__()
        self.read_config_file()
        self.init_koji_session(login=do_login)

    def get_all_dvers(self):
        """Return all possible dvers supported by any tag (as a list)"""
        pat = re.compile(r"(?:\b|^)(el\d+)")
        dvers = set()
        for tag in self.get_tags():
            match = pat.search(tag)
            if match:
                dvers.add(match.group(1))
        return sorted(list(dvers))

    def get_build_in_tag(self, tag, pkg_or_build):
        """Return the build matching 'pkg_or_build' in 'tag'.
        If pkg_or_build is not in the tag, returns None. Otherwise:
        If pkg_or_build is a package, returns the latest build for that
        package. If pkg_or_build is a build, it is returned unchanged.

        """
        if pkg_or_build in self.get_tagged_packages(tag):
            return self.get_latest_build(pkg_or_build, tag)
        elif pkg_or_build in self.get_tagged_builds(tag):
            return pkg_or_build
        else:
            return None

    def get_build_uri(self, build_nvr):
        """Return a URI to the kojiweb page of the build with the given NVR"""
        buildinfo = self.koji_get_build(build_nvr)
        return "%s/koji/buildinfo?buildID=%d" % (constants.HTTPS_KOJI_HUB, int(buildinfo['id']))

    def koji_get_build(self, build_nvr):
        return self.kojisession.getBuild(build_nvr)

    def get_first_tag(self, match, terms):
        """Return the first koji tag matching 'terms'.
        'match' is a string which is interpreted as a regex (if 'terms' is
        'regex') or an exact query (if 'terms' is 'exact').
        Return None if no such tag(s) are found.

        """
        try:
            return self.search_names(terms, 'tag', match)[0]
        except IndexError:
            return None

    def get_latest_build(self, package, tag):
        """Return the NVR of the latest build of a package in a tag, or None"""
        data = self.kojisession.listTagged(tag, latest=True, package=package)
        if not data:
            return None
        else:
            try:
                return data[0]['nvr']
            except KeyError:
                return None

    def get_tagged_builds(self, tag):
        """Return a list of NVRs of all builds in a tag"""
        if not KojiHelper.tagged_builds_cache.has_key(tag):
            data = self.kojisession.listTagged(tag)
            KojiHelper.tagged_builds_cache[tag] = [x['nvr'] for x in data]
        return KojiHelper.tagged_builds_cache[tag]

    def get_tagged_packages(self, tag):
        """Return a list of names of all builds in a tag"""
        if not KojiHelper.tagged_packages_cache.has_key(tag):
            KojiHelper.tagged_packages_cache[tag] = [split_nvr(x)[0] for x in self.get_tagged_builds(tag)]
        return KojiHelper.tagged_packages_cache[tag]

    def get_tags(self):
        """Return a list of all tag names"""
        if not KojiHelper.tags_cache:
            data = self.kojisession.listTags(None, None)
            KojiHelper.tags_cache = [x['name'] for x in data]
        return KojiHelper.tags_cache

    def get_task_state(self, task_id):
        """Return the symbolic state of the task (e.g. OPEN, CLOSED, etc.) as a string"""
        return self.TASK_STATES[self.kojisession.getTaskInfo(task_id)['state']]

    def regen_repos(self, tags_to_regen):
        """Regenerate the repos corresponding to the given tags.
        'tags_to_regen' is a list of strings.
        Waits for completion.

        """
        for tag in tags_to_regen:
            self.watch_tasks([self.regen_repo(tag)])


#
# JIRA writing
#


def write_jira(kojihelper, promoted_builds, routes, out=None):
    """Write input suitable for embedding into a JIRA ticket.
    """
    # Format
    # | TAG | build-1.2.osg31.el5 |
    out = out or sys.stdout
    out.write("*Promotions*\n")
    nvrs_no_dist = set()
    table = "|| Tag || Build ||\n"
    for tag in sorted(promoted_builds):
        for build in promoted_builds[tag]:
            uri = kojihelper.get_build_uri(build.nvr)
            table += "| %s | [%s|%s] |\n" % (tag, build.nvr, uri)
            nvrs_no_dist.add(build.nvr_no_dist)
    out.write("Promoted %s to %s\n" % (", ".join(sorted(nvrs_no_dist)), ", ".join([x.to_tag_hint % "el*" for x in routes])))
    out.write(table)


#
# Command line and main
#

def format_valid_routes(valid_routes):
    formatted = ""
    for route_name in sorted(valid_routes.keys()):
        route = valid_routes[route_name]
        dvers_list = ', '.join(sorted(route.dvers))
        if route.extra_dvers:
            dvers_list += ', [' + ','.join(sorted(route.extra_dvers)) + ']'
        formatted += " - %-20s: %-26s -> %-26s (%s)\n" % (
            route_name,
            route.from_tag_hint % '*',
            route.to_tag_hint % '*',
            dvers_list
        )
    return formatted


def parse_cmdline_args(all_dvers, valid_routes, argv):
    """Return a tuple of (options, positional args)"""
    helpstring = "%prog [-r|--route ROUTE]... [options] <packages or builds>"
    helpstring += "\n\nValid routes are:\n"
    helpstring += format_valid_routes(valid_routes)

    parser = OptionParser(helpstring)

    parser.add_option("-r", "--route", dest="routes", action="append",
                      help="The promotion route to use. May be specified multiple times. "
                      "If not specified, will use the %r route. Multiple routes may also "
                      "be separated by commas" % DEFAULT_ROUTE)
    parser.add_option("-n", "--dry-run", action="store_true", default=False,
                      help="Do not promote, just show what would be done")
    parser.add_option("--ignore-rejects", dest="ignore_rejects", action="store_true", default=False,
                      help="Ignore rejections due to version mismatch between dvers or missing package for one dver")
    parser.add_option("--regen", default=False, action="store_true",
                      help="Regenerate repo(s) afterward")
    parser.add_option("-y", "--assume-yes", action="store_true", default=False,
                      help="Do not prompt before promotion")
    for dver in all_dvers:
        parser.add_option("--%s-only" % dver, action="store_const", dest="only_dver", const=dver, default=None,
                          help="Promote only %s builds" % dver)
        parser.add_option("--no-%s" % dver, "--no%s" % dver, dest="no_dvers", action="append_const", const=dver, default=[],
                          help="Do not promote %s builds, even if they are default for the route(s)" % dver)
        parser.add_option("--%s" % dver, dest="extra_dvers", action="append_const", const=dver, default=[],
                          help="Promote %s builds if the route(s) support them" % dver)

    if len(argv) < 2:
        parser.print_help()
        sys.exit(2)

    options, args = parser.parse_args(argv[1:])

    options.routes = _get_wanted_routes(valid_routes, parser, options)

    return options, args


def _get_wanted_routes(valid_routes, parser, options):
    matched_routes = []

    routes = options.routes
    if routes:
        expanded_routes = []
        for route in routes:
            if route.find(',') != -1:  # We have a comma -- this means multiple routes.
                expanded_routes.extend(route.split(','))
            else:
                expanded_routes.append(route)
        # User is allowed to specify the shortest unambiguous prefix of a route
        for route in expanded_routes:
            if route in valid_routes.keys():
                # exact match
                matched_routes.append(route)
                continue
            matching_routes = [x for x in valid_routes.keys() if x.startswith(route)]
            if len(matching_routes) > 1:
                parser.error("Ambiguous route %r. Matching routes are: %s" % (route, ", ".join(matching_routes)))
            elif not matching_routes:
                parser.error("Invalid route %r. Valid routes are: %s" % (route, ", ".join(valid_routes.keys())))
            else:
                matched_routes.append(matching_routes[0])
    else:
        matched_routes = [DEFAULT_ROUTE]
    return matched_routes


def _print_route_dvers(routename, route):
    printf("The default dver(s) for %s are: %s", routename, ", ".join(route.dvers))
    if route.extra_dvers:
        printf("The route optionally supports these dver(s): %s", ", ".join(route.extra_dvers))


def main(argv=None):
    if argv is None:
        argv = sys.argv

    kojihelper = KojiHelper(False)

    valid_routes = load_routes(INIFILE)

    options, pkgs_or_builds = parse_cmdline_args(kojihelper.get_all_dvers(), valid_routes, argv)

    routenames = options.routes

    route_dvers_pairs = _get_route_dvers_pairs(routenames, valid_routes, options.extra_dvers, options.no_dvers,
                                               options.only_dver)

    if not options.dry_run:
        kojihelper.login_to_koji()

    for route, dvers in route_dvers_pairs:
        printf("Promoting from %s to %s for dvers: %s",
               route.from_tag_hint % 'el*',
               route.to_tag_hint % 'el*',
               ", ".join(sorted(dvers)))
    printf("Examining the following packages/builds:\n%s", "\n".join(["'" + x + "'" for x in pkgs_or_builds]))

    dvers = set()
    for _, x in route_dvers_pairs:
        dvers.update(x)
    promoter = Promoter(kojihelper, route_dvers_pairs)
    for pkgb in pkgs_or_builds:
        promoter.add_promotion(pkgb, options.ignore_rejects)

    if promoter.rejects:
        print "Rejected package or builds:\n" + "\n".join([str(x) for x in promoter.rejects])
        print "Rejects will not be promoted! Rerun with --ignore-rejects to promote them anyway."

    print "Promotion plan:"
    if any(promoter.tag_pkg_args.values()):
        text_args = {}
        for tag, builds in promoter.tag_pkg_args.items():
            text_args[tag] = [x.nvr for x in builds]
        print_table(text_args)
    else:
        printf("Nothing will be promoted!")
        return 1

    question = "Proceed with promoting the builds?"
    try:
        proceed = (options.assume_yes or not sys.stdin.isatty() or utils.ask_yn(question))
    except KeyboardInterrupt:
        printf("Canceled.")
        return 3

    if proceed:
        promoted_builds = promoter.do_promotions(options.dry_run, options.regen)
        if not options.dry_run:
            printf("\nJIRA code for this set of promotions:\n")
            write_jira(kojihelper, promoted_builds, [x[0] for x in route_dvers_pairs])
    else:
        printf("Not proceeding.")
        return 1

    return 0


def _get_route_dvers_pairs(routenames, valid_routes, extra_dvers, no_dvers, only_dver):
    route_dvers_pairs = []

    for routename in routenames:
        route = valid_routes[routename]

        if only_dver:
            if only_dver in route.dvers or only_dver in route.extra_dvers:
                route_dvers_pairs.append((route, set([only_dver])))
            else:
                printf("The dver %s is not available for route %s.", only_dver, routename)
                _print_route_dvers(routename, route)
                sys.exit(2)
            continue

        wanted_dvers_for_route = set(route.dvers)
        for extra_dver in extra_dvers:
            if extra_dver in route.extra_dvers:
                wanted_dvers_for_route.add(extra_dver)
        for no_dver in no_dvers:
            wanted_dvers_for_route.discard(no_dver)
        if not wanted_dvers_for_route:
            printf("All dvers for route %s have been disabled.")
            _print_route_dvers(routename, route)
            sys.exit(2)

        route_dvers_pairs.append((route, wanted_dvers_for_route))

    return route_dvers_pairs


if __name__ == "__main__":
    sys.exit(main(sys.argv))
