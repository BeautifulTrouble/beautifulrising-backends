#!/usr/bin/env python3

import json
import os
import re
import sys
from collections import defaultdict
from functools import reduce

# Auto-install and activate a virtualenv (where available)
if sys.version_info.major >= 3 and sys.version_info.minor >= 3:
    import autovenv
    autovenv.run()

import archieml
import couchclient
import driveclient

from utils import log, slugify, compact


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
    c['published-keyword'] = data.get('published-keyword', r'(?i)\bdone$')

    # Image folder regex
    c['image-folder'] = data.get('image-folder', r'(?i)^images$')

    # These types should specify a folder regex, singular and plural names
    #TODO: Catch missing type specification info
    c['module-types'] = data.get('module-types', [])
    c['other-types'] = data.get('other-types', [])
    c['all-types'] = c['module-types'] + c['other-types']

    # Some keys have plural and singular names (e.g. author:/authors:)
    # and these forms need to be merged together for simplicity.
    c['plural-separator'] = data.get('plural-separator', r'(?:\s*,|\s+&)\s+')
    c['plural-keys'] = data.get('plural-keys', {})

    # Get the map and reduce javascript functions, removing smart quotes
    replace = lambda s,r: s.replace(*r)
    pairs = '\u201c"', '\u201d"', "\u2018'", "\u2019'"
    c['map-functions'] = {name: reduce(replace, pairs, text) 
                          for name,text in data.get('map-functions', {}).items()}
    c['reduce-functions'] = {name: reduce(replace, pairs, text) 
                             for name,text in data.get('reduce-functions', {}).items()}

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

    strip_comments = lambda s,r=re.compile(r'(?:\[([a-z])\1?\])+$|^\[([a-z])\2?\].+$', re.M).sub: r('', s)

    # Keep track of all images
    images = []
    image_folder = None

    # Download content and put it in the database
    for folder in root.folders:

        if re.search(config['image-folder'], folder.title):
            image_folder = folder
            continue

        # This checks the folder name against all the regexes for valid
        # module types and returns the first match or None
        doc_type = next((T for T in config['all-types'] if re.search(T['folder'], folder.title)), None)
        if doc_type:
            log('\nIdentified folder "{}" containing {}...'.format(folder.title, doc_type['many']))

            # No code should ever use the type_slug but content editors may
            # choose to use "module-type: title" over "moduletype: title" so
            # this should be supported. 
            type = compact(doc_type['one'])
            type_slug = slugify(doc_type['one'])

            for document in folder.documents:
                if re.search(config['published-keyword'], document.title):
                    
                    # Remove google docs comments and parse archieml
                    text = strip_comments(document.text.replace('\r',''))
                    content = archieml.loads(text)

                    # Find title by type and ignore documents without them
                    content['type'] = type
                    content['title'] = title = content.get(type_slug, content.get(type, ''))
                    if not title:
                        log('"{}" needs a title. Skipping!'.format(document.title))
                        continue

                    # Convert singular keys to plural keys and split them up as lists
                    for plural_key,singular_key in config['plural-keys'].items():
                        single, plural = content.get(singular_key), content.get(plural_key)
                        if single or plural:
                            if single:
                                content[plural_key] = [single]
                                del content[singular_key]
                            if plural:
                                content[plural_key] = re.split(config['plural-separator'], plural)

                    if 'image' in content:
                        images.append(content['image'])

                    # Store the actual data
                    # TODO: handle duplicate couchdb ids
                    #       can documents be "updated" by
                    #       revision #? how is this best handled?
                    couch[type, title] = content
                    log("Parsed content of {}...".format(document.title))

    # Now that all content has been processed, download the images
    if image_folder:
        log("\nGot documents, now downloading images...")

        # Work from the assets directory
        try:
            os.mkdir(assets_dir)
        except FileExistsError: pass
        os.chdir(assets_dir)

        # XXX: Let's not overdownload, eh?
        #      need to get files with missing extensions too
        for image in image_folder.images:
            if image.title in images or image.title.rsplit('.')[0] in images:
                log("Downloading image {}...".format(image.title))
                #image.save_as(image.title)


if __name__ == '__main__':
    main()
    

