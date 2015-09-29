#!/usr/bin/env python3

import json
import os
import sys

# Auto-install and activate a virtualenv (where available)
if sys.version_info.major >= 3 and sys.version_info.minor >= 3:
    import autovenv
    autovenv.run()

import archieml
import couchclient
import driveclient

from utils import log, slugify, compact


ROOT_FOLDER = 'Beautiful Rising Toolbox Content Editing Demo'
ASSETS_DIR = 'assets'


config = {}
script_dir = os.path.dirname(os.path.realpath(__file__))
assets_dir = os.path.join(script_dir, ASSETS_DIR)


def parse_config(data):
    #Perform any transformations necessary before serializing config data
    c = {}
    c['module_types'] = data.get('module_types', {})
    c['other_types'] = data.get('other_types', {})
    c['doc_types'] = dict(c['module_types'].items() | c['other_types'].items())
    c['types_with_resources'] = data.get('types_with_resources', [])
    return c


def reset_database(couch):
    name = couch.db.name
    del couch.server[name]
    couch.server.create(name)


def main():
    # Get the google drive client
    drive = driveclient.DriveClient('google-docs-etl', 
        service_account_json_filename='google-docs-etl-bff03cc95d8e.json')

    # Get the root directory containing all the content types
    root = drive.folder(ROOT_FOLDER)
    if not root:
        log("Can't find the root folder!", fatal=True)

    # Get the couch client and flush the old database
    couch = couchclient.CouchClient('toolbox')
    reset_database(couch)

    # Parse the config document for settings
    for document in root.documents:
        if compact(document.title) == 'config':
            data = archieml.loads(document.text)
            config.update(parse_config(data))
            couch['config', 'main'] = config
            break

    # Work from the assets directory
    try:
        os.mkdir(assets_dir)
    except FileExistsError: pass
    os.chdir(assets_dir)

    # Download content and put it in the database
    for folder in [f for f in root.folders if compact(f.title) in config['doc_types']]:
        images = []
        for document in folder.documents:
            log("Parsing content of {}...".format(document.title))
            content = archieml.loads(document.text)
            content.update({'title': document.title})
            if 'image' in content:
                images.append(content['image'])
            type = config['doc_types'][compact(folder.title)]
            couch[type, document.title] = content
        for image in folder.images:
            if image.title in images:
                log("Downloading image {}...".format(image.title))
                image.save_as(image.title)
    

if __name__ == '__main__':
    main()
    

