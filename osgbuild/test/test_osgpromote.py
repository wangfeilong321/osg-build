#!/usr/bin/env python2

import sys
sys.path.insert(0, '.')

import logging
import unittest
import StringIO

from osgbuild import promoter
from osgbuild import constants

INIFILE = 'data/promoter.ini'

log = logging.getLogger('osgpromote')
log.setLevel(logging.ERROR)

TAGS = ['el6-gt52',
        'goc-el5-itb',
        'goc-el6-itb',
        'goc-el7-itb',
        'goc-el5-production',
        'goc-el6-production',
        'goc-el7-production',
        'hcc-el5',
        'hcc-el5-release',
        'hcc-el5-testing',
        'hcc-el6',
        'hcc-el6-release',
        'hcc-el6-testing',
        'hcc-el7',
        'hcc-el7-release',
        'hcc-el7-testing',
        'osg-3.1-el5-contrib',
        'osg-3.1-el5-development',
        'osg-3.1-el5-prerelease',
        'osg-3.1-el5-release',
        'osg-3.1-el5-testing',
        'osg-3.1-el6-contrib',
        'osg-3.1-el6-development',
        'osg-3.1-el6-prerelease',
        'osg-3.1-el6-release',
        'osg-3.1-el6-testing',
        'osg-3.2-el5-contrib',
        'osg-3.2-el5-development',
        'osg-3.2-el5-prerelease',
        'osg-3.2-el5-release',
        'osg-3.2-el5-testing',
        'osg-3.2-el6-contrib',
        'osg-3.2-el6-development',
        'osg-3.2-el6-prerelease',
        'osg-3.2-el6-release',
        'osg-3.2-el6-testing',
        'osg-3.2-el7-contrib',
        'osg-3.2-el7-development',
        'osg-3.2-el7-prerelease',
        'osg-3.2-el7-release',
        'osg-3.2-el7-testing',
        'osg-3.3-el7-contrib',
        'osg-3.3-el7-development',
        'osg-3.3-el7-prerelease',
        'osg-3.3-el7-release',
        'osg-3.3-el7-testing',
        'osg-3.3-el6-contrib',
        'osg-3.3-el6-development',
        'osg-3.3-el6-prerelease',
        'osg-3.3-el6-release',
        'osg-3.3-el6-testing',
        'osg-el5',
        'osg-el6',
        'osg-upcoming-el5-development',
        'osg-upcoming-el5-prerelease',
        'osg-upcoming-el5-release',
        'osg-upcoming-el5-testing',
        'osg-upcoming-el6-development',
        'osg-upcoming-el6-prerelease',
        'osg-upcoming-el6-release',
        'osg-upcoming-el6-testing',
        'uscms-el5',
        'uscms-el6',
        ]


class TestUtil(unittest.TestCase):
    buildnvr = "osg-build-1.3.2-1.osg32.el5"
    def test_split_nvr(self):
        self.assertEqual(('osg-build', '1.3.2', '1.osg32.el5'), promoter.split_nvr(self.buildnvr))

    def test_split_repo_dver(self):
        self.assertEqual(('osg-build-1.3.2-1', 'osg32', 'el5'), promoter.split_repo_dver(self.buildnvr))
        self.assertEqual(('foo-1-1', 'osg', ''), promoter.split_repo_dver('foo-1-1.osg'))
        self.assertEqual(('foo-1-1', '', 'el5'), promoter.split_repo_dver('foo-1-1.el5'))
        self.assertEqual(('foo-1-1', '', ''), promoter.split_repo_dver('foo-1-1'))
        # Tests against SOFTWARE-1420:
        self.assertEqual(('foo-1-1', 'osg', ''), promoter.split_repo_dver('foo-1-1.osg', ['osg']))
        self.assertEqual(('bar-1-1.1', '', ''), promoter.split_repo_dver('bar-1-1.1'))
        self.assertEqual(('bar-1-1.rc1', '', ''), promoter.split_repo_dver('bar-1-1.rc1', ['osg', 'osg31', 'osg32']))


