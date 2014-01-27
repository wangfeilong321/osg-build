#!/usr/bin/env python2

import logging
import unittest

from osgbuild import promoter

log = logging.getLogger('osgpromote')
log.setLevel(logging.ERROR)

TAGS = ['el6-gt52',
        'hcc-el5',
        'hcc-el5-release',
        'hcc-el5-testing',
        'hcc-el6',
        'hcc-el6-release',
        'hcc-el6-testing',
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

    def test_split_dver(self):
        self.assertEqual(('osg-build-1.3.2-1.osg32', 'el5'), promoter.split_dver(self.buildnvr))

    def test_split_repo_dver(self):
        self.assertEqual(('osg-build-1.3.2-1', 'osg32', 'el5'), promoter.split_repo_dver(self.buildnvr))
        self.assertEqual(('foo-1-1', 'osg', ''), promoter.split_repo_dver('foo-1-1.osg'))
        self.assertEqual(('foo-1-1', '', 'el5'), promoter.split_repo_dver('foo-1-1.el5'))
        self.assertEqual(('foo-1-1', '', ''), promoter.split_repo_dver('foo-1-1'))


class TestRouteDiscovery(unittest.TestCase):
    def setUp(self):
        self.route_discovery = promoter.RouteDiscovery(TAGS)
        self.routes = self.route_discovery.get_routes()

    def test_static_route(self):
        self.assertEqual('hcc-%s-testing', self.routes['hcc'][0])
        self.assertEqual('hcc-%s-release', self.routes['hcc'][1])

        f, t = self.routes['hcc'][0:2]
        self.assertEqual('hcc-%s-testing', f)
        self.assertEqual('hcc-%s-release', t)

    def test_detected_route(self):
        self.assertEqual('osg-3.2-%s-development', self.routes['3.2-testing'][0])
        self.assertEqual('osg-3.2-%s-testing', self.routes['3.2-testing'][1])

    def test_route_alias(self):
        for idx in [0, 1]:
            self.assertEqual(self.routes['testing'][idx], self.routes['3.2-testing'][idx])

    def test_new_static_route(self):
        self.assertEqual('hcc-%s-testing', self.routes['hcc'].from_tag_hint)
        self.assertEqual('hcc-%s-release', self.routes['hcc'].to_tag_hint)
        self.assertEqual('hcc', self.routes['hcc'].repo)

    def test_new_detected_route(self):
        self.assertEqual('osg-3.2-%s-development', self.routes['3.2-testing'].from_tag_hint)
        self.assertEqual('osg-3.2-%s-testing', self.routes['3.2-testing'].to_tag_hint)
        self.assertEqual('osg32', self.routes['3.2-testing'].repo)

    def test_new_route_alias(self):
        for key in 'from_tag_hint', 'to_tag_hint', 'repo':
            self.assertEqual(getattr(self.routes['testing'], key), getattr(self.routes['3.2-testing'], key))

    def test_type(self):
        for route in self.routes.values():
            self.assertTrue(isinstance(route, promoter.Route))


class MockKojiHelper(promoter.KojiHelper):
    tagged_builds_by_tag = {
            'osg-3.1-el5-development': [
                {'nvr': 'foobar-2000-1.osg31.el5', 'latest': True},
                ],
            'osg-3.1-el6-development': [
                {'nvr': 'foobar-2000-1.osg31.el6', 'latest': True},
                {'nvr': 'otherrejectme-1-1.osg31.el6', 'latest': True},
                ],
            'osg-3.2-el5-development': [
                {'nvr': 'foobar-1999-1.osg32.el5', 'latest': False},
                {'nvr': 'foobar-2000-1.osg32.el5', 'latest': True},
                {'nvr': 'rejectme-1-1.osg32.el5', 'latest': True},
                ],
            'osg-3.2-el6-development': [
                {'nvr': 'foobar-1999-1.osg32.el6', 'latest': False},
                {'nvr': 'foobar-2000-1.osg32.el6', 'latest': True},
                {'nvr': 'rejectme-2-1.osg32.el6', 'latest': True},
                {'nvr': 'otherrejectme-2-1.osg32.el6', 'latest': True},
                ],
            }
    tagged_packages_by_tag = {
            'osg-3.2-el5-development': [
                'foobar',
                'rejectme'],
            'osg-3.2-el6-development': [
                'foobar',
                'rejectme'],
            }

    def get_tagged_packages(self, tag):
        return self.tagged_packages_by_tag[tag]

    def get_tagged_builds(self, tag):
        return [build['nvr'] for build in self.tagged_builds_by_tag[tag]]

    def get_latest_build(self, package, tag):
        for build in self.tagged_builds_by_tag[tag]:
            if build['nvr'].startswith(package+'-') and build['latest']:
                return build['nvr']
        return None


class TestPromoter(unittest.TestCase):
    dvers = ['el5', 'el6']

    def setUp(self):
        self.route_discovery = promoter.RouteDiscovery(TAGS)
        self.routes = self.route_discovery.get_routes()
        self.kojihelper = MockKojiHelper(False)

    def _makePromoter(self, route, dvers=None):
        dvers = dvers or TestPromoter.dvers
        return promoter.Promoter(self.kojihelper, route, dvers)

    def test_add_promotion(self):
        route = self.routes['testing']
        prom = self._makePromoter(route)
        prom.add_promotion('foobar')
        for dver in self.dvers:
            self.assertTrue('foobar-2000-1.osg32.%s' % dver in prom.tag_pkg_args[route.to_tag_hint % dver])

    def test_reject_add(self):
        route = self.routes['testing']
        prom = self._makePromoter(route)
        prom.add_promotion('foobar')
        prom.add_promotion('rejectme')
        self.assertFalse('rejectme-1-1.osg32.el5' in prom.tag_pkg_args[route.to_tag_hint % 'el5'])

    def test_reject_add_with_ignore(self):
        route = self.routes['testing']
        prom = self._makePromoter(route)
        prom.add_promotion('foobar')
        prom.add_promotion('rejectme', ignore_rejects=True)
        self.assertTrue('rejectme-1-1.osg32.el5' in prom.tag_pkg_args[route.to_tag_hint % 'el5'])
        self.assertTrue('rejectme-2-1.osg32.el6' in prom.tag_pkg_args[route.to_tag_hint % 'el6'])

    def test_new_reject(self):
        route = self.routes['testing']
        prom = self._makePromoter(route)
        prom.add_promotion('rejectme')
        rejs = prom.get_rejects()
        self.assertEqual(1, len(rejs))
        self.assertEqual('rejectme', rejs[0].pkg_or_build)
        self.assertEqual(promoter.Reject.REASON_DISTINCT_ACROSS_DISTS, rejs[0].reason)

    def test_multi_promote(self):
        return # XXX DISABLED
        route1 = self.routes['3.1-testing']
        route2 = self.routes['3.2-testing']
        prom = self._makePromoter([route1, route2])
        prom.add_promotion('foobar')
        self.assertTrue(('osg-3.1-el5-testing', 'foobar-2000-1.osg31.el5') in prom.tag_pkg_args)
        self.assertTrue(('osg-3.2-el6-testing', 'foobar-2000-1.osg32.el6') in prom.tag_pkg_args)

    def test_cross_dist_reject(self):
        return # XXX DISABLED
        prom = self._makePromoter([self.routes['3.1-testing'], self.routes['3.2-testing']], ['el6'])
        prom.add_promotion('otherrejectme')
        rejs = prom.get_rejects()
        self.assertEqual(2, len(rejs))


if __name__ == '__main__':
    unittest.main()


