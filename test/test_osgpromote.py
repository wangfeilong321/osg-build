#!/usr/bin/env python2

import unittest

from osg_build_tools import promoter



class TestSimple(unittest.TestCase):
    buildnvr = "osg-build-1.3.2-1.osg32.el5"
    def test_split_nvr(self):
        self.assertEqual(promoter.split_nvr(self.buildnvr), ['osg-build', '1.3.2', '1.osg32.el5'])

    def test_split_dver(self):
        self.assertEqual(promoter.split_dver(self.buildnvr), ('osg-build-1.3.2-1.osg32', 'el5'))

    def test_split_dist(self):
        self.assertEqual(promoter.split_dist(self.buildnvr), ('osg-build-1.3.2-1', 'osg32.el5'))
        self.assertEqual(promoter.split_dist('foo-1-1.osg'), ('foo-1-1', 'osg'))
        self.assertEqual(promoter.split_dist('foo-1-1'), ('foo-1-1', ''))


if __name__ == '__main__':
    unittest.main()


