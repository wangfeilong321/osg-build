"""A package promotion script for OSG"""




import re
import sys
import logging

from osgbuild import constants
from osgbuild import kojiinter
from osgbuild import utils
from osgbuild.utils import printf, print_table
from optparse import OptionParser

try:
    from collections import namedtuple
except ImportError:
    from osgbuild.namedtuple import namedtuple

DEFAULT_ROUTE = 'testing'
DVERS_OFF_BY_DEFAULT = ['el7']

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


Route = namedtuple('Route', ['from_tag_hint', 'to_tag_hint', 'repo'])

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

STATIC_ROUTES = {
    "hcc": Route("hcc-%s-testing", "hcc-%s-release", "hcc"),
    "upcoming": Route("osg-upcoming-%s-development", "osg-upcoming-%s-testing", "osgup"),
   }

#
# Utility functions
#

def any(iterable): # Don't warn about redefining this. pylint: disable=W0622
    """True if any member of 'iterable' is true, False otherwise"""
    for element in iterable:
        if element:
            return True
    return False

def split_nvr(build):
    """Split an NVR into a (Name, Version, Release) tuple"""
    match = re.match(r"(?P<name>.+)-(?P<version>[^-]+)-(?P<release>[^-]+)$", build)
    if match:
        return (match.group('name'), match.group('version'), match.group('release'))
    else:
        return ('', '', '')

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

    return (build_no_dist, repo, dver)


