#!/usr/bin/env python3

# Auto-install and activate a virtualenv if possible
import autovenv
autovenv.run()

import argparse
import datetime
import os
import re
import sys
from io import BytesIO, StringIO
from itertools import zip_longest

import archieml
import couchdb
import driveclient
from apiclient.http import MediaIoBaseUpload
from jinja2 import Template

from utils import *


DB_NAME = 'toolbox'
CONFIG_FILE_NAME = 'CONFIG'
ROOT_FOLDER_NAME = 'BR CONTENT'
SERVICE_ACCOUNT_JSON_FILENAME = 'google-docs-etl-bff03cc95d8e.json'



class ContentLoader(object):
    def __init__(self):
        # Parse command line arguments
        argparser = argparse.ArgumentParser(description="")
        argparser.add_argument('--get', type=str, metavar='DOCUMENT_ID', action='append',
            help="Fetch a single document by its globally unique id, then update associated "
            "metadata and assets. Specify multiple times to get multiple documents at once. "
        )
        argparser.add_argument('--watch-docs', action='store_true',
            help="Register callbacks for all files found. Don't fetch any content."
        )
        argparser.add_argument('--report-broken-docs', action='store_true', 
            help="Produce a document containing information about documents which lack required "
            "fields for their type, as specified in the config document."
        )
        argparser.add_argument('--reset-db', action='store_true',
            help='Reset any existing database called "{}" before loading content.'.format(DB_NAME)
        )
        self.options,_ = argparser.parse_known_args()

        # Connect to couchdb
        self.couch = couchdb.Server()
        if self.options.reset_db and DB_NAME in self.couch:
            confirm = input('Are you sure you want to delete the database "{}" [y/N]? '.format(DB_NAME))
            if confirm.lower() == 'y':
                del self.couch[DB_NAME]
        self.db = self.couch[DB_NAME] if DB_NAME in self.couch else self.couch.create(DB_NAME)

        # Connect to Google Drive API
        self.drive = driveclient.DriveClient('google-docs-etl', 
            scopes='https://www.googleapis.com/auth/drive',
            service_account_json_filename=SERVICE_ACCOUNT_JSON_FILENAME)
        self.root = self.drive.folder(ROOT_FOLDER_NAME) 
        if not self.root:
            die("Can't find the root folder!")

        # Master lists of content
        self.all_content = []
        self.published_content = []

        # Load CONFIG_FILE_NAME from ROOT_FOLDER_NAME and store as "config:api" & self.config
        self.configure()

        # Get all or some documents according to command line flags
        documents = self.get_documents()

        # Import docs or don't, depending on your --options
        for document in documents:
            if self.options.watch_docs:
                pass
                #XXX:
                
            else:
                published = re.search(self.config['published-keyword'], document.title)
                if published or self.options.report_broken_docs:
                    content = self.extract_and_transform(document)
                    self.all_content.append(content)
                    if published:
                        self.published_content.append(content)

        self.filter_broken_documents()
        #self.download_assets()
        for content_item in self.published_content:
            self.save(content_item)

    def id(self, content):
        '''
        Produce consistent ids for couchdb
        '''
        return '{type}:{slug}'.format(**content)


    def save(self, doc, id=None):
        '''
        Write doc to couchdb, adding a revision if the document exists
        '''
        doc.update(_id=id or self.id(doc))
        try:
            doc.update(_rev=self.db[doc['_id']]['_rev'])
        except couchdb.http.ResourceNotFound: pass
        self.db.save(doc)


    def configure(self):
        '''
        Fetch, parse and store the config document
        '''
        document = self.root.file(CONFIG_FILE_NAME)
        if not document:
            die("Can't find a config file!")

        self.config = c = {}
        data = archieml.loads(document.text)

        c['plural-separator'] = data.get('plural-separator', r'(?:\s*,|\s+&)\s+')
        c['plural-keys'] = data.get('plural-keys', {})
        c['collate'] = data.get('collate', {})
        c['published-keyword'] = data.get('published-keyword', r'(?i)\bdone$')
        c['assets'] = data.get('assets', [{'key': 'image', 'source': 'images', 'destination': 'images'}])
        c['content'] = data.get('content', ['^content$'])
        c['types'] = []
        for k,v in data.items():
            if k.endswith('-types'):
                for d in v:
                    d['name'] = slugify(d['one'])
                c['types'] += v
                c[k] = v

        self.save(c, 'config:api')


    def get_documents(self):
        '''
        Get content for all (or some) documents and populate all_content or published_content
        '''
        if self.options.get:
            documents = filter(None, (self.drive.get(id) for id in self.options.get))
        else:
            documents = []
            for folder in self.root.folders:
                # Try to match one of the content types with this folder's name
                if any(re.search(pat, folder.title) for pat in self.config['content']):
                    log('Looking for content in folder "{}"'.format(folder.title))
                    documents.extend(folder.documents)
        return documents


    def extract_and_transform(self, document):
        '''
        Process a document and return a content item.
        '''
        text = document.text
        text = text.replace('\r', '')
        # Strip google's comment annotations if at all possible
        text = re.sub(r'(?:\[([a-z])\1?\])+$|^\[([a-z])\2?\].+$', '', text, flags=re.M) 
        content = archieml.loads(text)

        #TODO: Strip as much whitespace from values as possible

        # Add a few useful bits
        type = next((T['name'] for T in self.config['types'] if T['name'] in content), '')
        content['type'] = type
        content['title'] = content.get(type, '')
        content['slug'] = slugify(content['title'], allow=':')
        content['document_id'] = document.id
        content['document_link'] = document.alternateLink
        content['document_title'] = document.title

        # Convert singular keys to plural keys and split them up as lists
        for plural_key,singular_key in self.config['plural-keys'].items():
            single, plural = content.get(singular_key), content.get(plural_key)
            if single:
                content[plural_key] = [single]
                del content[singular_key]
            if plural:
                multiline = re.split(r'\s*\n\s*\n\s*', plural)
                content[plural_key] = (multiline if len(multiline) > 1 else 
                                       re.split(self.config['plural-separator'], plural))

        # Collate (zip) specific plural data together and give it new names
        for result,mapping in self.config['collate'].items():
            collated_lists = zip_longest(*(content.get(existing_key, []) 
                                for existing_key in mapping.values()), fillvalue='')
            collated_dicts = [dict(zip(mapping.keys(), L)) for L in collated_lists]
            for old_key in mapping.values():
                content.pop(old_key, None)
            content[result] = collated_dicts
        
        log("Extracted {} ({}: {})".format(document.id, type, content['title']))
        return content


    def filter_broken_documents(self):
        '''
        Remove docs from published_content if they have obvious content problems 
        '''
        missing = []
        for content_item in self.all_content:
            missing_items = []
            required = next((T.get('required', '') for T in self.config['types'] if T['name'] == content_item['type']), '')
            required = re.split(self.config['plural-separator'], required)
            for requirement in filter(None, required):
                if not any(content_item.get(field.strip()) for field in requirement.split('|')):
                    missing_items.append(requirement.replace('|', ' or '))
            if missing_items:
                missing.append((content_item, missing_items))
                if content_item in self.published_content:
                    self.published_content.remove(content_item)
        if self.options.report_broken_docs:
            self.report_broken_documents(missing)


    def download_assets(self):
        '''
        This modifies published_content and should be called before the final db store.
        '''
        for asset_spec in self.config['assets']:
            asset_folder = self.root.folder(asset_spec['source'])
            if not asset_folder:
                continue
            with script_subdirectory(asset_spec['destination']):
                log('Downloading assets from folder "{}" into "{}"...'.format(asset_folder.title, os.getcwd()))   
                # Iterate first over drive files because it's slow
                for asset in asset_folder.files:
                    # Find content items which require the asset
                    for content_item in self.published_content:
                        asset_name = content_item.get(asset_spec['key'])
                        if asset_name == asset.title or asset_name == asset.title.rsplit('.')[0]:
                            # Update the filename
                            content_item[asset_spec['key']] = asset.title
                            asset.save_as(asset.title)
                            log('Downloaded asset "{}"'.format(asset.title))


    def write_document(self, name, parent, bytestring, source_mimetype='text/plain'):
        '''
        Given a filename and a parent folder, create or update a document with a bytestring
        Docs will be converted from their source_mimetype
        '''
        params = {
            'body': {
                'title': name,
                'mimeType': 'application/vnd.google-apps.document',
            },
            'convert': True,
            'media_body': MediaIoBaseUpload(BytesIO(bytestring), mimetype=source_mimetype),
        }
        existing_file = parent.file(name)
        if existing_file:
            self.drive.service.files().update(fileId=existing_file.id, **params).execute()
        else:
            params['body']['parents'] = [{'id': parent.id}]
            self.drive.service.files().insert(**params).execute()


    def report_broken_documents(self, missing):
        report_name = 'Content Error Report'
        timestamp = datetime.datetime.utcnow()
        output = Template('''
            <style>* { font-family: "Consolas"; }</style>
            <h1>{{ report_name }}</h1>
            <p>
                Modifications to this file will be discarded.<br><em>Updated: {{ timestamp }} UTC</em>
            </p>
            <hr>
            {% for content_item,missing_items in missing %}
            <p>
                <a href="{{ content_item.document_link }}">{{ content_item.document_title }}</a><br>
                {% if content_item.authors %}
                {% for author in content_item.authors %}
                <strong>Author:</strong> {{ author.name }} {{ author.email }}<br>
                {% endfor %}
                {% endif %}
                <strong>Missing:</strong> {{ missing_items|join(', ') }}
            </p><p></p>
            {% endfor %}
        ''').render({k:v for k,v in vars().items() if k != 'self'})
        self.write_document(report_name, self.root, output.encode('ascii', 'xmlcharrefreplace'), 'text/html')
                

if __name__ == '__main__':
    ContentLoader()