class TestRouteLoader(unittest.TestCase):
    def setUp(self):
        self.routes = promoter.load_routes(INIFILE)

    def test_static_route(self):
        self.assertEqual('hcc-%s-testing', self.routes['hcc'].from_tag_hint)
        self.assertEqual('hcc-%s-release', self.routes['hcc'].to_tag_hint)
        self.assertEqual('hcc', self.routes['hcc'].repo)
        self.assertEqual(['el5', 'el6', 'el7'], self.routes['hcc'].dvers)

    def test_osg_route(self):
        self.assertEqual('osg-3.2-%s-development', self.routes['3.2-testing'].from_tag_hint)
        self.assertEqual('osg-3.2-%s-testing', self.routes['3.2-testing'].to_tag_hint)
        self.assertEqual('osg32', self.routes['3.2-testing'].repo)

    def test_route_alias(self):
        for key in 'from_tag_hint', 'to_tag_hint', 'repo':
            self.assertEqual(getattr(self.routes['testing'], key), getattr(self.routes['3.2-testing'], key))

    def test_type(self):
        for route in self.routes.values():
            self.assertTrue(isinstance(route, promoter.Route))


class FakeKojiHelper(promoter.KojiHelper):
    tagged_builds_by_tag = {
            'osg-3.1-el5-development': [
                {'nvr': 'goodpkg-2000-1.osg31.el5', 'latest': True},
                ],
            'osg-3.1-el6-development': [
                {'nvr': 'goodpkg-2000-1.osg31.el6', 'latest': True},
                {'nvr': 'reject-distinct-repos-1-1.osg31.el6', 'latest': True},
                ],
            'osg-3.2-el5-development': [
                {'nvr': 'goodpkg-1999-1.osg32.el5', 'latest': False},
                {'nvr': 'goodpkg-2000-1.osg32.el5', 'latest': True},
                {'nvr': 'reject-distinct-dvers-1-1.osg32.el5', 'latest': True},
                ],
            'osg-3.2-el6-development': [
                {'nvr': 'goodpkg-1999-1.osg32.el6', 'latest': False},
                {'nvr': 'goodpkg-2000-1.osg32.el6', 'latest': True},
                {'nvr': 'reject-distinct-dvers-2-1.osg32.el6', 'latest': True},
                {'nvr': 'reject-distinct-repos-2-1.osg32.el6', 'latest': True},
                ],
            'osg-3.3-el6-development': [
                {'nvr': 'goodpkg-1999-1.osg33.el6', 'latest': False},
                {'nvr': 'goodpkg-2000-1.osg33.el6', 'latest': True},
                {'nvr': 'reject-distinct-dvers-2-1.osg33.el6', 'latest': True},
                {'nvr': 'reject-distinct-repos-2-1.osg33.el6', 'latest': True},
                ],
            'osg-3.3-el7-development': [
                {'nvr': 'goodpkg-1999-1.osg33.el7', 'latest': False},
                {'nvr': 'goodpkg-2000-1.osg33.el7', 'latest': True},
                {'nvr': 'reject-distinct-dvers-1-1.osg33.el7', 'latest': True},
                ],
            }
    tagged_packages_by_tag = {
            'osg-3.1-el5-development': [
                'goodpkg'],
            'osg-3.1-el6-development': [
                'goodpkg',
                'reject-distinct-repos'],
            'osg-3.2-el5-development': [
                'goodpkg',
                'reject-distinct-dvers'],
            'osg-3.2-el6-development': [
                'goodpkg',
                'reject-distinct-dvers',
                'reject-distinct-repos'],
            'osg-3.3-el6-development': [
                'goodpkg',
                'reject-distinct-dvers',
                'reject-distinct-repos'],
            'osg-3.3-el7-development': [
                'goodpkg',
                'reject-distinct-dvers'],
            }

    want_success = True

    def __init__(self, *args):
        self.newly_tagged_packages = []
        super(FakeKojiHelper, self).__init__(*args)

    def get_tagged_packages(self, tag):
        return self.tagged_packages_by_tag[tag]

    def get_tagged_builds(self, tag):
        return [build['nvr'] for build in self.tagged_builds_by_tag[tag]]

    def get_latest_build(self, package, tag):
        for build in self.tagged_builds_by_tag[tag]:
            if build['nvr'].startswith(package+'-') and build['latest']:
                return build['nvr']
        return None

    def koji_get_build(self, build_nvr):
        return {'id': 319}

    def tag_build(self, tag, build, force=False):
        self.newly_tagged_packages.append(build)
        task_id = len(self.newly_tagged_packages) - 1
        #sys.stdout.write("%d = tag(%s, %s)\n" % (task_id, tag, build))
        return task_id

    def watch_tasks(self, a_list):
        pass

    def get_task_state(self, task_id):
        if len(self.newly_tagged_packages) > task_id and self.want_success:
            return 'CLOSED'
        else:
            return 'FAILED'