class RouteDiscovery(object):
    """For discovering and validating promotion routes.
    In addition to including the predefined routes (from STATIC_ROUTES),
    also looks for new-style osg routes (with tags of the form
    osg-M.N-elX-{development,testing,contrib} or
    osg-upcoming-elX-{development,testing}), and discovers which dvers
    each route is good for.

    Some terminology:
      * 'dver' is 'el5', 'el6', 'el7', etc.
      * 'osgver' is '3.1', '3.2', etc.
      * a tag_hint is the name of a koji tag with %s where the dver would go,
        e.g. '%s-upcoming-development'
      * a 'repo' is the first part of a new-style dist tag, e.g. 'hcc', 'osg31'
      * a route contains a from_tag_hint, to_tag_hint, and repo
      * a route is valid for a dver if tags exist for both the from_tag and the
        to_tag (obtained by filling in the dver for the tag_hints)
      * a route is valid if it is valid for any dver
    """

    def __init__(self, tags):
        """'tags' is a list of strings, generally produced by running
        KojiHelper.get_tags(). Can raise KojiTagsAreMessedUp if problems are
        found with the tags.
        """
        self.tags = tags
        self.routes = self.discover_routes()
        self.dvers_for_routes = self.discover_dvers_for_routes(self.routes)

    def discover_routes(self):
        """Create and return a dict of routes keyed by a name (e.g. 'testing').
        Includes both statically defined routes and OSG routes.

        """
        routes = STATIC_ROUTES.copy()
        routes.update(self.get_valid_osg_routes())
        return routes

    def discover_dvers_for_routes(self, routes):
        """Find and return a dict of dvers for given routes. Values are keyed
        by the route name (i.e. the key in routes)

        """
        dvers_for_routes = {}
        to_remove = []
        for route_name, route in routes.items():
            dvers = self.get_dvers_for_route(route)
            if not dvers:
                to_remove.append(route_name)
            else:
                dvers_for_routes[route_name] = dvers
        for route_name in to_remove:
            del routes[route_name]
        return dvers_for_routes

    def get_routes(self):
        """Return a dict of routes that were discovered."""
        return self.routes

    def get_dvers_for_route(self, route):
        """Return the dvers (as a list of strings) a route supports"""
        tag_pattern = re.compile(re.sub(r'%s', r'(el\d+)', route[0]))
        available_tags = [x for x in self.tags if tag_pattern.match(x)]
        dver_pattern = re.compile(r'(el\d+)')
        available_dvers = []
        for tag in available_tags:
            match = dver_pattern.search(tag)
            if match:
                available_dvers.append(match.group(1))
        return available_dvers

    def get_dvers_for_route_by_name(self, route_name, routes=None):
        """Return the dvers (as a list of strings) a route supports
        'route_name' is a key in the 'routes' dictionary.

        """
        routes = routes or self.routes
        route = routes[route_name]
        return self.get_dvers_for_route(route)

    def get_valid_osg_routes(self):
        """Return a dictionary of the valid OSG routes.
        This includes the versioned routes (e.g. '3.1-testing') as well
        as short aliases for them (e.g. 'testing' for the newest versioned
        route for testing).

        """
        valid_osg_routes = self.get_valid_versioned_osg_routes()
        valid_osg_routes.update(self.get_osg_route_aliases(valid_osg_routes))

        return valid_osg_routes

    def get_valid_versioned_osg_routes(self):
        """Return a dict of versioned OSG routes (e.g. '3.1-testing').
        All routes are validated (see validate_route_for_dver()).

        """
        valid_versioned_osg_routes = {}
        for osgver, dver in self._get_osgvers_dvers():
            devel_tag_hint = "osg-%s-%%s-development" % (osgver)
            contrib_tag_hint = "osg-%s-%%s-contrib" % (osgver)
            testing_tag_hint = "osg-%s-%%s-testing" % (osgver)
            osgshortver = osgver.replace('.', '')

            potential_routes = {osgver + "-testing": (devel_tag_hint, testing_tag_hint, 'osg' + osgshortver),
                                osgver + "-contrib": (devel_tag_hint, contrib_tag_hint, 'osg' + osgshortver)}

            for route_name, route in potential_routes.items():
                self.validate_route_for_dver(route, dver)
                valid_versioned_osg_routes[route_name] = Route(route[0], route[1], route[2])

        return valid_versioned_osg_routes

    def _get_osgvers_dvers(self):
        """Helper for get_valid_versioned_osg_routes
        Finds osg-testing tags (i.e. tags like 'osg-3.1-el5-testing') and
        returns a list of tuples containing:
        - the OSG major version as a string (e.g. '3.1')
        - the dver of the tag as a string (e.g. 'el5')

        """
        osg_testing_pattern = re.compile(r"^osg-(\d+\.\d+)-(el\d+)-testing$")
        osgvers_dvers = []
        for tag in self.tags:
            match = osg_testing_pattern.search(tag)
            if match:
                osgvers_dvers.append(match.group(1, 2))
        return osgvers_dvers

    def get_osg_route_aliases(self, valid_versioned_osg_routes):
        """Get a dict of route aliases for the OSG routes.
        These aliases are 'testing' and 'contrib'; they are aliases to the
        newest testing and contrib routes (e.g.  'osg-3.2-%s-development').

        Assumes routes have been validated.

        """
        osg_route_aliases = {}

        for route_base in ['testing', 'contrib']:
            highest_route = self._get_highest_route(route_base, valid_versioned_osg_routes)
            if highest_route:
                osg_route_aliases[route_base] = valid_versioned_osg_routes[highest_route]
            else:
                raise KojiTagsAreMessedUp("No OSG route found for %s" % route_base)

        return osg_route_aliases

    # don't care that this can be a function: pylint: disable=R0201
    def _get_highest_route(self, route_base, valid_osg_routes):
        """Helper for get_osg_route_aliases.
        Return the versioned OSG route matching route_base with the highest
        version. For example, if the available routes are '3.1-testing' and
        '3.2-testing', and route_base is 'testing', then this will return
        '3.2-testing'.

        Returns None if no such route exists.

        """
        def _cmp_version(a, b): # pylint: disable=C0103,C0111
            return cmp(a.split('.'), b.split('.'))

        pattern = re.compile(r"(\d+\.\d+)-%s" % route_base)
        osgvers_for_route_base = [pattern.match(x).group(1) for x in valid_osg_routes if pattern.match(x)]
        if osgvers_for_route_base:
            highest_osgver = sorted(osgvers_for_route_base, cmp=_cmp_version)[-1]
            return '%s-%s' % (highest_osgver, route_base)
        else:
            return None

    def validate_route_for_dver(self, route, dver):
        """Check that both sides of a route exist for the given dver.
        Returns nothing; raises KojiTagsAreMessedUp if validation fails.

        """
        errors = []
        for tag_hint in route[0:2]:
            tag = tag_hint % dver
            if tag not in self.tags:
                errors.append("%s is missing" % tag)
        if errors:
            raise KojiTagsAreMessedUp("Error validating route %s: %s" % (route, "; ".join(errors)))

    def validate_route_by_name_for_dver(self, route_name, dver):
        """See validate_route_for_dver; 'route_name' is a key in the 'routes' dict."""
        return self.validate_route_for_dver(self.routes[route_name], dver)


