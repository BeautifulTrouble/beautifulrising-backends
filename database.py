#encoding: utf-8

'''
This module provides convenience classes to query and modify a single CouchDB
database as specified in config.py. Basic usage looks like this:

    methodologies = DatabaseDocumentQueryset(type='methodology')
    methodologies[0]['new_key'] = 'value'
    methodologies.save()

It's not meant to be fancy like an ORM, but basic combinatorial queries work:

    lonely_planets = DatabaseDocumentQueryset(type='planet', mood='lonely')
    americans = DatabaseDocumentQueryset.regex_query(type='^person$', name='^.{4}$')
'''


import collections
import copy
import hashlib
import json
import textwrap
import warnings

import couchdb

from utils import log, warn

from config import (
    DB_NAME,
    DB_SERVER,
)


warnings.simplefilter('once', PendingDeprecationWarning)


# Stable hash of a dictionary's items (used for naming views)
def hashobj(obj):
    string = '{}{}'.format(sorted(obj.keys()), sorted(obj.values())).encode()
    return hashlib.md5(string).hexdigest()


# We only need one DatabaseConnector object, so we may as well insantiate it
staticclass = lambda cls: cls()

@staticclass
class DatabaseConnector(object):
    '''
    Abstraction of common database activities
    '''
    def __init__(self):
        self.server_address = DB_SERVER
        self.database_name = DB_NAME + '_testing'

        # A write queue is used for lazy persistence
        self.write_queue = []

        # Connect
        self.server = couchdb.Server(self.server_address)
        self.get_or_create_database()


    def _get_view(self, mapping, regex):
        '''
        Query a view and return documents. This method will create the relevant
        view by calling DatabaseConnector._make_view if needed.
        '''
        view = ('by_regex/' if regex else 'by_exact/') + hashobj(mapping)
        try:
            results = DatabaseDocumentQueryset([row['value'] for row in self.db.view(view)])
        except couchdb.http.ResourceNotFound:
            self._make_view(mapping, regex)
            results = DatabaseDocumentQueryset([row['value'] for row in self.db.view(view)])
        return results


    def _make_view(self, mapping, regex):
        '''
        Creates a view for every query used by constructing a js function
        '''
        mapping_hash = hashobj(mapping)

        # Create a javascript condition string by &&-joining an appropriate
        # fragment. If regex isn't specified, use exact matching
        if regex:
            ddoc_name = '_design/by_regex'
            fragment = r'''/{regex}/.test(doc['{prop}'] || '')'''
            condition = ' && '.join(fragment.format(**vars()) for prop,regex in mapping.items())
        else:
            ddoc_name = '_design/by_exact'
            fragment = r'''{string} == doc['{prop}']'''
            condition = ' && '.join(fragment.format(prop=prop, string=json.dumps(string))
                                    for prop,string in mapping.items())

        mapfun = textwrap.dedent(r'''
            function(doc) {
                if (%s) {
                    emit(doc.document_id, doc);
                }
            }
        '''.strip('\n')) % condition

        ddoc = self.db.get(ddoc_name)
        if ddoc:
            ddoc['views'][mapping_hash] = {'map': mapfun}
        else:
            ddoc = {'views': {mapping_hash: {'map': mapfun}}}
        ddoc['_id'] = ddoc_name
        self.save(ddoc, now=True)


    def mapfunction_query(self, mapfunction):
        '''
        CouchDB 2.x no longer supports temporary views as used by
        DatabaseConnector.mapfunction_query, so future database upgrades will
        break on this call.
        '''
        warnings.warn(self.query.__doc__, PendingDeprecationWarning, stacklevel=4)
        return [doc['value'] for doc in self.db.query(mapfunction)]


    def exact_query(self, **mapping):
        '''
        Return matching docs for given key_names='exact_values'
        '''
        return self._get_view(mapping, regex=False)


    def regex_query(self, **mapping):
        '''
        Return matching docs for given key_names='^regular_expressions$'
        '''
        return self._get_view(mapping, regex=True)


    def save(self, *objects, now=False):
        '''
        Enqueue objects for saving in the database. If the now flag is set,
        the save will take place immediately.
        '''
        # Freeze contents so mutable data doesn't change before actual write
        # XXX: Vet this assumption
        objects = [copy.deepcopy(obj) for obj in objects]

        # Enqueue the objects for saving
        self.write_queue.extend(objects)

        # Real persistence
        if now or not objects:
            retry_queue = []

            for success,id,rev_or_exc in self.db.update(self.write_queue):
                log('saved:', id) if success else warn('updating:', id, color='yellow')

                # Enqueue conflicting (existing) objects for a retry save
                if isinstance(rev_or_exc, couchdb.http.ResourceConflict):
                    retry = next(obj for obj in self.write_queue if obj['_id'] == id)

                    # Indicate that we wish to update the existing revision
                    retry.update(_rev=self.db[id]['_rev'])
                    retry_queue.append(retry)

            if retry_queue:
                for success, id, rev_or_exc in self.db.update(retry_queue):
                    print(success, id, rev_or_exc)
                    log('saved:', id) if success else warn('lost:', id)

            self.write_queue.clear()


    def get_or_create_database(self):
        '''
        Get or create database as needed
        '''
        if self.database_name not in self.server:
            self.server.create(self.database_name)
        self.db = self.server[self.database_name]
        return self.db


    def delete_database(self):
        '''
        A full reset
        '''
        confirm = input('Delete the database "{}" [y/N]? '.format(self.database_name))
        if confirm.lower() == 'y' and self.database_name in self.server:
            del self.server[self.database_name]
            warn('deleted:', self.database_name)


class DatabaseDocument(collections.OrderedDict):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)


    def save(self, now=False):
        self['_id'] = ':'.join(self.get(k,'') for k in ('slug', 'type', 'document_id'))
        DatabaseConnector.save(self, now=now)


class DatabaseDocumentQueryset(collections.UserList):
    '''
    Simple query abstraction handles two major relevant use cases:

        1. Regex lookup on document IDs when a string is passed
            DatabaseDocumentQueryset("flash-mob")
        2. Key/Value matching on documents when keys are passed
            DatabaseDocumentQueryset(type="methodology")
    '''
    def __init__(self, *a, **kw):
        if a and isinstance(a[0], list):
            # If a list is passed, it will be interpreted as a request to wrap
            # it in the DatabaseDocumentQueryset type
            documents = a[0]

        elif a and isinstance(a[0], str):
            # If a string is passed, it will be interpreted to be a regex
            # query on couch document ids
            documents = DatabaseConnector.regex_query(_id=a[0])
            documents.extend(a[1:])

        elif kw:
            # If keyword arguments are used, they will be interpreted as
            # a mapping of exact mactches
            documents = DatabaseConnector.exact_query(**kw)

        documents = [DatabaseDocument(d) for d in documents]
        super().__init__(documents)


    def __repr__(self):
        return '{}({})'.format(self.__class__.__name__, list(self))


    def save(self, now=True):
        '''
        Save a queryset immediately
        '''
        for d in self:
            d.save()
        DatabaseConnector.save(now)


    @staticmethod
    def exact_query(**mapping):
        return DatabaseConnector.exact_query(**mapping)


    @staticmethod
    def regex_query(**mapping):
        return DatabaseConnector.regex_query(**mapping)


