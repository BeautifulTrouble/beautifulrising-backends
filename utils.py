
import contextlib
import datetime
import fcntl
import json
import os
import re
import sys
from functools import reduce
from subprocess import Popen

import archieml
import magic
import unidecode


DEBUG = '--debug' in sys.argv
DEVELOP = '--develop' in sys.argv


# Regex word boundaries don't work with Arabic characters, so any regex which
# leverages the \b will fail to work. The string ARABIC_BOUNDARY_REGEX can be
# interpolated within a regex as a (fingers crossed) drop-in replacement for
# a \b. Credit for the idea of using positive lookbehind assertions goes to
# http://www.rexegg.com/regex-boundaries.html#real-word-boundary
ARABIC_RANGES = r'\u0600-\u06ff\u0750-\u077f\ufb50-\ufbc1\ufbd3-\ufd3f'\
                r'\ufd49-\ufd8f\ufd92-\ufdc7\ufe70-\ufefc\ufdf0-\ufdfd'
ARABIC_BOUNDARY_REGEX = r'(?:(?<=[^\w{0}])(?=[\w{0}])|(?<=[\w{0}])(?=[^\w{0}]))'.format(ARABIC_RANGES)


class JSONEncoder(json.JSONEncoder):
    '''
    JSON encoder which invokes magic _json method on custom classes
    '''
    def default(self, obj):
        try:
            return obj._json()
        except AttributeError:
            return json.JSONEncoder.default(self, obj)


@contextlib.contextmanager
def script_directory():
    '''
    A context manager which allows you to write blocks of code which run within 
    this script's directory. The working directory is restored afterward.
    '''
    cwd = os.getcwd()
    directory = os.path.dirname(os.path.realpath(__file__))
    os.chdir(directory)
    try:
        yield directory
    finally:
        os.chdir(cwd)


@contextlib.contextmanager
def script_subdirectory(name):
    '''
    A context manager which allows you to write blocks of code which run within 
    a subdirectory of this script's directory. The subdirectory is created if it
    does not exist, and the working directory is restored after completion.

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
        yield subdirectory
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


def venv_run(path, *args, **kwargs):
    '''
    Convenience function for running a python process within the same virtualenv
    as the caller. If relative, the path is relative to this script's directory.
    '''
    with script_directory():
        return Popen([sys.executable, path, *args], **kwargs).pid


def parse_archieml(text):
    '''
    Abstract all archieml preprocessing and parsing to this function
    '''
    text = text.replace('\r', '')
    # Obliterate ALL of google's [a][b][c] comment annotations!
    text = re.sub(r'^\[[a-z]\].+$', '', text, flags=re.M)
    text = re.sub(r'\[[a-z]\]', '', text)
    # Undo some of the auto-capitalization google docs inflicts
    return {k.lower(): v for k,v in archieml.loads(text).items() if v}


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


def nest_parens(text, level=0):
    '''
    Typographically adjust parens such that parens within parens become
    alternating brackets and parens. Use a level argument to move all nested
    parens "down a level" (e.g.: "(hello [world])" --> "[hello (world)]")
    '''
    adjusted = []
    for c in text:
        if c in '([':
            c = '(['[level%2]
            level += 1
        elif c in '])':
            c = '])'[level%2]
            level -= 1
        adjusted.append(c)
    return ''.join(adjusted)


def strip_smartquotes(s):
    '''
    For code mangled by a word processor
    '''
    pairs = '\u201c"', '\u201d"', "\u2018'", "\u2019'"
    replace = lambda s, r: s.replace(*r)
    return reduce(replace, pairs, s)


def log(*s, fatal=False, tty=sys.stdout.isatty(), color='green', **kw):
    '''
    Tee-style logging with timestamps
    '''
    # Support foreground colors specified by name or ANSI escape number
    colors = dict(zip('red green yellow blue magenta cyan white'.split(), range(31,38)))
    colors.update({str(v):v for k,v in colors.items()})
    color = colors[str(color).lower()]

    # Select color or plain logging depending on terminal type
    logfmt = ('\x1b[30m[\x1b[{}m{{:^10}}\x1b[30m]\x1b[0m'.format(color) if tty else '[{:^10}]').format

    # Format and colorize special messages (those with a colon after the first word)
    if ':' in s[0]:
        head, tail = s[0].split(':', maxsplit=1)
        s = [logfmt(head), tail, *s[1:]]
    s = ' '.join(map(str, s))

    # Log to file
    script_dir = os.path.dirname(os.path.realpath(__file__))
    with open(os.path.join(script_dir, 'log.txt'), 'a', encoding="utf-8") as file:
        file.write('{} {}\n'.format(datetime.datetime.utcnow().isoformat(), s))

    # Log to terminal
    print(s, **kw)

    # Exit
    if fatal:
        print('Quitting.')
        sys.exit(int(fatal))

warn = lambda *s, fatal=False, color='red', **kw: log(*s, fatal=fatal, color=color, **kw)


def die(*s):
    '''
    Perl-y
    '''
    log(*s, fatal=True)


__all__ = [
    'DEBUG',
    'DEVELOP',
    'ARABIC_BOUNDARY_REGEX',
    'JSONEncoder',
    'script_directory', 
    'script_subdirectory', 
    'only_one_process',
    'venv_run',
    'parse_archieml',
    'mimetype',
    'slugify', 
    'nest_parens',
    'strip_smartquotes',
    'log', 
    'warn',
    'die', 
]
