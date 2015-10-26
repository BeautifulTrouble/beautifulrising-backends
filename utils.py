
import datetime
import os
import re

import unidecode

def slugify(s):
    s = unidecode.unidecode(s).lower()
    s = s.replace("'", '')
    return re.sub(r'\W+', '-', s)

def compact(s):
    s = unidecode.unidecode(s).lower()
    return re.sub(r'\s+', '', s)

def log(*s, fatal=None):
    s = ' '.join(s)
    script_dir = os.path.dirname(os.path.realpath(__file__))
    with open(os.path.join(script_dir, 'log.txt'), 'a') as file:
        file.write('{} {}\n'.format(datetime.datetime.utcnow().isoformat(), s))
        print(s)
    if fatal:
        print('Quitting.')
        sys.exit(int(fatal))


__all__ = ['slugify', 'compact', 'log']
