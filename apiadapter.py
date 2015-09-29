#!/usr/bin/env python

import json
import sys

from flask import Flask, Response, request

import couchclient


PREFIX = 'api'


app = Flask(__name__)
couch = couchclient.CouchClient('toolbox')
config = couch['config', 'main']


query_by_type = "function(doc){if(doc.type=='%s'){emit(doc.slug,doc)}}"


for plural,singular in config['doc_types'].items():
    def get_many():
        type = config['doc_types'][request.url_rule.endpoint]
        try: result = json.dumps([i.value for i in couch.query(query_by_type % type)])
        except: return '', 404
        return Response(result, 200, mimetype='application/json')
    app.add_url_rule('/{PREFIX}/{plural}'.format(**vars()), plural, get_many)

    def get_one(id):
        type = request.url_rule.endpoint
        try: result = json.dumps(couch[type, id])
        except: return '', 404
        return Response(result, 200, mimetype='application/json')
    app.add_url_rule('/{PREFIX}/{singular}/<id>'.format(**vars()), singular, get_one)


if __name__ == '__main__':
    app.run(port=6969, debug='debug' in sys.argv)
    
