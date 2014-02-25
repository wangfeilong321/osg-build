"""utilities for osg-build"""
import itertools
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime


class CalledProcessError(Exception):
    """Returned by checked_call and checked_backtick if the subprocess exits
    nonzero.

    """
    def __init__(self, process, returncode, output=None):
        super(CalledProcessError, self).__init__()
        self.process = process
        self.returncode = returncode
        self.output = output

    def __str__(self):
        logging.debug(self.output)
        return ("Error in called process(%s): subprocess returned %s.\nOutput: %s" %
                (str(self.process), str(self.returncode), str(self.output)))

    def __repr__(self):
        return str(repr(self.process),
                   repr(self.returncode),
                   repr(self.output))


def checked_call(*args, **kwargs):
    """A wrapper around subprocess.call() that raises CalledProcessError on
    a nonzero return code. Similar to subprocess.check_call() in 2.7+, but
    prints the command to run and the result if loglevel is DEBUG.

    """
    err = unchecked_call(*args, **kwargs)
    if err:
        raise CalledProcessError([args, kwargs], err, None)


def unchecked_call(*args, **kwargs):
    """A wrapper around subprocess.call() with the same semantics as checked_call: 
    Prints the command to run and the result if loglevel is DEBUG.

    """
    if type(args[0]) == type(''):
        cmd = args[0]
    elif type(args[0]) == type([]) or type(args[0]) == type(()):
        cmd = "'" + "' '".join(args[0]) + "'"
    logging.debug("Running " + cmd)

    err = subprocess.call(*args, **kwargs)
    logging.debug("Subprocess returned " + str(err))
    return err


def backtick(*args, **kwargs):
    """Call a process and return its output, ignoring return code.
    See checked_backtick() for semantics.

    """
    try:
        output = checked_backtick(*args, **kwargs)
    except CalledProcessError, e:
        output = e.output

    return output


def sbacktick(*args, **kwargs):
    """Call a process and return a pair containing its output and exit status.
    See checked_backtick() for semantics.

    """
    returncode = 0
    try:
        output = checked_backtick(*args, **kwargs)
    except CalledProcessError, e:
        output = e.output
        returncode = e.returncode

    return (output, returncode)


def checked_backtick(*args, **kwargs):
    """Call a process and return a string containing its output.
    This is a wrapper around subprocess.Popen() and passes through arguments
    to subprocess.Popen().

    Raises CalledProcessError if the process has a nonzero exit code. The
    'output' field of the CalledProcessError contains the output in that case.

    If the command is a string and 'shell' isn't passed, it's split up
    according to shell quoting rules using shlex.split()

    The output is stripped unless nostrip=True is specified.
    If err2out=True is specified, stderr will be included in the output.

    If clocale=True is specified, LC_ALL=C will be added to the subprocess's
    environment, forcing the 'C' locale for program output.

    """
    cmd = args[0]
    if type(cmd) == type('') and 'shell' not in kwargs:
        cmd = shlex.split(cmd)

    sp_kwargs = kwargs.copy()

    nostrip = sp_kwargs.pop('nostrip', False)
    sp_kwargs['stdout'] = subprocess.PIPE
    if sp_kwargs.pop('err2out', False):
        sp_kwargs['stderr'] = subprocess.STDOUT
    if sp_kwargs.pop('clocale', False):
        sp_kwargs['env'] = dict(sp_kwargs.pop('env', os.environ), LC_ALL='C')

    proc = subprocess.Popen(cmd, *args[1:], **sp_kwargs)

    output = proc.communicate()[0]
    if not nostrip:
        output = output.strip()
    err = proc.returncode

    if err:
        raise CalledProcessError([args, kwargs], err, output)
    else:
        return output


def slurp(filename):
    """Return the contents of a file as a single string."""
    fh = open(filename, 'r')
    try:
        contents = fh.read()
    finally:
        fh.close()
    return contents


def unslurp(filename, contents):
    """Write a string to a file."""
    fh = open(filename, 'w')
    try:
        fh.write(contents)
    finally:
        fh.close()

def atomic_unslurp(filename, contents, mode=0644):
    """Write contents to a file, making sure a half-written file is never
    left behind in case of error.

    """
    fd, tempname = tempfile.mkstemp(dir=os.path.dirname(filename))
    try:
        try:
            os.write(fd, contents)
        finally:
            os.close(fd)
    except:
        os.unlink(tempname)
        raise
    os.rename(tempname, filename)
    os.chmod(filename, mode)


def find_file(filename, paths=None):
    """Go through each directory in paths and look for filename in it. Return
    the first match.

    """
    matches = find_files(filename, paths)
    if matches:
        return matches[0]
    else:
        return None


def find_files(filename, paths=None):
    """Go through each directory in paths and look for filename in it. Return
    all matches.

    """
    matches = []
    if paths is None:
        paths = sys.path
    for p in paths:
        j = os.path.join(p, filename)
        if os.path.isfile(j):
            matches += [j]
    return matches


