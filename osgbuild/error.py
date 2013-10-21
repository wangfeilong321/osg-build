"""Exception classes for osg-build"""
# pylint: disable=C0103,C0111
import os
import re
import traceback

class Error(Exception):
    """Base class for expected exceptions"""
    def __init__(self, msg, tb=None):
        Exception.__init__(self)
        self.msg = msg
        if tb is None:
            self.traceback = traceback.format_exc()

    def __repr__(self):
        return repr((self.msg, self.traceback))

    def __str__(self):
        return str(self.msg)


class SVNError(Error):
    def __init__(self, msg):
        Error.__init__(self, "SVN error: %s" % msg)


class GitError(Error):
    def __init__(self, msg):
        Error.__init__(self, "git error: %s" % msg)


class GlobNotFoundError(Error):
    def __init__(self, globtext):
        msg = "Couldn't find file matching '%s'." % globtext
        Error.__init__(self, msg)


class FileNotFoundError(Error):
    def __init__(self, fname, searchpath=None):
        msg = "Couldn't find file named '%s'." % fname
        if searchpath:
            Error.__init__(self, msg + " Search path was:\n%s" %
                           os.pathsep.join(searchpath))
        else:
            Error.__init__(self, msg)


class ProgramNotFoundError(Error):
    def __init__(self, program):
        msg = "Couldn't find required program '%s'." % program
        if program.find("/") == -1:
            Error.__init__(self, msg + " $PATH was:\n%s" % os.environ['PATH'])
        else:
            Error.__init__(self, msg)


class OSGBuildError(Error):
    def __init__(self, msg, tb=None):
        Error.__init__(self, "Error in build step: " + msg, tb)


class OSGPrebuildError(Error):
    def __init__(self, msg, tb=None):
        Error.__init__(self, "Error in pre-build step: " + msg, tb)


class UsageError(Error):
    def __init__(self, msg, tb=None):
        Error.__init__(self, "Usage error: " + msg + "\n", tb)


class KojiError(Error):
    def __init__(self, msg, tb=None):
        Error.__init__(self, "Koji error: " + msg, tb)


class MockError(Error):
    def __init__(self, msg, tb=None):
        Error.__init__(self, "Mock error: " + msg, tb)

def type_of_error(err_object):
    if isinstance(err_object, Exception):
        return str(err_object.__class__.__name__)
    else:
        return "Unknown"

