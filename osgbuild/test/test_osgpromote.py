#!/usr/bin/env python2

import unittest

from osgbuild import promoter


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
        self.assertEqual(promoter.split_nvr(self.buildnvr), ('osg-build', '1.3.2', '1.osg32.el5'))

    def test_split_dver(self):
        self.assertEqual(promoter.split_dver(self.buildnvr), ('osg-build-1.3.2-1.osg32', 'el5'))

    def test_split_repo_dver(self):
        self.assertEqual(promoter.split_repo_dver(self.buildnvr), ('osg-build-1.3.2-1', 'osg32', 'el5'))
        self.assertEqual(promoter.split_repo_dver('foo-1-1.osg'), ('foo-1-1', 'osg', ''))
        self.assertEqual(promoter.split_repo_dver('foo-1-1.el5'), ('foo-1-1', '', 'el5'))
        self.assertEqual(promoter.split_repo_dver('foo-1-1'), ('foo-1-1', '', ''))


class TestRouteDiscovery(unittest.TestCase):
    def setUp(self):
        self.route_discovery = promoter.RouteDiscovery(TAGS)
        self.routes = self.route_discovery.get_routes()

    def test_static_route(self):
        self.assertEqual(self.routes['hcc'][0], 'hcc-%s-testing')
        self.assertEqual(self.routes['hcc'][1], 'hcc-%s-release')

        f, t = self.routes['hcc'][0:2]
        self.assertEqual(f, 'hcc-%s-testing')
        self.assertEqual(t, 'hcc-%s-release')

    def test_detected_route(self):
        self.assertEqual(self.routes['3.2-testing'][0], 'osg-3.2-%s-development')
        self.assertEqual(self.routes['3.2-testing'][1], 'osg-3.2-%s-testing')

    def test_route_alias(self):
        for idx in [0, 1]:
            self.assertEqual(self.routes['testing'][idx], self.routes['3.2-testing'][idx])

    def test_new_static_route(self):
        self.assertEqual(self.routes['hcc'].from_tag_hint, 'hcc-%s-testing')
        self.assertEqual(self.routes['hcc'].to_tag_hint, 'hcc-%s-release')
        self.assertEqual(self.routes['hcc'].repo, 'hcc')

    def test_new_detected_route(self):
        self.assertEqual(self.routes['3.2-testing'].from_tag_hint, 'osg-3.2-%s-development')
        self.assertEqual(self.routes['3.2-testing'].to_tag_hint, 'osg-3.2-%s-testing')
        self.assertEqual(self.routes['3.2-testing'].repo, 'osg32')

    def test_new_route_alias(self):
        for key in 'from_tag_hint', 'to_tag_hint', 'repo':
            self.assertEqual(getattr(self.routes['testing'], key), getattr(self.routes['3.2-testing'], key))

    def test_type(self):
        for route in self.routes.values():
            self.assertTrue(isinstance(route, promoter.Route))


class MockKojiHelper(promoter.KojiHelper):
    tagged_builds_by_tag = {
            'osg-3.2-el5-development': [
                {'nvr': 'foobar-1999-1.osg32.el5', 'latest': False},
                {'nvr': 'foobar-2000-1.osg32.el5', 'latest': True},
                {'nvr': 'reject-1-1.osg32.el5', 'latest': True},
                ],
            'osg-3.2-el6-development': [
                {'nvr': 'foobar-1999-1.osg32.el6', 'latest': False},
                {'nvr': 'foobar-2000-1.osg32.el6', 'latest': True},
                {'nvr': 'reject-2-1.osg32.el6', 'latest': True},
                ],
            }
    tagged_packages_by_tag = {
            'osg-3.2-el5-development': [
                'foobar',
                'reject'],
            'osg-3.2-el6-development': [
                'foobar',
                'reject'],
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
        prom.add_promotion('reject')
        self.assertFalse('reject-1-1.osg32.el5' in prom.tag_pkg_args[route.to_tag_hint % 'el5'])

    def test_reject_add_with_ignore(self):
        route = self.routes['testing']
        prom = self._makePromoter(route)
        prom.add_promotion('foobar')
        prom.add_promotion('reject', ignore_rejects=True)
        self.assertTrue('reject-1-1.osg32.el5' in prom.tag_pkg_args[route.to_tag_hint % 'el5'])
        self.assertTrue('reject-2-1.osg32.el6' in prom.tag_pkg_args[route.to_tag_hint % 'el6'])

    def test_new_reject(self):
        route = self.routes['testing']
        prom = self._makePromoter(route)
        prom.add_promotion('reject')
        rejs = prom.get_rejects()
        self.assertEqual(len(rejs), 1)
        self.assertTrue(rejs[0].pkg_or_build == 'reject')
        self.assertTrue(rejs[0].reason == promoter.Rejects.REASON_DISTINCT_ACROSS_DVERS)





if __name__ == '__main__':
    unittest.main()