class TestPromoter(unittest.TestCase):
    dvers = ['el5', 'el6']
    dvers_33 = ['el6', 'el7']

    def setUp(self):
        self.routes = promoter.load_routes(INIFILE)
        self.kojihelper = FakeKojiHelper(False)
        self.testing_route = self.routes['testing']
        self.testing_promoter = self._make_promoter([self.testing_route])
        self.multi_routes = [self.routes['3.1-testing'], self.routes['3.2-testing']]

    def _make_promoter(self, routes, dvers=None):
        dvers = dvers or TestPromoter.dvers
        pairs = [(route, set(dvers)) for route in routes]
        return promoter.Promoter(self.kojihelper, pairs)

    def test_add_promotion(self):
        self.testing_promoter.add_promotion('goodpkg')
        for dver in self.dvers:
            self.assertTrue(
                'goodpkg-2000-1.osg32.%s' % dver in
                [x.nvr for x in self.testing_promoter.tag_pkg_args[self.testing_route.to_tag_hint % dver]])

    def test_add_promotion_with_nvr(self):
        self.testing_promoter.add_promotion('goodpkg-2000-1.osg32.el5')
        for dver in self.dvers:
            self.assertTrue(
                'goodpkg-2000-1.osg32.%s' % dver in
                [x.nvr for x in self.testing_promoter.tag_pkg_args[self.testing_route.to_tag_hint % dver]])

    def test_add_promotion_with_nvr_no_dist(self):
        self.testing_promoter.add_promotion('goodpkg-2000-1')
        for dver in self.dvers:
            self.assertTrue(
                'goodpkg-2000-1.osg32.%s' % dver in
                [x.nvr for x in self.testing_promoter.tag_pkg_args[self.testing_route.to_tag_hint % dver]])

    def test_reject_add(self):
        self.testing_promoter.add_promotion('goodpkg')
        self.testing_promoter.add_promotion('reject-distinct-dvers')
        self.assertFalse(
            'reject-distinct-dvers-1-1.osg32.el5' in
            [x.nvr for x in self.testing_promoter.tag_pkg_args[self.testing_route.to_tag_hint % 'el5']])

    def test_reject_add_with_ignore(self):
        self.testing_promoter.add_promotion('goodpkg')
        self.testing_promoter.add_promotion('reject-distinct-dvers', ignore_rejects=True)
        self.assertTrue(
            'reject-distinct-dvers-1-1.osg32.el5' in
            [x.nvr for x in self.testing_promoter.tag_pkg_args[self.testing_route.to_tag_hint % 'el5']])
        self.assertTrue(
            'reject-distinct-dvers-2-1.osg32.el6' in
            [x.nvr for x in self.testing_promoter.tag_pkg_args[self.testing_route.to_tag_hint % 'el6']])

    def test_new_reject(self):
        self.testing_promoter.add_promotion('reject-distinct-dvers')
        rejs = self.testing_promoter.rejects
        self.assertEqual(1, len(rejs))
        self.assertEqual('reject-distinct-dvers', rejs[0].pkg_or_build)
        self.assertEqual(promoter.Reject.REASON_DISTINCT_ACROSS_DISTS, rejs[0].reason)

    def test_multi_promote(self):
        prom = self._make_promoter(self.multi_routes)
        prom.add_promotion('goodpkg-2000-1')
        for dver in ['el5', 'el6']:
            for osgver in ['3.1', '3.2']:
                tag = 'osg-%s-%s-testing' % (osgver, dver)
                dist = 'osg%s.%s' % (osgver.replace(".", ""), dver)
                pkg = 'goodpkg-2000-1.%s' % dist

                self.assertTrue(tag in prom.tag_pkg_args)
                self.assertTrue(pkg in [x.nvr for x in prom.tag_pkg_args[tag]])

    def test_cross_dist_reject(self):
        prom = self._make_promoter(self.multi_routes, ['el6'])
        prom.add_promotion('reject-distinct-repos')
        rejs = prom.rejects
        self.assertEqual(1, len(rejs))

    def test_do_promotions(self):
        self.testing_promoter.add_promotion('goodpkg')
        promoted_builds = self.testing_promoter.do_promotions()
        self.assertEqual(2, len(self.kojihelper.newly_tagged_packages))
        for dver in ['el5', 'el6']:
            tag = 'osg-3.2-%s-testing' % dver
            dist = 'osg32.%s' % dver
            nvr = 'goodpkg-2000-1.%s' % dist
            self.assertTrue(tag in promoted_builds)
            self.assertTrue(nvr in [x.nvr for x in promoted_builds[tag]])
            self.assertEqual(1, len(promoted_builds[tag]))
        self.assertEqual(2, len(promoted_builds))

    def test_do_multi_promotions(self):
        prom = self._make_promoter(self.multi_routes)
        prom.add_promotion('goodpkg-2000-1')
        promoted_builds = prom.do_promotions()
        self.assertEqual(4, len(self.kojihelper.newly_tagged_packages))
        for osgver in ['3.1', '3.2']:
            for dver in ['el5', 'el6']:
                tag = 'osg-%s-%s-testing' % (osgver, dver)
                dist = 'osg%s.%s' % (osgver.replace(".", ""), dver)
                nvr = 'goodpkg-2000-1.%s' % dist
                self.assertTrue(tag in promoted_builds)
                self.assertTrue(nvr in [x.nvr for x in promoted_builds[tag]])
                self.assertEqual(1, len(promoted_builds[tag]))
        self.assertEqual(4, len(promoted_builds))

    def _test_write_jira(self, real_promotions):
        out = StringIO.StringIO()
        promoted_builds = {}
        if real_promotions:
            prom = self._make_promoter(self.multi_routes)
            prom.add_promotion('goodpkg-2000-1')
            promoted_builds = prom.do_promotions()
        expected_lines = [
            "*Promotions*",
            "Promoted goodpkg-2000-1 to osg-3.1-el*-testing, osg-3.2-el*-testing",
            "|| Tag || Build ||"]
        for osgver in ['3.1', '3.2']:
            for dver in ['el5', 'el6']:
                tag = 'osg-%s-%s-testing' % (osgver, dver)
                dist = 'osg%s.%s' % (osgver.replace(".", ""), dver)
                nvr = 'goodpkg-2000-1.%s' % dist

                build = promoter.Build.new_from_nvr(nvr)
                if not real_promotions:
                    promoted_builds[tag] = [build]
                build_uri = "%s/koji/buildinfo?buildID=%d" % (constants.HTTPS_KOJI_HUB, 319)
                expected_lines.append("| %s | [%s|%s] |" % (tag, build.nvr, build_uri))
        expected_lines.append("")
        promoter.write_jira(self.kojihelper, promoted_builds, self.multi_routes, out)
        actual_lines = out.getvalue().split("\n")
        for idx, expected_line in enumerate(expected_lines):
            self.assertEqual(expected_line, actual_lines[idx])
        self.assertEqual(len(expected_lines), len(actual_lines))

    def test_write_jira(self):
        self._test_write_jira(real_promotions=False)

    def test_all(self):
        self._test_write_jira(real_promotions=True)

if __name__ == '__main__':
    unittest.main()