class Promoter(object):
    """For promoting sets of packages.
    Usage is to add packages or builds via add_promotion and then call
    do_promotions to actually promote.
    do_promotions should not be called twice.

    """
    def __init__(self, kojihelper, routes, dvers):
        """kojihelper is an instance of KojiHelper. routes is a list of Route objects. dvers is a list of strings.
        """
        self.tag_pkg_args = {}
        self.rejects = []
        if isinstance(routes, Route):
            self.routes = [routes]
        elif isinstance(routes, list) or isinstance(routes, tuple):
            self.routes = list(routes)
        else:
            raise TypeError("Unexpected type for routes: %s" % type(routes))
        self.dvers = dvers
        self.kojihelper = kojihelper
        self.repos = set(route.repo for route in self.routes)


    def add_promotion(self, pkg_or_build, ignore_rejects=False):
        """Run get_builds() for 'pkg_or_build', using from_tag_hint as the
        tag hint.
        Returns nothing; builds to promote are added to tag_pkg_args, which
        is a dict keyed by tag (actual tag, not tag hint) of koji builds that
        should be added to that tag.

        """
        tag_build_pairs = []
        for route in self.routes:
            builds = self.get_builds(route, self.dvers, pkg_or_build, ignore_rejects)
            for dver in builds:
                to_tag = route.to_tag_hint % dver
                tag_build_pairs.append((to_tag, builds[dver]))

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
        return ("%s/koji/buildinfo?buildID=%d" % (constants.HTTPS_KOJI_HUB, int(buildinfo['id'])))

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

def parse_cmdline_args(all_dvers, valid_routes, argv):
    """Return a tuple of (options, positional args)"""
    helpstring = "%prog [-r|--route ROUTE]... [options] <packages or builds>"
    helpstring += "\n\nValid routes are:\n"
    for route in sorted(valid_routes.keys()):
        helpstring += " - %-14s: %-30s -> %s\n" % (
            route, valid_routes[route][0] % '*', valid_routes[route][1] % '*')

    parser = OptionParser(helpstring)

    parser.add_option("-r", "--route", dest="routes", action="append",
                      help="The promotion route to use. May be specified multiple times."
                      "If not specified, will use the %r route. Multiple routes may also "
                      "be separated by commas." % DEFAULT_ROUTE)
    parser.add_option("-n", "--dry-run", action="store_true", default=False,
                      help="Do not promote, just show what would be done")
    parser.add_option("--ignore-rejects", dest="ignore_rejects", action="store_true", default=False,
                      help="Ignore rejections due to version mismatch between dvers or missing package for one dver")
    parser.add_option("--regen", default=False, action="store_true",
                      help="Regenerate repo(s) afterward")
    parser.add_option("-y", "--assume-yes", action="store_true", default=False,
                      help="Do not prompt before promotion")
    for dver in all_dvers:
        parser.add_option("--%s-only" % dver, action="store_true", default=False,
                          help="Promote only %s builds" % dver)
        if dver not in DVERS_OFF_BY_DEFAULT:
            parser.add_option("--no-%s" % dver, "--no%s" % dver, action="store_true", default=False,
                              help="Do not promote %s builds" % dver)
        else:
            parser.add_option("--%s" % dver, dest="no_%s" % dver, action="store_false", default=True)

    if len(argv) < 2:
        parser.print_help()
        sys.exit(2)

    options, args = parser.parse_args(argv[1:])

    options.dvers = _get_wanted_dvers(all_dvers, parser, options)
    if not options.dvers:
        parser.error("No dvers found to promote")

    matched_routes = []
    if options.routes:
        expanded_routes = []
        for route in options.routes:
            if route.find(',') != -1: # We have a comma -- this means multiple routes.
                expanded_routes.extend(route.split(','))
            else:
                expanded_routes.append(route)
        # User is allowed to specify the shortest unambiguous prefix of a route
        for route in expanded_routes:
            matching_routes = [x for x in valid_routes.keys() if x.startswith(route)]
            if len(matching_routes) > 1:
                parser.error("Ambiguous route %r. Matching routes are: %s" % (route, ", ".join(matching_routes)))
            elif not matching_routes:
                parser.error("Invalid route %r. Valid routes are: %s" % (route, ", ".join(valid_routes.keys())))
            else:
                matched_routes.append(matching_routes[0])
    else:
        matched_routes = [DEFAULT_ROUTE]
    options.routes = matched_routes

    return (options, args)

