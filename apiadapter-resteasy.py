#!/usr/bin/env python


import json
import sys

from couchdb.http import ResourceNotFound
from flask import Flask, Response, request, jsonify
from flask.ext.resteasy import Api, Resource
from werkzeug.exceptions import NotFound

import couchclient
from utils import slugify, compact, log


PREFIX = 'api/v1'

app = Flask(__name__)
api = Api(app)
couch = couchclient.CouchClient('toolbox')
config = couch['config', 'main']


class JSEscaped(object):
    def __getitem__(self, item):
        # TODO: Can reduce functions live in the config document?
        pass

    functions = {
        'type': """
            function(doc) {
                if (doc.type == '<<>>') {
                    emit(doc.slug, doc);
                }
            }
        """,
        'type_and_slug': """
            // TODO
        """,
    }


query_by_type = "function(doc){if(doc.type=='%s'){emit(doc.slug,doc)}}"
types = {compact(T['one']): compact(T['many']) for T in config['all-types']}

for singular_name,plural_name in types.items():

    @api.resource('/{PREFIX}/{plural_name}'.format(**vars()), endpoint=plural_name)
    class Many(Resource):
        type = singular_name
        def get(self):
            try:
                return list(couch.query(query_by_type % self.type))
            except ResourceNotFound: raise NotFound

    @api.resource('/{PREFIX}/{singular_name}/<id>'.format(**vars()), endpoint=singular_name)
    class One(Resource):
        type = singular_name
        def get(self, id):
            try:
                return couch[self.type, id]
            except ResourceNotFound: raise NotFound

    
@api.resource('/{PREFIX}/config'.format(**vars()))
class Config(Resource):
    def get(self):
        return config


if __name__ == '__main__':
    app.run(port=6969, debug='debug' in sys.argv)
    
