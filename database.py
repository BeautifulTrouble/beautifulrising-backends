#encoding: utf-8

'''
This module provides convenience classes to query and modify a single CouchDB
database as specified in config.py. Basic usage looks like this:

    methodologies = DatabaseConnector.query(type='methodology')
    methodologies[0]['new_key'] = 'value'
    methodologies.save()

It's not meant to be fancy like an ORM, but basic combinatorial queries work:

    lonely_planets = DatabaseConnector.query(type='planet', mood='lonely')
    americans = DatabaseConnector.query_regex(type='^person$', name='^.{4}$')
'''


import collections
import copy
import hashlib
import json
import textwrap
import warnings

import couchdb

from utils import DEBUG, log, warn

from config import (
    DB_NAME,
    DB_SERVER,
)


if DEBUG:
    DB_NAME = DB_NAME + '_testing'


warnings.simplefilter('once', PendingDeprecationWarning)


def hashobj(obj):
    '''
    Produces a stable hash of a dictionary's items. This is used when naming
    and looking up automatically-generated views for queries
    '''
    # Support objects which aren't dictionaries, but stability isn't guaranteed
    if not isinstance(obj, dict):
        obj = {str(obj): None}

    keys = sorted(obj.keys())
    # Support one level of embedded lists so that these produce a stable hash: 
    #   {'type': ['thIs', 'thAt']}
    #   {'type': ('thAt', 'thIs')}
    values = sorted(str(sorted(v) if isinstance(v, (list, tuple)) else v) for v in obj.values())
    string = '{}{}'.format(keys, values).encode()
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
        self.database_name = DB_NAME

        # A write queue is used for lazy persistence
        self.write_queue = []

        # Connect
        self.server = couchdb.Server(self.server_address)
        self.get_or_create_database()


    def _create_map_function(self, mapping, query_type='exact'):
        '''
        Constructs a javascript map function for simple combinatoric queries.
        The queries are structured as an AND-joined sequence of OR-joined
        matches using either regex or exact string matching on document fields.

        An example mapping for a regex query might look like:

            {'type': 'person', 'last_name': ['s[eo]n$', 'strom$']}

        ...and would construct a view function to match docs with types person
        or salesperson containing last names like Anderson, Andersen & Bergstrom
        '''
        # Create a javascript condition string by joining an appropriate fragment
        if query_type == 'regex':
            fragment = '''/{string}/.test(doc['{field}'] || '')'''
            string_func = str
        else:
            fragment = '''doc['{field}'] == {string}'''
            string_func = json.dumps

        clauses = []
        for field, match in mapping.items():
            if isinstance(match, (list, tuple)):
                fragments = (fragment.format(field=field, string=string_func(s)) for s in match)
                clauses.append('({})'.format(' || '.join(fragments)))
            else:
                clauses.append(fragment.format(field=field, string=string_func(match)))
        condition = ' && '.join(clauses)

        map_function = textwrap.dedent(r'''
            function(doc) {
                if (%s) {
                    emit(doc.document_id, doc);
                }
            }
        '''.strip('\n')) % condition

        return map_function


    def _get_or_create_view(self, mapping, query_type):
        '''
        Query a view and return documents. This method will create the relevant
        view by calling DatabaseConnector.create_view if needed.
        '''
        view_name = query_type + '/' + hashobj(mapping)

        results = self.view(view_name)
        if not results:
            self.create_view(self._create_map_function(mapping, query_type), view_name)
            results = self.view(view_name)

        return results


    def create_view(self, map_function, view_name=None):
        '''
        Produce a custom view from a map function and return that view's name
        for use with DatabaseConnector.view
        '''
        if view_name is None:
            view_name = 'custom/' + hashobj(map_function)

        # The full view name is the name of a design document, a slash & a view
        ddoc_name, view = view_name.split('/', maxsplit=1)
        ddoc_name = '_design/' + ddoc_name

        # Ensure the design document exists and create the view
        ddoc = self.db.get(ddoc_name)
        if ddoc:
            ddoc['views'][view] = {'map': map_function}
        else:
            ddoc = {'views': {view: {'map': map_function}}}
        ddoc['_id'] = ddoc_name
        self.save(ddoc, now=True)

        return view_name


    def mapfunction_query(self, mapfunction):
        '''
        CouchDB 2.x no longer supports temporary views as used by
        DatabaseConnector.mapfunction_query, so this call may not work.
        '''
        warnings.warn(self.query.__doc__, PendingDeprecationWarning, stacklevel=4)
        return [doc['value'] for doc in self.db.query(mapfunction)]

    
    def query(self, **mapping):
        '''
        Specify a mapping of fields to exact matches where those matches can
        be either strings or lists of OR-joined possible string matches.
        '''
        return self._get_or_create_view(mapping, 'exact')


    def query_one(self, **mapping):
        '''
        Same as DatabaseConnector.query, but returns only the first result
        '''
        results = self._get_or_create_view(mapping, 'exact')
        if results:
            return results[0]
    

    def query_regex(self, **mapping):
        '''
        Specify a mapping of fields to regex matches where those matches can
        be either string regexes or lists of OR-joined possible regex matches.
        '''
        return self._get_or_create_view(mapping, 'regex')


    def query_regex_one(self, **mapping):
        '''
        Same as DatabaseConnector.query_regex, but returns only the first result
        '''
        results = self._get_or_create_view(mapping, 'regex')
        if results:
            return results[0]


    def view(self, view_name):
        '''
        Produce a DatabaseDocumentQueryset from a given view. Callers should
        check for an empty result, indicating the view may not exist.
        '''
        try:
            results = DatabaseDocumentQueryset([row['value'] for row in self.db.view(view_name)])
        except couchdb.http.ResourceNotFound:
            results = DatabaseDocumentQueryset()
        return results


    def save(self, *objects, now=False):
        '''
        Enqueue objects for saving in the database. If the now flag is set,
        the save will take place immediately.
        '''
        # Freeze contents so mutable data doesn't change before actual write
        # XXX: Vet this assumption about copies
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
    Simple container for a collection of DatabaseDocument objects
    '''
    def __init__(self, *a, **kw):
        documents = []

        if a and isinstance(a[0], list):
            # If a list is passed, it will be interpreted as a request to wrap
            # it in the DatabaseDocumentQueryset type
            documents = [DatabaseDocument(d) for d in a[0]]

        super().__init__(documents)


    def __repr__(self):
        return '{}({})'.format(self.__class__.__name__, list(self))


    def _json(self):
        return self.data


    def save(self, now=True):
        '''
        Save a queryset immediately
        '''
        for d in self:
            d.save()
        DatabaseConnector.save(now)