def _get_wanted_dvers(all_dvers, parser, options):
    """Helper for parse_cmdline_args. Looks at the --dver-only (e.g. --el5-only)
    and --no-dver (e.g. --no-el5) arguments the user may have specified and
    returns a list of the dvers we actually want to promote for.
    --elX-only arguments override --no-elX arguments.

    """
    wanted_dvers = list(all_dvers)
    # the dvers for which the user specified --dver-only (e.g. --el5-only).
    # There should be at most 1, but need to check and give appropriate error.
    only_dvers = []
    for dver in all_dvers:
        if getattr(options, "%s_only" % dver):
            only_dvers.append(dver)
    if len(only_dvers) > 1:
        bad_opt_names = ['--%s-only' % dver for dver in only_dvers]
        parser.error("Can't specify " + " and ".join(bad_opt_names))
    elif len(only_dvers) == 1:
        return list(only_dvers)

    # Now go through any --no-dvers (e.g. --no-el5) the user specified.
    for dver in all_dvers:
        if getattr(options, "no_%s" % dver):
            wanted_dvers.remove(dver)

    return wanted_dvers

def main(argv=None):
    if argv is None:
        argv = sys.argv

    kojihelper = KojiHelper(False)

    route_discovery = RouteDiscovery(kojihelper.get_tags())
    valid_routes = route_discovery.get_routes()

    options, pkgs_or_builds = parse_cmdline_args(kojihelper.get_all_dvers(), valid_routes, argv)

    dvers = options.dvers
    routes = options.routes

    for route in routes:
        dvers_for_route = route_discovery.get_dvers_for_route_by_name(route)
        for dver in dvers:
            if dver not in dvers_for_route:
                printf("The dver %s is not available for route %s.", dver, route)
                printf("The available dvers for that route are: %s", ", ".join(dvers_for_route))
                sys.exit(1)

    if not options.dry_run:
        kojihelper.login_to_koji()

    for route in routes:
        printf("Promoting from %s to %s for dvers: %s",
               valid_routes[route][0] % 'el*',
               valid_routes[route][1] % 'el*',
               ", ".join(dvers))
    printf("Examining the following packages/builds:\n%s", "\n".join(["'" + x + "'" for x in pkgs_or_builds]))

    real_routes = [valid_routes[route] for route in routes]
    promoter = Promoter(kojihelper, real_routes, dvers)
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
            write_jira(kojihelper, promoted_builds, real_routes)
    else:
        printf("Not proceeding.")
        return 1

    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))

