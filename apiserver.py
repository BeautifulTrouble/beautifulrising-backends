#!/usr/bin/env python
"""
Notes to self:
* many endpoints return a subset
* querystring as opposed to prefix?

"""

import sys

import couchdb
from couchdb.http import ResourceNotFound
from flask import Flask, Response, request, jsonify
from flask_restful import Resource, Api
from werkzeug.exceptions import NotFound

from utils import *


PREFIX = 'api/v1'
GOOGLE_VERIFICATION = 'google5844dd3c739be066.html'


app = Flask(__name__)
api = Api(app)

couch = couchdb.Server()
db = couch['toolbox']

config = db['config:api']


def flatten_args(args, sep=','):
    # Flatten a werkzeug MultiDict containing duplicate, optionally comma-
    # separated query parameters to a dictionary of lists without duplicates
    #   e.g.:     "?name=foo&tags=a,b&tags=c,d&tags=d"
    #   becomes:  {'name': ['foo'], 'tags': ['a', 'c', 'b', 'd']}
    return {k: list({i for splitL in (L.split(sep) for L in args.getlist(k)) for i in splitL})
            for k in args}


# In lieu of a proper couchdb design document, here's a temporary query
#query_by_type = "function(doc){if(doc.type=='%s'){emit(doc.slug,doc)}}"




# Get content types and create endpoints
types = {slugify(T['one']): slugify(T['many']) for T in config['types']}
for singular_name,plural_name in types.items():
    
    class Many(Resource):
        type = singular_name
        query = "function(doc){if(doc.type=='%s'){emit(doc.slug,doc)}}" % singular_name
        def get(self):
            try:
                resources = [doc['value'] for doc in db.query(self.query)]
            except (KeyError, ResourceNotFound): raise NotFound
            args = flatten_args(request.args)
            filtered = []
            if 'tags' in args:
                for doc in resources:
                    pass
                    # filtering should be generic, no?
                    # here's where we look at the taxonomies
                    # which should be in the config.
                    
                    
                #resources = [d for d in 
                print(args['tags'])
            
            return resources
    api.add_resource(Many, '/{PREFIX}/{plural_name}'.format(**vars()), endpoint=plural_name)


    class One(Resource):
        type = singular_name
        def get(self, id):
            try:
                resource = db['{}:{}'.format(self.type, id)]
            except ResourceNotFound: raise NotFound
            return resource
    api.add_resource(One, '/{PREFIX}/{singular_name}/<id>'.format(**vars()), endpoint=singular_name)
            

# Expose the config for front-ends
class Config(Resource):
    def get(self):
        return config
api.add_resource(Config, '/{PREFIX}/config'.format(**vars()))


@app.route(GOOGLE_VERIFICATION)
def google_verification():
    return 'google-site-verification: {}'.format(GOOGLE_VERIFICATION)


if __name__ == '__main__':
    app.run(port=6969, debug='debug' in sys.argv)

