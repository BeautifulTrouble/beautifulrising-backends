#encoding: utf-8

import copy

import couchdb

from utils import log


class DatabaseConnector(object):
    '''
    Abstraction of database activities
    XXX: This likely should serve as the common mechanism for both ETL & API
    '''
    def __init__(self, server_address, database_name):
        self.server_address = server_address
        self.database_name = database_name

        # A write queue is used for lazy persistence
        self.write_queue = []

        # Connect
        self.server = couchdb.Server(self.server_address)
        self.get_or_create_database()


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
        XXX: Unwatch google drive step should happen in the caller
        '''
        confirm = input('Delete the database "{}" [y/N]? '.format(self.database_name))
        if confirm.lower() == 'y' and self.database_name in self.server:
            del self.server[self.database_name]
            log('deleted:', self.database_name, color=31)


    def save(self, *objects, now=False):
        '''
        XXX:
        '''
        # XXX: for testing, how do we ensure objects have an _id?
        for o in objects:
            o['_id'] = [*o.keys()][0]

        # Although objects aren't really stored until an explicit request to
        # do so is made, it seems reasonable to enqueue a copy of the objects
        # in case mutable elements within them continue to change.
        # XXX: vet this assumption
        objects = [copy.deepcopy(obj) for obj in objects]
        self.write_queue.extend(objects)

        # Real persistence
        if now or not objects:
            retry_queue = []

            for success,id,rev_or_exc in self.db.update(self.write_queue):
                log('saved:', id) if success else log('updating:', id, color=33)

                # Enqueue conflicting (existing) objects for a retry save
                if isinstance(rev_or_exc, couchdb.http.ResourceConflict):
                    retry = next(obj for obj in self.write_queue if obj['_id'] == id)

                    # Indicate that we wish to update the existing revision
                    retry.update(_rev=self.db[id]['_rev'])
                    retry_queue.append(retry)

            if retry_queue:
                for success,id,rev_or_exc in self.db.update(retry_queue):
                    log('saved:', id) if success else log('lost:', id, color=31)

            self.write_queue.clear()

