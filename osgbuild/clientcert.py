import re
import os
import time

from datetime import datetime
from osgbuild.error import ClientCertError
from osgbuild import utils

class ClientCert(object):
    __slots__ = ['filename', 'first_commonname', 'startdate', 'enddate']

    def __init__(self, filename):
        self.filename = str(filename)

        if not os.path.exists(self.filename):
            raise ClientCertError(self.filename, "file not found")

        self.startdate = None
        self.enddate = None
        self.first_commonname = None
        self.do_openssl_lookup()
        self.assert_not_expired()

    def do_openssl_lookup(self):
        cmd = ["openssl", "x509",
                    "-in", self.filename,
                    "-noout",
                    "-subject", "-nameopt", "multiline",
                    "-dates"]
        output = utils.checked_backtick(cmd)

        self.startdate, self.enddate = self.extract_dates(output)
        self.first_commonname = self.extract_first_commonname(output)

    @staticmethod
    def _parse_date(datestr):
        # Want to match something like "Nov 15 09:49:34 2013 GMT"
        # datetime.strptime is not in Python 2.4, but the 2.6 manual has this
        # equivalent:
        return datetime(*(time.strptime(datestr, "%b %d %H:%M:%S %Y %Z")[0:6]))

    def extract_dates(self, output):
        startdate_match = re.search(r"""(?xms)
                ^ notBefore=([^\n]+) $""", output)
        enddate_match = re.search(r"""(?xms)
                ^ notAfter=([^\n]+) $""", output)
        try:
            startdate = self._parse_date(startdate_match.group(1))
            enddate = self._parse_date(enddate_match.group(1))
        except (ValueError, AttributeError), err:
            raise ClientCertError(
                self.filename,
                "cannot determine valid dates from openssl output"
                    "\n%s\n"
                    "Error was: %s" % (output, str(err)))

        return startdate, enddate

    def extract_first_commonname(self, output):
        # Get the first commonName using the negative lookbehind assertion to make
        # sure we're capturing the right commonName if there are multiple.
        # Note that the commonName is indented since we're using multiline
        # output format.
        cn_match = re.search(r"""(?xms)
            (?!<commonName)
            ^ \s* commonName \s* = \s* ([^\n]+) $""", output)
        if not cn_match:
            raise ClientCertError(
                self.filename,
                "cannot determine commonName from openssl output\n%s\n" % output)
        return cn_match.group(1)

    def assert_not_expired(self):
        if datetime.utcnow() > self.enddate:
            raise ClientCertError(self.filename, "cert expired")
        elif datetime.utcnow() < self.startdate:
            raise ClientCertError(self.filename, "cert not valid yet")


