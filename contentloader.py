#!/usr/bin/env python3

import json
import os
import re
import sys
from collections import defaultdict

# Auto-install and activate a virtualenv (where available)
if sys.version_info.major >= 3 and sys.version_info.minor >= 3:
    import autovenv
    autovenv.run()

import archieml
import couchclient
import driveclient

from utils import log, slugify, compact


#ROOT_FOLDER = 'Beautiful Rising Toolbox Content Editing Demo'
ROOT_FOLDER = 'BR CONTENT'
SERVICE_ACCOUNT_JSON = 'google-docs-etl-bff03cc95d8e.json'


config = {}
script_dir = os.path.dirname(os.path.realpath(__file__))
assets_dir = os.path.join(script_dir, 'assets')


def parse_config(data):
    '''
    Prepare config data for serialization and storage in the db
    '''
    c = {}

    # This regex is used to mark ready-to-publish documents
    c['publish'] = data.get('publish', r'(?i)\bdone$')

    # Image folder regex
    c['images'] = data.get('images', r'(?i)^images$')

    # These types should specify a folder regex, singular and plural names
    #TODO: Catch missing type specification info
    c['module-types'] = mtypes = data.get('module-types', [])
    c['other-types'] = otypes = data.get('other-types', [])
    c['all-types'] = mtypes + otypes

    return c


def reset_database(couch):
    name = couch.db.name
    del couch.server[name]
    couch.server.create(name)


def main():
    # Get the google drive client
    drive = driveclient.DriveClient('google-docs-etl', 
        service_account_json_filename=SERVICE_ACCOUNT_JSON)

    # Get the root directory
    root = drive.folder(ROOT_FOLDER)
    if not root:
        # TODO: this potential point of failure should be more robust
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
    else:
        log("Can't find a config file!", fatal=True)

    # This is how we match documents which are ready to publish
    ready_to_publish = re.compile(config['publish']).search

    # Keep track of all images
    images = []
    image_folder = None

    # Download content and put it in the database
    for folder in root.folders:

        # Identify the image folder for later
        if re.search(config['images'], folder.title):
            image_folder = folder

        # This checks the folder name against all the regexes for valid
        # module types and returns the first match or None
        doc_type = next((T for T in config['all-types'] if re.search(T['folder'], folder.title)), None)
        if doc_type:

            # No code should ever use the type_slug but content editors may
            # choose to use "module-type: title" over "moduletype: title" so
            # this should be supported. 
            type = compact(doc_type['one'])
            type_slug = slugify(doc_type['one'])

            log('\nIdentified folder "{}" containing {}...'.format(folder.title, doc_type['many']))

            for document in folder.documents:
                if ready_to_publish(document.title):
                    content = archieml.loads(document.text)
                    content['type'] = type
                    content['title'] = title = content.get(type_slug, content.get(type, ''))
                    if not title:
                        log('"{}" needs a title. Skipping!'.format(document.title))
                        continue
                    log("Parsed content of {}...".format(document.title))

                    if 'image' in content:
                        images.append(content['image'])

                    # Store the actual data
                    # TODO: handle duplicate couchdb ids
                    couch[type, title] = content

    # Now that all content has been processed, download the images
    if image_folder:
        # Work from the assets directory
        try:
            os.mkdir(assets_dir)
        except FileExistsError: pass
        os.chdir(assets_dir)

        # Let's not overdownload, eh?
        #for image in set(image_folder.images):

        for image in image_folder.images:
            if image.title in images or image.title.rsplit('.')[0] in images:
                log("Downloading image {}...".format(image.title))
                image.save_as(image.title)


if __name__ == '__main__':
    main()
    

