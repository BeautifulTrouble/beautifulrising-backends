'''
Simplify some basic interactions with couchdb, like auto-slugifying ids.
'''

import couchdb
import couchdb.design

from utils import slugify


class CouchClient(object):
    def __init__(self, db_name, url='http://127.0.0.1:5984'):
        self.server = couchdb.Server(url=url)
        try:
            self.db = self.server[db_name]
        except couchdb.http.ResourceNotFound:
            self.db = self.server.create(db_name)

    def __getattr__(self, attr):
        '''
        Attribute access on an instance of this class acts as a proxy to the
        underlying database object.
        '''
        return getattr(self.db, attr, '')

    def __getitem__(self, item):
        '''
        Proxy the underlying database object and auto-slugify the string(s)
        provided. Example:

            bern = couch['candidate:bernie-sanders']
            bern = couch['candidate', 'Bernie Sanders']

        '''
        if isinstance(item, tuple):
            item = ':'.join(item)
        return self.db[slugify(item, allow=':')]

    def __setitem__(self, item, value):
        '''
        Proxy the underlying database object and auto-slugify the string(s)
        provided. The slug and type will be added to the document. Example:

            couch['candidate:donald-trump'] = {...}
            couch['candidate', 'Donald Trump'] = {...}

        '''
        if not isinstance(item, tuple):
            item = item.split(':', 1)
        type, slug = (slugify(i, allow=':') for i in item)
        value.update({'slug': slug, 'type': type})
        self.db[':'.join((type, slug))] = value

    def __delitem__(self, item):
        '''
        Proxy the underlying database object and auto-slugify the string(s)
        provided. Example:

            del couch['candidate:bernie-sanders']
            del couch['candidate', 'Bernie Sanders']

        '''
        if isinstance(item, tuple):
            item = ':'.join(item)
        del self.db[slugify(item, allow=':')]

    def add_view(self, view_name, map, reduce=None, **kw):
        '''
        Convenience function for adding a map/reduce view
        '''
        design, name = view_name.split('/')[:2]
        view = couchdb.design.ViewDefinition(design, name, map, reduce_fun=reduce, **kw)
        view.sync(self.db)