def super_unpack(*compressed_files):
    '''Extracts compressed files, calling the appropriate expansion
    program based on the file extension.'''

    handlers = [
        ('.tar.bz2',  'tar xjf %s'),
        ('.tar.gz',   'tar xzf %s'),
        ('.bz2',      'bunzip2 %s'),
        ('.rar',      'unrar x %s'),
        ('.gz',       'gunzip %s'),
        ('.tar',      'tar xf %s'),
        ('.tbz2',     'tar xjf %s'),
        ('.tgz',      'tar xzf %s'),
        ('.zip',      'unzip %s'),
        ('.Z',        'uncompress %s'),
        ('.7z',       '7z x %s'),
        ('.tar.xz',   'xz -d %s -c | tar xf -'),
        ('.xz',       'xz -d %s'),
        ('.rpm',      'rpm2cpio %s | cpio -id'),
    ]
    for cf in compressed_files:
        for (ext, cmd) in handlers:
            if cf.endswith(ext):
                subprocess.call(cmd % re.escape(cf), shell=True)
                break


def safe_makedirs(directory, mode=0777):
    """Create a directory and all its parent directories, unless it already
    exists.

    """
    if not os.path.isdir(directory):
        os.makedirs(directory, mode)


def ask(question, choices):
    """Prompt user for a choice from a list. Return the choice."""
    choices_lc = [x.lower() for x in choices]
    user_choice = ""
    match = False
    while not match:
        print question
        user_choice = raw_input("[" + "/".join(choices) + "] ? ").strip().lower()
        for choice in choices_lc:
            if user_choice.startswith(choice):
                match = True
                break
    return user_choice


def ask_yn(question):
    """Prompt user for a yes/no question. Return True or False for yes or no"""
    user_choice = ask(question, ("y", "n"))
    if user_choice.startswith("y"):
        return True
    else:
        return False


def safe_make_backup(filename, move=True):
    """Back up a file if it exists (either copy or move)"""
    if os.path.exists(filename):
        newname = filename + datetime.now().strftime(".%y%m%d%H%M%S~")
        if move:
            shutil.move(filename, newname)
        else:
            shutil.copy(filename, newname)


# original from rsvprobe.py by Marco Mambelli
def which(program):
    """Python replacement for which"""
    def is_exe(fpath):
        "is a regular file and is executable"
        return os.path.isfile(fpath) and os.access(fpath, os.X_OK)
    fpath, _ = os.path.split(program)
    if fpath:
        if is_exe(program):
            return program
    else:
        for path in os.environ["PATH"].split(os.pathsep):
            exe_file = os.path.join(path, program)
            if is_exe(exe_file):
                return exe_file
    return None



def printf(fstring, *args, **kwargs):
    """A shorthand for printing with a format string.
    The kwargs 'file' and 'end' are as in the Python3 print function.
    """
    file_ = kwargs.pop('file', sys.stdout)
    end = kwargs.pop('end', "\n")
    ffstring = fstring + end
    if len(args) == 0 and len(kwargs) > 0:
        file_.write(ffstring % kwargs)
    elif len(args) == 1 and type(args[0]) == dict:
        file_.write(ffstring % args[0])
    else:
        file_.write(ffstring % args)

def errprintf(fstring, *args, **kwargs):
    """printf to stderr"""
    kwargs.pop('file', None)
    printf(fstring, file=sys.stderr, *args, **kwargs)

class safelist(list):
    """A version of the list type that has get and pop methods that accept
    default arguments instead of raising errors. (Compare dict.get and dict.pop)
    """
    def get(self, idx, default=None):
        """L.get(idx, default=None) -> item
        Get item at idx. If idx is out of range, return default."""
        try:
            return self.__getitem__(idx)
        except IndexError:
            return default

    def pop(self, *args):
        """L.pop([idx[,default]]) -> item, remove specified index
        (default last). If idx is out of range, then if return default if
        specified, raise IndexError if not.
        """
        try:
            return list.pop(self, args[0])
        except IndexError:
            if len(args) < 2:
                raise
            else:
                return args[1]


def get_screen_columns():
    """Return the number of columns in the screen"""
    screen_columns = os.environ.get('COLUMNS')
    if not screen_columns:
        try:
            screen_columns = int(backtick("stty size").split()[1])
        except TypeError:
            screen_columns = 80
    return screen_columns

try:
    from itertools import izip_longest # pylint: disable=E0611
except ImportError:
    class ZipExhausted(Exception):
        pass

    def izip_longest(*args, **kwds):
        """izip_longest from the python 2.6 documentation
        (since it's not in 2.4)
        """
        # izip_longest('ABCD', 'xy', fillvalue='-') --> Ax By C- D-
        fillvalue = kwds.get('fillvalue')
        counter = [len(args) - 1]
        def sentinel():
            if not counter[0]:
                raise ZipExhausted
            counter[0] -= 1
            yield fillvalue
        fillers = itertools.repeat(fillvalue)
        iterators = [itertools.chain(it, sentinel(), fillers) for it in args]
        try:
            while iterators:
                yield tuple(map(lambda x:x.next(), iterators)) # disable deprecated-lambda check: pylint:disable=W0110
                # ^ the 'next' builtin is not in 2.4
        except ZipExhausted:
            pass


def print_table(columns_by_header):
    """Print a dict of lists in a table, with each list being a column"""
    screen_columns = get_screen_columns()
    field_width = int(screen_columns / len(columns_by_header))
    columns = []
    for entry in sorted(columns_by_header):
        columns.append([entry, '---'] + sorted(columns_by_header[entry]))
    for columns_in_row in izip_longest(fillvalue='', *columns):
        for col in columns_in_row:
            printf("%-*s", field_width, col, end='')
        printf("")


