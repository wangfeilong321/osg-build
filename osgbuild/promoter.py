"""A package promotion script for OSG"""




import re
import sys
import time
import logging

from osgbuild import constants
from osgbuild import kojiinter
from osgbuild import utils
from osgbuild.utils import printf, print_table
from optparse import OptionParser


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


class Route(object):
    def __init__(self, from_tag_hint, to_tag_hint, repo):
        self.from_tag_hint = from_tag_hint
        self.to_tag_hint = to_tag_hint
        self.repo = repo
        # for compatibility:
        self.listform = [from_tag_hint, to_tag_hint, repo]

    def __getitem__(self, key):
        return self.listform[key]

class Reject(object):
    REASON_NOMATCHING_FOR_DVER = "No build matching %(pkg_or_build)s for dver %(dver)s"
    REASON_DISTINCT_ACROSS_DVERS = "Build versions matching %(pkg_or_build)s distinct across dvers"
    def __init__(self, pkg_or_build, dver, reason):
        self.pkg_or_build = pkg_or_build
        self.dver = dver
        self.reason = reason

    def str(self):
        return self.reason % {'pkg_or_build': self.pkg_or_build, 'dver': self.dver}

STATIC_ROUTES = {
    "hcc": Route("hcc-%s-testing", "hcc-%s-release", "hcc"),
    "old-upcoming": Route("%s-osg-upcoming-development", "%s-osg-upcoming-testing", "osg"),
    "old-testing": Route("%s-osg-development", "%s-osg-testing", "osg"),
    "old-contrib": Route("%s-osg-testing", "%s-osg-contrib", "osg"),
    "new-upcoming": Route("osg-upcoming-%s-development", "osg-upcoming-%s-testing", "osg"),
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
        return ()

def split_dver(build):
    """Split out the dver from the NVR of 'build'.
    For example, split_dver("foobar-1-1.osg.el5") returns
    ("foobar-1-1.osg", "el5")
    Return the empty string for the dver if it's not in the NVR.

    """
    pattern = re.compile(r"\.(el\d+)$")
    nvr_no_dver = pattern.sub("", build)
    dver = pattern.search(build)
    return (nvr_no_dver, dver and dver.group(1) or "")

def trim_dver(build):
    """Remove the dver from the NVR of 'build'"""
    return split_dver(build)[0]

def split_repo_dver(build):
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

    """
    build_no_dist = build
    repo = ""
    dver = ""

    # order matters since later patterns are less specific and would match more
    pat_1_repo_and_dver = re.compile(r"(?P<build_no_dist>.+)\.(?P<repo>\w+)\.(?P<dver>el\d+)$")
    pat_2_dver_only = re.compile(r"(?P<build_no_dist>.+)\.(?P<dver>el\d+)$")
    pat_3_repo_only = re.compile(r"(?P<build_no_dist>.+)\.(?P<repo>\w+)$")

    match = pat_1_repo_and_dver.match(build) or \
            pat_2_dver_only.match(build) or \
            pat_3_repo_only.match(build)

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
        for route_name, route in routes.iteritems():
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
                                osgver + "-contrib": (testing_tag_hint, contrib_tag_hint, 'osg' + osgshortver)}

            for route_name, route in potential_routes.iteritems():
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
        These aliases are 'testing', 'upcoming', and 'contrib'.

        If the old OSG routes (e.g. 'old-testing') are still valid, the aliases
        will point to the old OSG routes.

        Otherwise, if versioned OSG routes exist, then 'testing' and 'contrib'
        are aliases to the newest testing and contrib routes (e.g.
        'osg-3.2-%s-development') and 'upcoming' is an alias for the
        'new-upcoming' route in STATIC_ROUTES.

        Assumes routes have been validated.

        """
        osg_route_aliases = {}

        for route_base in ['testing', 'contrib']:
            old_route = 'old-%s' % route_base
            if self.get_dvers_for_route_by_name(old_route, STATIC_ROUTES):
                osg_route_aliases[route_base] = STATIC_ROUTES[old_route]
            elif valid_versioned_osg_routes:
                highest_route = self._get_highest_route(route_base, valid_versioned_osg_routes)
                if highest_route:
                    osg_route_aliases[route_base] = valid_versioned_osg_routes[highest_route]
                else:
                    raise KojiTagsAreMessedUp("No OSG route found for %s" % route_base)

        if self.get_dvers_for_route_by_name('old-upcoming', STATIC_ROUTES):
            osg_route_aliases['upcoming'] = STATIC_ROUTES['old-upcoming']
        elif self.get_dvers_for_route_by_name('new-upcoming', STATIC_ROUTES):
            osg_route_aliases['upcoming'] = STATIC_ROUTES['new-upcoming']

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

        pattern = re.compile("(\d+\.\d+)-%s" % route_base)
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
    def __init__(self, kojihelper, route, dvers):
        """kojihelper is an instance of KojiHelper. route is a
        (from_tag_hint, to_tag_hint pair). dvers is a list of strings.
        """
        self.tag_pkg_args = {}
        self.rejects = []
        self.from_tag_hint, self.to_tag_hint, self.repo = route
        self.dvers = dvers
        self.kojihelper = kojihelper


    def add_promotion(self, pkg_or_build, ignore_rejects=False):
        """Run get_builds() for 'pkg_or_build', using from_tag_hint as the
        tag hint.
        Returns nothing; builds to promote are added to tag_pkg_args, which
        is a dict keyed by tag (actual tag, not tag hint) of koji builds that
        should be added to that tag.

        """
        builds = self.get_builds(self.from_tag_hint, self.dvers, pkg_or_build, ignore_rejects=ignore_rejects)
        for dver in builds:
            to_tag = self.get_to_tag_for_dver(dver)

            build = builds[dver]
            self.tag_pkg_args.setdefault(to_tag, [])
            self.tag_pkg_args[to_tag].append(build)

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
        for tag, builds in self.tag_pkg_args.iteritems():
            for build in builds:
                try:
                    # Make sure the build isn't already in tag
                    if build in self.kojihelper.get_tagged_builds(tag):
                        printf("Skipping %s, already in %s", build, tag)
                        continue
                except KeyError:
                    pass

                build_no_dver, dver = split_dver(build)
                # Launch the builds
                if not dry_run:
                    task_id = self.kojihelper.tag_build(tag, build)
                    tasks[task_id] = (build_no_dver, dver, build)
                else:
                    printf("tagBuild('%s', '%s')", tag, build)

        if not dry_run:
            promoted_builds = self.watch_builds(tasks)

            if regen:
                print "--- Regenerating repos"
                self.kojihelper.regen_repos(tags_to_regen=self.tag_pkg_args.keys())

            return promoted_builds

    def get_builds(self, tag_hint, dvers, pkg_or_build, ignore_rejects=False):
        """Get a dict of builds keyed by dver for pkg_or_build.
        If pkg_or_build is a package, then it gets the latest version of the
        package in tag_hint % dver.
        If pkg_or_build is a build, then it uses that specific version.
        Note that latest is defined as the build most recently tagged into
        tag_hint % dver, NOT the newest version.

        If ignore_rejects is False, then it can reject packages if
        either of the following apply:
        * pkg_or_build is a build and a build with that NVR is missing from
          at least one dver. (For example, pkg_or_build is foobar-1-1.osg.el5,
          and foobar-1-1.osg.el5 exists but foobar-1-1.osg.el6 doesn't).
        * pkg_or_build is a package and the NVRs of the latest version of that
          package are different across NVRs. (For example, pkg_or_build is
          foobar, el5 has foobar-1-1.osg.el5, and el6 has foobar-1-2.osg.el6).
        In either of those cases, neither the el5 or the el6 builds should be
        promoted.

        In case of a rejection (or no matching packages found at all), an
        empty dict is returned.
        """
        builds = {}
        # Find each build for all dvers matching pkg_or_build
        for dver in dvers:
            tag = self.get_valid_tag_for_dver(tag_hint, dver)

            pkg_or_build_no_dver = trim_dver(pkg_or_build)
            # Case 1: pkg_or_build is a build, in which case take off its dver
            # and put the current dver on, then find a build for that.
            build1 = self.kojihelper.get_build_in_tag(tag, "%s.%s" % (pkg_or_build_no_dver, dver))
            # Case 2: pkg_or_build is a package, in which case putting a dver
            # on doesn't help--just find the latest build in the tag.
            build2 = self.kojihelper.get_build_in_tag(tag, pkg_or_build_no_dver)

            build = build1 or build2
            if not build:
                log.warning("There is no build matching %s for dver %s.", pkg_or_build, dver)
                if not ignore_rejects:
                    log.warning("Rejected package.")
                    self.rejects.append(Reject(pkg_or_build, dver, Reject.REASON_NOMATCHING_FOR_DVER))
                    return {}
                else:
                    continue
            builds[dver] = build

        if len(builds) == 0:
            return {}
        # find builds where the VERSION-RELEASEs (without dver) are distinct
        # between the dvers we are running the script for, and reject them.
        vrs = ['-'.join(split_nvr(builds[x])[1:]) for x in builds]
        vrs_no_dver = [trim_dver(x) for x in vrs]
        if len(set(vrs_no_dver)) > 1:
            log.warning("The versions of the builds matching %s are distinct across dvers.", pkg_or_build)
            if not ignore_rejects:
                log.warning("Rejected package.")
                self.rejects.append(Reject(pkg_or_build, dver, Reject.REASON_DISTINCT_ACROSS_DVERS))
                return {}
        return builds

    def get_valid_tag_for_dver(self, tag_hint, dver):
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

    def get_from_tag_for_dver(self, dver):
        """Convenience function to get the actual tag we will tag a build from
        given the dver."""
        return self.get_valid_tag_for_dver(self.from_tag_hint, dver)

    def get_to_tag_for_dver(self, dver):
        """Convenience function to get the actual tag we will tag a build into
        given the dver."""
        return self.get_valid_tag_for_dver(self.to_tag_hint, dver)

    def get_rejects(self):
        return self.rejects

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
        for task_id in tasks:
            build_no_dver, dver, build = tasks[task_id]
            if self.kojihelper.get_task_state(task_id) == 'CLOSED':
                promoted_builds.setdefault(build_no_dver, dict())
                promoted_builds[build_no_dver][dver] = build
            else:
                printf("* Error promoting build %s", build)

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
        pat = re.compile("(?:\b|^)(el\d+)")
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

    def get_build_uri(self, build):
        """Return a URI to the kojiweb page of the build with the given NVR"""
        buildinfo = self.kojisession.getBuild(build)
        return ("%s/koji/buildinfo?buildID=%d" % (constants.HTTPS_KOJI_HUB, int(buildinfo['id'])))

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
# TWiki writing
#
def write_twiki(kojihelper, promoter, builds, dvers, output_format, no_date=False, out=None):
    """Print TWiki code for the promoted builds, with links.
    'kojihelper' is an instance of KojiHelper.
    'promoter' is an instance of Promoter.
    'builds' is data structure returned by Promoter.do_promotions.
    'dvers' is a list of strings.
    'output_format' can be:
        'relnote', in which case a bulleted list grouped by dver is printed.
        'old' or 'prerelnote', in which case table rows are printed, one for
        each set of builds, with an optional date (if no_date is False).
    'out' is an output stream (such as a file). sys.stdout is used if not
    specified.
    """
    out = out or sys.stdout
    if output_format == 'relnote':
        write_releasenotes(kojihelper, out, builds, dvers)
    elif output_format == 'old' or output_format == 'prerelnote':
        write_prereleasenotes(kojihelper, out, builds, no_date)
    elif output_format == 'jira':
        write_jira(kojihelper, promoter, out, builds, dvers)
    elif output_format == 'none':
        pass
    else:
        # Sanity check, but optparse should have caught this already
        print >> sys.stderr, "Unknown output format!"


def write_jira(kojihelper, promoter, out, builds, dvers):
    """Write input suitable for embedding into a JIRA ticket. See write_twiki()"""
    # Format
    # | TAG | build-1.2.osg31.el5 |
    out.write("*Promotions*\n")
    out.write("|| Tag || Build ||\n")
    for dver in dvers:
        for build_no_dver in sorted(builds):
            build = builds[build_no_dver][dver]
            tag = promoter.get_to_tag_for_dver(dver)
            uri = kojihelper.get_build_uri(build)
            out.write("| %s | [%s|%s] |\n" % (tag, build, uri))

def write_releasenotes(kojihelper, out, builds, dvers):
    """Write twiki output in release note format. See write_twiki()"""
    # Release note format
    #    * build-1-2.osg.el5
    #    * build-1-2.osg.el6
    for dver in dvers:
        for build_no_dver in sorted(builds):
            build = builds[build_no_dver][dver]
            out.write("   * [[%s][%s]]\n" % (kojihelper.get_build_uri(build), build))

def write_prereleasenotes(kojihelper, out, builds, no_date):
    """Write twiki output in PreReleaseNotes format. See write_twiki()"""
    # PreReleaseNotes format
    # | DATE | build-1-2.osg (el5+el6) |
    first = True
    for build_no_dver in sorted(builds):
        if first and not no_date:
            out.write("| %s |" % time.strftime("%Y-%m-%d"))
            first = False
        else:
            out.write("||")
        out.write(" %(build_no_dver)s " % locals())
        build_links = []
        for dver in sorted(builds[build_no_dver]):
            build = builds[build_no_dver][dver]
            build_links.append("[[%s][%s]]" % (kojihelper.get_build_uri(build), dver))
        out.write("(" + "+".join(build_links) + ")")
        out.write(" |\n")


#
# Command line and main
#

def parse_cmdline_args(all_dvers, valid_routes, argv):
    """Return a tuple of (options, positional args)"""
    helpstring = "%prog -r|--route ROUTE [options] <packages or builds>"
    helpstring += "\n\nValid routes are:\n"
    for route in sorted(valid_routes.keys()):
        helpstring += " - %-14s: %-30s -> %s\n" % (
            route, valid_routes[route][0] % '*', valid_routes[route][1] % '*')
    parser = OptionParser(helpstring)

    parser.add_option("-r", "--route", default="testing", type='choice', choices=valid_routes.keys(),
                      help="The promotion route to use.")
    parser.add_option("-n", "--dry-run", action="store_true", default=False,
                      help="Do not promote, just show what would be done")
    parser.add_option("--ignore-rejects", dest="ignore_rejects", action="store_true", default=False,
                      help="Ignore rejections due to version mismatch between dvers or missing package for one dver")
    of_choices = ['old', 'prerelnote', 'relnote', 'jira', 'none']
    parser.add_option("--output-format", "--of", default='jira', type='choice', choices=of_choices,
                      help="Valid output formats are: " + ", ".join(of_choices))
    parser.add_option("--no-date", "--nodate", default=False, action="store_true",
                      help="Do not add the date to the wiki code")
    parser.add_option("--regen", default=False, action="store_true",
                      help="Regenerate repo(s) afterward")
    parser.add_option("-y", "--assume-yes", action="store_true", default=False,
                      help="Do not prompt before promotion")
    for dver in all_dvers:
        parser.add_option("--%s-only" % dver, action="store_true", default=False,
                          help="Promote only %s builds" % dver)
        parser.add_option("--no-%s" % dver, "--no%s" % dver, action="store_true", default=False,
                          help="Do not promote %s builds" % dver)

    if len(argv) < 2:
        parser.print_help()
        sys.exit(2)

    options, args = parser.parse_args(argv[1:])

    options.dvers = _get_wanted_dvers(all_dvers, parser, options)
    if not options.dvers:
        parser.error("No dvers found to promote")

    if options.route:
        # User is allowed to specify the shortest unambiguous prefix of a route
        matching_routes = [x for x in valid_routes.keys() if x.startswith(options.route)]
        if len(matching_routes) > 1:
            parser.error("Ambiguous route. Matching routes are: " + ", ".join(matching_routes))
        elif not matching_routes:
            parser.error("Invalid route. Valid routes are: " + ", ".join(valid_routes.keys()))
        else:
            options.route = matching_routes[0]
    else:
        parser.error("Missing required parameter '--route'")

    return (options, args)

def _get_wanted_dvers(all_dvers, parser, options):
    """Helper for parse_cmdline_args. Looks at the --dver-only (e.g. --el5-only)
    and --no-dver (e.g. --no-el5) arguments the user may have specified and
    returns a list of the dvers we actually want to promote for.

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
        wanted_dvers = list(only_dvers)

    # Now go through any --no-dvers (e.g. --no-el5) the user specified.
    for dver in all_dvers:
        if getattr(options, "no_%s" % dver):
            if dver in only_dvers:
                parser.error("Can't specify both --no-%s and --%s-only" % (dver, dver))
            else:
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
    route = options.route

    dvers_for_route = route_discovery.get_dvers_for_route_by_name(route)
    for dver in dvers:
        if dver not in dvers_for_route:
            printf("The dver %s is not available for route %s.", dver, route)
            printf("The available dvers for that route are: %s", ", ".join(dvers_for_route))
            sys.exit(1)

    if not options.dry_run:
        kojihelper.login_to_koji()

    printf("Promoting from %s to %s for dvers: %s",
           valid_routes[route][0] % 'el*',
           valid_routes[route][1] % 'el*',
           ", ".join(dvers))
    printf("Examining the following packages/builds:\n%s", "\n".join(["'" + x + "'" for x in pkgs_or_builds]))

    promoter = Promoter(kojihelper, valid_routes[route], dvers)
    for pkgb in pkgs_or_builds:
        promoter.add_promotion(pkgb, options.ignore_rejects)

    if promoter.rejects:
        print "Rejected package or builds:\n" + "\n".join(promoter.rejects)
        print "Rejects will not be promoted! Rerun with --ignore-rejects to promote them anyway."

    print "Promotion plan:"
    if any(promoter.tag_pkg_args.values()):
        print_table(promoter.tag_pkg_args)
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
        if not options.dry_run and options.output_format != 'none':
            printf("\nJIRA / Twiki code for this set of promotions:\n")
            write_twiki(kojihelper, promoter, promoted_builds, dvers, options.output_format, options.no_date)
    else:
        printf("Not proceeding.")
        return 1

    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))

