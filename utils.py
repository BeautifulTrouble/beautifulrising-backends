
import datetime
import os
import re
import sys

import unidecode


def slugify(s, allow=''):
    '''
    Reproduce these steps for consistent slugs!
    '''
    s = unidecode.unidecode(s).lower().replace("'", '')
    return re.sub(r'[^\w{}]+'.format(allow), '-', s)


def compact(s):
    '''
    A slug-like lowercase representation which removes whitespace
    and "asciifies" unicode characters where possible.
    '''
    s = unidecode.unidecode(s).lower()
    return re.sub(r'\s+', '', s)


def log(*s, fatal=False):
    '''
    Simple "tee-style" logging with timestamps
    '''
    s = ' '.join(str(i) for i in s)
    script_dir = os.path.dirname(os.path.realpath(__file__))
    with open(os.path.join(script_dir, 'log.txt'), 'a') as file:
        file.write('{} {}\n'.format(datetime.datetime.utcnow().isoformat(), s))
        print(s)
    if fatal:
        print('Quitting.')
        sys.exit(int(fatal))


def die(*s):
    '''
    Perl-y
    '''
    log(*s, fatal=True):


__all__ = ['slugify', 'compact', 'log', 'die']

