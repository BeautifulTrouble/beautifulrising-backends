
import contextlib
import datetime
import fcntl
import os
import re
import sys
from functools import reduce

import magic
import unidecode


@contextlib.contextmanager
def script_directory():
    '''
    A context manager which allows you to write blocks of code which run within 
    the current script's directory. The working directory is restored afterward.
    '''
    cwd = os.getcwd()
    os.chdir(os.path.dirname(os.path.realpath(__file__)))
    try:
        yield
    finally:
        os.chdir(cwd)


@contextlib.contextmanager
def script_subdirectory(name):
    '''
    A context manager which allows you to write blocks of code which run within a 
    subdirectory of the current script's directory. The subdirectory is created if 
    it does not exist, and the working directory is restored after completion.

    >>> with script_subdirectory("html"):
    ...    output_templates()
    '''
    cwd = os.getcwd()
    subdirectory = os.path.join(os.path.dirname(os.path.realpath(__file__)), name)
    if not os.path.exists(subdirectory):
        os.makedirs(subdirectory)
        log('mkdir: {}'.format(subdirectory))
    os.chdir(subdirectory)
    try: 
        yield
    finally: 
        os.chdir(cwd)


@contextlib.contextmanager
def only_one_process(name=None):
    '''
    Use a file lock to ensure only one process runs at a time
    '''
    if not name:
        #TODO: Improve upon this for situations with a deeper stack
        name = os.path.splitext(os.path.basename(sys._getframe(2).f_globals['__file__']))[0]
    with script_directory(), open(name + '.lock', 'w') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def mimetype(filename):
    '''
    Convenience wrapper for python-magic
    '''
    return magic.from_file(filename, mime=True).decode()


def slugify(s, allow=''):
    '''
    Reproduce these steps for consistent slugs!
    '''
    s = unidecode.unidecode(s).lower().replace("'", '')
    return re.sub(r'[^\w{}]+'.format(allow), '-', s)


def strip_smartquotes(s):
    '''
    For code mangled by a word processor
    '''
    pairs = '\u201c"', '\u201d"', "\u2018'", "\u2019'"
    replace = lambda s,r: s.replace(*r)
    return reduce(replace, pairs, s)


def log(*s, fatal=False, **kw):
    '''
    Simple "tee-style" logging with timestamps
    '''
    s = ' '.join(str(i) for i in s)
    script_dir = os.path.dirname(os.path.realpath(__file__))
    with open(os.path.join(script_dir, 'log.txt'), 'a', encoding="utf-8") as file:
        file.write('{} {}\n'.format(datetime.datetime.utcnow().isoformat(), s))
        print(s, **kw)
    if fatal:
        print('Quitting.')
        sys.exit(int(fatal))


def die(*s):
    '''
    Perl-y
    '''
    log(*s, fatal=True)


__all__ = [
    'script_directory', 
    'script_subdirectory', 
    'only_one_process',
    'mimetype',
    'slugify', 
    'strip_smartquotes',
    'log', 
    'die', 
]
