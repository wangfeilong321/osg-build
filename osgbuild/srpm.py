"""Tasks for an SRPM build."""

# pylint: disable=W0614
import glob
import fnmatch
import logging
import os
import re
import shutil

# local
from osgbuild.constants import *
from osgbuild import fetch_sources
from osgbuild import utils
from osgbuild.error import *


log = logging.getLogger('osgbuild')
log.propagate = False


class SRPMBuild(object):
    """Tasks for an SPRM build and helper functions."""

    def __init__(self, package_dir, buildopts, mock_obj, koji_obj):
        self.package_dir = package_dir
        self.mock_obj = mock_obj
        self.koji_obj = koji_obj

        self.buildopts = buildopts

        self.abs_package_dir = os.path.abspath(self.package_dir)
        self.package_name = os.path.basename(self.abs_package_dir)
        if not re.match(r"\w+", self.package_name): # sanity check
            raise Error("Package directory '%s' gives invalid package name '%s'" %
                        (self.abs_package_dir, self.package_name))
        # Unless working_directory is '.', i.e. we want to put the wd's in
        # the package dir, get rid of any parent or current directory
        # components so doing "osg-build pre -w TEMP ../foobar" won't put stuff
        # in "/tmp/foobar"
        if (os.path.realpath(self.buildopts['working_directory']) !=
                os.path.realpath('.')):
            package_dir_no_parent = re.sub(r'^(\.\.?/)+', '', package_dir)
            self.working_subdir = (
                os.path.abspath(
                    os.path.join(
                        self.buildopts['working_directory'],
                        package_dir_no_parent)))
        else:
            self.working_subdir = (
                os.path.abspath(
                    os.path.join(
                        self.buildopts['working_directory'],
                        package_dir)))
        self.results_dir = os.path.join(self.working_subdir, WD_RESULTS)
        self.prebuild_dir = os.path.join(self.working_subdir, WD_PREBUILD)
        self.unpacked_dir = os.path.join(self.working_subdir, WD_UNPACKED)
        self.unpacked_tarball_dir = os.path.join(self.working_subdir, WD_UNPACKED_TARBALL)
        self.quilt_dir = os.path.join(self.working_subdir, WD_QUILT)
    # end of __init__()


    def maybe_autoclean(self):
        """Delete underscore directories if the autoclean option is set."""
        if self.buildopts['autoclean']:
            for udir in [self.results_dir,
                         self.prebuild_dir,
                         self.unpacked_dir,
                         self.unpacked_tarball_dir]:
                if os.path.exists(udir):
                    log.debug("autoclean removing " + udir)
                    shutil.rmtree(udir)


    def get_rpmbuild_defines(self, prebuild):
        """Get a list of --define arguments to pass to rpmbuild based on the
        working dir and the subdirectories specified in the WD_* constants.

        """
        rhel = self.buildopts.get('redhat_release', '5')
        defines = [
            "_build_name_fmt %%{NAME}-%%{VERSION}-%%{RELEASE}.%%{ARCH}.rpm",
            "rhel " + rhel,
            "dist ." + self.buildopts.get('distro_tag', 'osg.el' + rhel),
            "osg 1",
        ]
        for dver in DVERS:
            if dver == rhel:
                defines.append("el%s 1" % dver)
            else:
                defines.append("el%s 0" % dver)
        if rhel == '5':
            defines += [
                "_source_filedigest_algorithm 1",
                "_binary_filedigest_algorithm 1",
                "_binary_payload w9.gzdio",
                "_source_payload w9.gzdio",
                "_default_patch_fuzz 2",
            ]

        if prebuild:
            defines += [
                "_topdir " + self.prebuild_dir,
                "_builddir " + "%{_topdir}",
                "_buildrootdir " + "%{_topdir}",
                "_rpmdir " + "%{_topdir}",
                "_sourcedir " + "%{_topdir}",
                "_specdir " + "%{_topdir}",
                "_srcrpmdir " + "%{_topdir}",
                "_tmppath " + "%{_topdir}"
            ]
        else:
            defines += [
                "_topdir " + self.results_dir,
                "_builddir " + "%{_topdir}/BUILD",
                "_buildrootdir " + "%{_topdir}/BUILDROOT",
                "_rpmdir " + "%{_topdir}",
                "_sourcedir " + "%{_topdir}",
                "_specdir " + "%{_topdir}",
                "_srcrpmdir " + "%{_topdir}",
                "_tmppath " + "%{_topdir}/tmp"
            ]

        return ['--define=' + d for d in defines]
    # end of get_rpmbuild_defines()


    def make_srpm(self, spec_fn):
        """Make an SRPM from a spec file. Raise OSGPrebuildError on failure"""
        cmd = (["rpmbuild", "-bs", "--nodeps"] +
               self.get_rpmbuild_defines(prebuild=True) +
               [spec_fn])
        err_msg_prefix = ("Error making SRPM from %s\n"
                          "Command used was: %s\n" %
                          (spec_fn, " ".join(cmd)))
        out, err = utils.sbacktick(cmd, nostrip=True, clocale=True, err2out=True)
        if err:
            log.error("Rpmbuild failed. Output follows: " + out)
            raise OSGPrebuildError(err_msg_prefix +
                                   "Rpmbuild return code %d" % err)

        match = re.search(r"(?ms)^Wrote: ([^\n]+.src.rpm)$", out)
        if match:
            srpm = match.group(1).strip()
            if os.path.isfile(srpm):
                log.debug("Created SRPM: %s", srpm)
                return srpm
        raise OSGPrebuildError(err_msg_prefix +
                               "Unable to find resulting SRPM.")


    def prebuild_external_sources(self, destdir=None):
        """Collect sources and spec file in prebuild_dir. Return the path to
        the spec file.

        """
        if destdir is None: destdir = self.prebuild_dir
        return fetch_sources.fetch(
            package_dir=self.package_dir,
            destdir=destdir,
            cache_prefix=self.buildopts['cache_prefix'],
            unpacked_dir=self.unpacked_dir,
            want_full_extract=self.buildopts.get('full_extract'),
            unpacked_tarball_dir=self.unpacked_tarball_dir)

    def prebuild(self):
        """prebuild task.
        Create an SRPM containing upstream sources (if any) plus our changes
        (if any) plus a spec file.

        Return the name of the SRPM created.

        """
        utils.safe_makedirs(self.prebuild_dir)
        spec_filename = self.prebuild_external_sources()

        result_srpm = self.make_srpm(spec_filename)

        if result_srpm:
            log.info("Files have been prepared in %s.", self.prebuild_dir)
            return os.path.abspath(result_srpm)


    def quilt(self):
        """quilt task. Prebuild the package (except for making the SRPM)
        and run 'quilt setup' on the spec file.

        """
        if not utils.which("quilt"):
            raise ProgramNotFoundError("quilt")

        if self.buildopts['autoclean']:
            if os.path.exists(self.quilt_dir):
                log.debug("autoclean removing " + self.quilt_dir)
                shutil.rmtree(self.quilt_dir)

        utils.safe_makedirs(self.quilt_dir)
        spec_filename = self.prebuild_external_sources(destdir=self.quilt_dir)

        os.chdir(self.quilt_dir)
        ret = utils.unchecked_call(["quilt", "-v", "setup", spec_filename])
        if ret != 0:
            raise Error("Error running 'quilt setup' on the spec file.")

        log.info("quilt files ready in %s", self.quilt_dir)


    def prepare(self):
        """prepare task. Prebuild the package and run rpmbuild -bp on it."""
        srpm = self.prebuild()
        utils.safe_makedirs(self.results_dir)
        shutil.copy(srpm, self.results_dir)
        for dname in ['BUILD', 'tmp']:
            utils.safe_makedirs(os.path.join(self.results_dir, dname))
        rpm_cmd = (["rpm"] +
                   self.get_rpmbuild_defines(prebuild=False) +
                   ["-i", srpm])
        ret = utils.unchecked_call(rpm_cmd)
        if ret != 0:
            raise Error("Unable to unpack SRPM: rpm -i returned %d" % ret)

        rpmbuild_cmd = (["rpmbuild", "-bp", "--nodeps"] +
                        self.get_rpmbuild_defines(prebuild=False) +
                        glob.glob(os.path.join(self.results_dir, "*.spec")))
        if self.buildopts['target_arch'] is not None:
            rpmbuild_cmd += ["--target", self.buildopts['target_arch']]
        ret = utils.unchecked_call(rpmbuild_cmd)
        if ret != 0:
            raise Error(
                "Unable to prepare the package: rpmbuild -bp returned %d" % ret)
        log.info("Files prepared in: " +
                     os.path.join(self.results_dir, "BUILD"))
    # end of prepare()


    def rpmbuild(self):
        """rpmbuild task.
        Build the package using rpmbuild on the local machine.

        """
        srpm = self.prebuild()
        utils.safe_makedirs(self.results_dir)
        shutil.copy(srpm, self.results_dir)
        for d in ['BUILD', 'tmp']:
            utils.safe_makedirs(os.path.join(self.results_dir, d))
        cmd = (["rpmbuild"] +
               self.get_rpmbuild_defines(prebuild=False) +
               ["--rebuild", srpm])
        if self.buildopts['target_arch'] is not None:
            cmd += ["--target", self.buildopts['target_arch']]
        err = utils.unchecked_call(cmd)

        # TODO Parse rpmbuild output instead of using glob
        if err:
            raise OSGBuildError('Making RPM failed (command was: ' +
                                " ".join(cmd) +')')
        else:
            rpms = [x for x in glob.glob(os.path.join(self.results_dir, "*.rpm"))
                    if not fnmatch.fnmatch(x, '*.src.rpm')]
            if not rpms:
                raise OSGBuildError("No RPMs found. Making RPMs failed?")
            log.info("The following RPM(s) have been created:\n" +
                         "\n".join(rpms))


    def mock(self):
        """mock task. Build the package using mock on the local machine."""
        srpm = self.prebuild()
        utils.safe_makedirs(self.results_dir)

        rpms = self.mock_obj.rebuild(self.results_dir, srpm)
        if self.buildopts['mock_clean']:
            self.mock_obj.clean()
        log.info("The following RPM(s) have been created:\n" +
                     "\n".join(rpms))


    def koji(self):
        """koji task. Submit a build to koji; add the package first if
        necessary.

        """
        if not self.buildopts['scratch']:
            self.koji_obj.add_pkg(self.package_name)
        utils.safe_makedirs(self.results_dir)
        srpm = self.prebuild()
        task_id = self.koji_obj.build_srpm(srpm)
        return task_id


    def lint(self):
        """lint task. Prebuild the package and run rpmlint on the SRPM."""
        if not utils.which("rpmlint"):
            raise ProgramNotFoundError("rpmlint")
        conf_file = utils.find_file("rpmlint.cfg", DATA_FILE_SEARCH_PATH)
        if not conf_file:
            raise FileNotFoundError("rpmlint.cfg", DATA_FILE_SEARCH_PATH)
        srpm = self.prebuild()
        lint_output, lint_returncode = utils.sbacktick(
            ["rpmlint", "-f", conf_file, srpm])

        print lint_output
        if lint_returncode == 0:
            print "rpmlint ok for " + self.package_name
        elif lint_returncode < 64:
            print "Error running rpmlint for " + self.package_name
        elif lint_returncode == 64:
            print "rpmlint found problems with " + self.package_name
        elif lint_returncode == 66:
            print "rpmlint found many problems with " + self.package_name
        else:
            print "unrecognized return code from rpmlint: " + str(lint_returncode)
# end of SRPMBuild


