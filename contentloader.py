# fmt: off

import argparse
import concurrent.futures
import json
import math
import os
import re
import shlex
import sys
import time
from copy import deepcopy
from datetime import datetime
from dateutil import parser
from hashlib import md5
from subprocess import Popen
from urllib.parse import urljoin

import couchdb
import driveclient
import jinja2
import ftlangdetect
import requests
from fuzzywuzzy.process import extractOne
from icu import ListFormatter, Locale

# Kludge to fix broken google-api-python-client
from oauth2client import file


from utils import *
from config import *


NEW = '_new_content'


class ContentLoader(object):
    def __init__(self):
        # Parse command line arguments
        arg_parser = argparse.ArgumentParser(description="")
        exclusive_args = arg_parser.add_mutually_exclusive_group()
        exclusive_args.add_argument('--id', type=google_doc_id, metavar='DOCUMENT_ID', action='append', default=[], dest='ids',
            help="Fetch a single document by its globally unique id, then update associated metadata and assets. Specify multiple times to get multiple documents at once.")
        exclusive_args.add_argument('--change-id', type=str, metavar='CHANGE_ID', action='append', default=[], dest='changes',
            help="Fetch a single document by its ephemeral change id, then update associated metadata and assets. Specify multiple times to get multiple documents at once.")
        exclusive_args.add_argument('--assets', action='store_true',
            help="Download and convert all assets and quit.")
        exclusive_args.add_argument('--regenerate-previews', action='store_true',
            help="Update external site preview images and quit.")
        arg_parser.add_argument('--no-previews', action='store_true',
            help="Skip site preview generation.")
        exclusive_args.add_argument('--watch-docs', action='store_true',
            help="Initiate a request to watch drive for changes and quit. It will expire in one day.")
        exclusive_args.add_argument('--stop-watching', action='store_true',
            help="If the db has any record of a watch request, request that it be cancelled and quit.")
        exclusive_args.add_argument('--local', action='store_true',
            help="Perform a full reload using locally cached data rather than fetching it from google drive.")
        exclusive_args.add_argument('--save-local', action='store_true',
            help="Save local cache of data for use with --local and quit.")
        exclusive_args.add_argument('--delete-db', action='store_true',
            help=f'Delete any existing database named "{DB_NAME}".')
        exclusive_args.add_argument('--test-match', type=str, metavar='SLUG',
            help="Fuzzy match the given slug against existing content")

        self.options, _ = arg_parser.parse_known_args()

        # Connect to couchdb
        self.couch = couchdb.Server(DB_SERVER)
        self.db_get_or_create()

        # Connect to Google Drive and get the root folder
        self.drive = driveclient.DriveClient(DRIVE_CLIENT_NAME,
            scopes='https://www.googleapis.com/auth/drive',
            service_account_json_filename=DRIVE_SERVICE_ACCOUNT_JSON_FILENAME)
        self.root = self.drive.folder(DRIVE_ROOT_FOLDER_NAME) 
        if not self.root:
            die("Can't find the root folder!")

        # Load DRIVE_CONFIG_FILE_NAME from DRIVE_ROOT_FOLDER_NAME and store as "config:api" & self.config
        self.configure()

        # Site previews to be generated last
        self.preview_queue = {}

        # Download assets
        if self.options.assets:
            self.download_assets(force_conversion=True)

        # Generate fresh site previews
        elif self.options.regenerate_previews:
            all_content = [d.doc for d in self.db.view('_all_docs', include_docs=True) if 'document_id' in d.doc]
            for content in all_content:
                self.enqueue_previews_and_update_rwes(content)
            self.generate_previews()
            self.db_save(all_content)

        # Test fuzzy matcher against existing content
        elif self.options.test_match:
            all_slugs = [d.doc['slug'] for d in self.db.view('_all_docs', include_docs=True)]
            match = self.find_fuzzy(self.options.test_match, all_slugs, 90)
            if match:
                log('fuzzy: Found match "{}" for string "{}"'.format(match, self.options.test_match))
            else:
                log('fuzzy: No match for string "{}"'.format(self.options.test_match))

        # Watch for changes
        elif self.options.watch_docs:
            self.watch()

        # Stop watching for changes
        elif self.options.stop_watching:
            self.unwatch()

        # Delete the database
        elif self.options.delete_db:
            confirm = input(f'Delete the database "{DB_NAME}" [y/N]? ')
            if confirm.lower() == 'y':
                self.unwatch()
                del self.couch[DB_NAME]

        # Load content
        else:
            #TODO: handle document renaming/deletion

            if self.options.local:
                with script_directory():
                    try:
                        with open(DRIVE_CACHE_FILE_NAME) as f:
                            log('local: loading local cache of drive content')
                            published_documents = json.load(f, object_hook=driveclient_document_json_decoder)
                    except FileNotFoundError:
                        die(f"local: can't find local cache {DRIVE_CACHE_FILE_NAME}")

            else:
                # Identify published documents by their filenames and fetch new content
                published = re.compile(self.config['published-filename-regex']).search
                published_documents = [d for d in self.get_documents() if published(d.title) or d.id in self.options.ids]

                # Eagerly download in multiple threads (segfaults!)
                # with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                #     published_documents = executor.map(
                #         lambda d: PhonyDriveFileWithText(d.client, {**d.attributes, '__text': d.text}), published_documents)

                if self.options.save_local:
                    with script_directory():
                        with open(DRIVE_CACHE_FILE_NAME, 'w') as f:
                            json.dump(published_documents, f, indent=2, default=driveclient_document_json_encoder)
                            log('local: saved local cache of drive content', fatal=True)

            if not published_documents:
                warn('skip: no documents to load', fatal=True)
            new_content = filter(None, map(self.extract_and_transform, published_documents))

            # A full reload is triggered when no ids or changes are specified
            full_reload = not (self.options.ids or self.options.changes)
            if not full_reload:
                log('db: preserving existing content')
                existing_content = {d.doc['document_id']: d.doc for d in
                    self.db.view('_all_docs', include_docs=True) if 'document_id' in d.doc}
            else:
                log('db: not preserving existing content')
                existing_content = {}

            # Merge new content with existing, preserving revision number and translations
            for content in new_content:
                existing = existing_content.get(content['document_id'])
                if existing and '_rev' in existing: 
                    content['_rev'] = existing['_rev']
                    content['translations'] = existing.get('translations', {})
                existing_content[content['document_id']] = content

            all_content = list(existing_content.values())
            all_content = self.add_language_tags(all_content)

            all_content = self.pre_filters(all_content)

            all_content = self.merge_translations(all_content)
            all_content = self.fix_relationships(all_content)

            all_content = self.post_filters(all_content)

            self.download_assets()
            self.generate_previews()

            if full_reload:
                # TODO: Improve this heuristic so that casual development
                #       doesn't lead to webhook registration for the
                #       production server.
                production = not (DEBUG or DEVELOP or self.options.local)
                if production:
                    self.unwatch()
                log(f'db: replacing database "{DB_NAME}"')
                del self.couch[DB_NAME]
                self.db_get_or_create()
                self.configure()
                if production:
                    self.watch()

            self.db_save(all_content)


    def pre_filters(self, all_content):
        '''
        Project-specific filtering 
        '''
        log('filters: preprocessing unmerged docs')

        # There are about 12 more dashes in unicode, but we'll support these
        # five for key-whatever modules and call it a day. This regex handles
        # incorrect spacing around the hyphens, Arabic hyphens and more!
        key_pattern = r'(?P<module>.+?)(?:{}[][)(]*[-—–―ـ]\s+|\s+[-—–―ـ]\s+)(?P<description>.+)'
        key_finder = re.compile(key_pattern.format(ARABIC_BOUNDARY_REGEX), re.DOTALL).findall

        language_default = self.config['language-default']

        module_types = [t['one'] for t in self.config['types-tool']]
        module_types_plural = [t['many'] for t in self.config['types-tool']]

        for content in all_content:
            if NEW in content:
                # Flag person docs as having an email address
                if content['type'] == 'person':
                    content['email-available'] = bool(content.get('emails'))

                # Add a module-type
                if content['type'] in module_types:
                    content['module-type'] = 'full'
                    if re.search('SNAPSHOT', content['document_title']):
                        content['module-type'] = 'snapshot'
                    elif re.search('GALLERY', content['document_title']):
                        content['module-type'] = 'gallery'

                # Clean up learn-more section
                content['learn-more'] = [L for L in content.get('learn-more', [])
                    if L.get('title') and L.get('link') and L.get('title') != 'abc' and L.get('link') != 'url']
                if not content['learn-more']:
                    del content['learn-more']

                # Clean up real-world-examples section
                content['real-world-examples'] = [e for e in content.get('real-world-examples', [])
                                                  if all(map(e.get, ['title','link','description']))]
                self.enqueue_previews_and_update_rwes(content)
                if not content['real-world-examples']:
                    del content['real-world-examples']

                # Clean up some snapshots with example write ups
                full_write_up = content.get('full-write-up')
                if full_write_up and re.search(r'In a page \(500 words\) or less', full_write_up):
                    del content['full-write-up']

                # Clean up some modules with example tags
                tags = content.get('tags')
                if tags:
                    if len(tags) == 3 and all(t.lower() in ['corruption', 'mining', 'gender & sexuality'] for t in tags):
                        del content['tags']
                    # Slugify tags (note that they're not checked for existence)
                    content['tags'] = [slugify(t) for t in tags]

                # Simplify the key-stuff (more processing is done in post_filters)
                content['key-modules'] = {}
                for module_type in module_types_plural:
                    key_name = 'key-' + module_type
                    if key_name in content:
                        content['key-modules'][key_name] = [result[0] for result in (key_finder(k) for k in content[key_name]) if result]
                        del content[key_name]
                if not content['key-modules']:
                    del content['key-modules']

        return all_content


    def post_filters(self, all_content):
        '''
        As a final step, iterate through all modules, patch up module links
        and add bylines for simplicity
        '''
        log('filters: postprocessing merged docs')

        language_all = self.config['language-all']
        language_default = self.config['language-default']

        # Produce byline field for each language
        list_formatters = {lang: ListFormatter.createInstance(Locale(lang)).format
            for lang in language_all}

        people_by_slug = {c['slug']: c for c in all_content if c['type'] == 'person'}
        for content in all_content:
            people_content = [people_by_slug[a] for a in content.get('authors', [])]
            if people_content:
                titles_by_lang = {lang: [p['translations'].get(lang, {}).get('title', p['title']) for p in people_content]
                                  for lang in language_all}
                for lang, titles in titles_by_lang.items():
                    byline = list_formatters[lang](titles)
                    if lang == language_default:
                        content['byline'] = byline
                    elif lang in content['translations']:
                        content['translations'][lang]['byline'] = byline

        # This regex isn't pefect, but should work for 99% of our cases. The
        # problem relates to detecting nested parens without a proper parser.
        # This solution just swallows any ending with an extra close paren.
        # Since the link text is fuzzy matched anyway, we don't need to reliably
        # capture the full link text. However, if any module names end up with
        # parens in their middles "Like (such as) this", this method will fail.
        xref_matcher = re.compile(r'(?<!!)\[([^\]]*)\]\(((?!http)[^)]+)\)(?:\s*\))?').search
        xref_format_strings = {
            **{lang: '(see: [{type}: {title}](/tool/{slug})' for lang in language_all},
            **{'link': '[{title}](/tool/{slug})'},
            **self.config.get('xref-format-strings', {}),
        }
        markdown_fields = self.config['markdown']

        # Get type names for each language (currently in the config as lang-named keys within types-tool)
        types = {lang: {T['one']: T.get(lang, T['one']) for T in self.config['types-tool']}
                 for lang in language_all}

        def patch_links(text):
            m, chunks = xref_matcher(text), []
            while m:
                link_text, module_name, _, end = *m.groups(), *m.span()
                # TODO: ensure this gets the right language, even on a fresh load
                content = self.find_content(module_name, all_content, thresh=90)
                # Module exists
                if content:
                    if link_text:
                        replacement = xref_format_strings['link'].format(title=link_text, slug=content['slug'])
                    else:
                        if language == language_default:
                            link_text = nest_parens(content['title'], 1)
                        else:
                            try:
                                link_text = nest_parens(content['translations'][language]['title'], 1)
                            # Linking to a non-existent translation? Yikes. Insert the default language name.
                            except KeyError:
                                link_text = nest_parens(content['title'], 1)
                        type_name = types[language][content['type']].upper()
                        replacement = xref_format_strings[language].format(type=type_name, title=link_text, slug=content['slug'])
                    chunks.append(re.sub(re.escape(m.group()), replacement, text[:end]))
                # No module, but there's link text
                elif link_text:
                    chunks.append(re.sub(re.escape(m.group()), link_text, text[:end]))
                # No module, so remove markdown and leading spaces
                else:
                    chunks.append(re.sub(r'\s*' + re.escape(m.group()), '', text[:end]))
                text = text[end:]
                m = xref_matcher(text)
            return ''.join(chunks) + text

        # Recursive visitor reaches all deeply nested strings
        visit_all = lambda x: {
            list:   lambda L: [visit_all(i) for i in L],
            tuple:  lambda t: [visit_all(i) for i in t],
            dict:   lambda d: {k: visit_all(v) for k,v in d.items()},
            str:    lambda s: patch_links(s),
            int:    lambda i: i,
        }[type(x)](x)


        # Create a mapping of titles to slugs
        slugs_by_title = {}
        for content in all_content:
            slugs_by_title[content['title']] = content['slug']
            for c in content['translations'].values():
                if 'title' in c:
                    slugs_by_title[c['title']] = content['slug']
        titles = slugs_by_title.keys()

        # This final pass through all nested content patches up xrefs and key-modules
        tool_by_slug = {c['slug']: c for c in all_content}

        for content in all_content:
            language = content['lang']
            # Add slugs to key-modules
            if 'key-modules' in content:
                for key_group in content['key-modules'].values():
                    for i, k in enumerate(key_group):
                        key_group[i] = list(k[:2]) + [slugs_by_title.get(self.find_fuzzy(k[0], titles, thresh=90), '')]
            # Process xref links in markdown fields
            if NEW in content and language in language_all:
                for field in self.config['markdown']:
                    if content.get(field):
                        content[field] = visit_all(content.get(field))
            # This should be the last time the NEW marker is needed
            content.pop(NEW, None)

            for language, c in content['translations'].items():
                # Add slugs to nested key-modules
                if 'key-modules' in c:
                    for key_group in c['key-modules'].values():
                        for i, k in enumerate(key_group):
                            # Replace english key module title with translated title if possible
                            slug = slugs_by_title.get(self.find_fuzzy(k[0], titles, thresh=90), '')
                            if slug:
                                key_group[i] = [tool_by_slug[slug]['translations'].get(language, {'title': k[0]})['title'], k[1], slug]
                # Process xref links in markdown fields
                if NEW in c and language in language_all:
                    for field in self.config['markdown']:
                        if c.get(field):
                            c[field] = visit_all(c.get(field))
                # This should be the last time the NEW marker is needed
                # NOTE these remain in the translated pieces unless removed here
                c.pop(NEW, None)

        return all_content


    def find_fuzzy(self, title, title_list, thresh=50):
        '''
        General-purpose fuzzy matcher
        '''
        match = extractOne(title, title_list)
        if match and match[1] >= thresh:
            return match[0]


    def find_content(self, item_name, item_list, thresh=50, fuzzy_match_cache={}, rename_cache=set()):
        '''
        Use fuzzy matching to find a content item from a list
        This should always return a dict

        XXX: If item_list has changed since the last time it was cached, this function
             can return an item which is no longer in item_list. A source of strange bugs.
        '''
        if not isinstance(item_name, str):
            return {}

        cached = fuzzy_match_cache.setdefault((id(item_list), len(item_list)), {}).get(item_name)
        if cached:
            return cached

        # First determine whether the item_name refers to a module which has been renamed
        renamed = self.config['renamed-modules']
        match = extractOne(item_name, renamed.keys())
        if match and match[1] >= 90:
            if item_name not in rename_cache:
                rename_cache.add(item_name)
                log(f'renamed: reference changed from "{item_name}" to "{renamed[match[0]]}"')
            item_name = renamed[match[0]]

        # Perform the actual match
        match = extractOne({'title': item_name}, item_list, processor=lambda i: i.get('title', ''))
        if match and match[1] >= thresh:
            fuzzy_match_cache[id(item_list), len(item_list)][item_name] = match[0]
            return match[0]
        return {}


    def db_get_or_create(self):
        '''
        Get the database, creating if necessary
        '''
        self.db = self.couch[DB_NAME] if DB_NAME in self.couch else self.couch.create(DB_NAME)
        return self.db


    def db_save(self, doc_or_docs):
        '''
        Write one or many dicts (docs) to couchdb
        '''
        if not doc_or_docs: return
        # Handle one or many docs
        docs = [doc_or_docs] if isinstance(doc_or_docs, dict) else doc_or_docs

        log(f'db: storing {len(docs)} doc(s)')
        # Remove couch-disallowed keys and add _id where needed
        docs = [{k:v for k,v in d.items() if k in ('_id', '_rev') or not k.startswith('_')} for d in docs]
        [d.update(_id='{type}:{slug}'.format(**d)) for d in docs if '_id' not in d]
        # Simple conflict resolution (WARNING: this won't work with replication!)
        for success,id,rev_or_exc in self.db.update(docs):
            if isinstance(rev_or_exc, couchdb.http.ResourceConflict):
                retry = [d for d in docs if d['_id'] == id][0]
                retry.update(_rev=self.db[id]['_rev'])
                self.db.save(retry)


    def configure(self):
        '''
        Fetch, parse, set defaults, and store the config
        '''
        # Cap couchdb revision limit since documents are so frequently updated
        requests.put(urljoin(DB_SERVER, DB_NAME+'/_revs_limit'), data='50').status_code

        # Load configuration document and set defaults
        document = self.root.file(DRIVE_CONFIG_FILE_NAME)
        if not document:
            die("Can't find a config file!")
        self.config = c = parse_archieml(document.text)

        # Language settings
        c.setdefault('language-default', 'en')
        c.setdefault('language-all', ['en'])
        c.setdefault('language-omit', [])
        c.setdefault('language-detection-weighted-keys', [])
        # How we distinguish published content
        c.setdefault('published-filename-regex', r'\bDONE\b')
        # Ignore folders
        c.setdefault('ignore-folder-regex', r'^$')
        # Renaming synonymous keys, including those with language-suffixes
        c.setdefault('synonyms', {})
        # Manage single keys which contain lists
        c.setdefault('plural-separator-regex', r'(?:\s*,|\s+and|\s+&)\s+')
        c.setdefault('plural-keys', {})
        # Fields which should be parsed with markdown parser
        c.setdefault('markdown', [])
        # Fields which should be indexed by client search engines
        c.setdefault('search', [])
        # Download top-level assets from this top-level folder to this (relative) local path
        c.setdefault('asset-sources', ['ASSETS'])
        c.setdefault('asset-path', '/assets/content')
        c.setdefault('asset-manipulation', {})
        # Content type information
        c['types'] = []
        for key,value in c.items():
            if key.startswith('types-'):
                c['types'] += value
        c['plural-name-for-type'] = {T['one']: T['many'] for T in c['types']}
        c['singular-name-for-type'] = {T['many']: T['one'] for T in c['types']}

        # Relationships between content items are specified as pairs of one-way fields and groups of pairs of two-way fields
        c['relationships'] = {'forward': {}, 'backward': []}
        for key,value in c.items():
            if key.startswith('one-way') or key.startswith('two-way'):
                c['relationships']['forward'].update(value)
            if key.startswith('two-way'):
                c['relationships']['backward'].append(value)
        # Document renaming
        c['renamed-modules'] = {d['old']: d['new'] for d in c.get('renamed-modules', [])}

        # Save the config before creating lots of temporary language-related data within it
        c.update(type='config', slug='api')
        log(f'load: configuration options from drive document "{DRIVE_CONFIG_FILE_NAME}"')
        self.db_save(c)

        # Key transformations have to take into account language suffixes, so this adds suffixed copies
        # of synonyms and plural-keys
        add_language_suffixes = lambda D: [D.update(each) for each in
            [{k+'-'+lang: [i+'-'+lang for i in v] if isinstance(v, list) else
                          v+'-'+lang for k,v in D.items()} for lang in self.config['language-all']] ]
        add_language_suffixes(c['synonyms'])
        add_language_suffixes(c['plural-keys'])


    def watch(self):
        '''
        Request push notifications for entire drive be sent to the API_NOTIFICATION_PATH
        '''
        self.unwatch()
        url = urljoin(API_SERVER, API_NOTIFICATION_PATH)
        now = datetime.utcnow()
        expiration = int((60*60*24 + now.timestamp()) * 1000) # UTC + 24h in ms
        self.drive.execute(self.drive.service.changes().watch(body={
            'id': f'{DRIVE_CLIENT_NAME}-{expiration}',
            'type': 'web_hook',
            'address': url,
            'token': API_NOTIFICATION_TOKEN,
            'expiration': expiration,
        }))
        log(f"watch: for push notifications at {url}")
    
    
    def unwatch(self):
        '''
        Request all push notification channels in db be cancelled
        '''
        if 'config:notification-channels' in self.db:
            for channel, resource in self.db['config:notification-channels'].items():
                if channel.startswith(DRIVE_CLIENT_NAME):
                    try:
                        self.drive.execute(self.drive.service.channels().stop(body={
                            'id': channel, 
                            'resourceId': resource,
                        }))
                        warn(f"stop: Channel-Id: {channel} Resource-Id: {resource}")
                    except Exception as e:
                        warn(f"unwatch: {e}")
            del self.db['config:notification-channels']


    def get_documents(self):
        '''
        Get documents by file ids, change ids or all documents
        '''
        documents = []
        # Get only the specifically requested documents by id or change id
        if self.options.ids or self.options.changes:
            documents.extend(d for d in (self.drive.get(id) for id in self.options.ids) if d)
            documents.extend(d for d in (self.drive.get_change(id) for id in self.options.changes) if d)
        # Get all documents
        else:
            # Recursive folder getter requires python3.3+ for "yield from"
            def get_folders(root):
                for folder in root.folders:
                    if re.search(self.config['ignore-folder-regex'], folder.title):
                        log(f'omit: by ignore-folder-regex "{folder.title}"')
                        continue
                    yield folder
                    yield from get_folders(folder)
            for folder in get_folders(self.root):
                documents.extend(folder.documents)
                log(f'find: content in drive folder "{folder.title}"')
        return documents


    def extract_and_transform(self, document):
        '''
        Process a document and return a content item.
        '''
        content = parse_archieml(document.text)
        content[NEW] = True

        # Rename synonymous keys (this should happen before all other transformations)
        for old_key,new_key in self.config['synonyms'].items():
            old_value = content.get(old_key)
            if old_value is not None:
                content[new_key] = old_value
                del content[old_key]

        # Determine the type
        type = next((T for T in self.config['types'] if T['one'] in content), {}).get('one', '')
        content['type'] = type
        content['title'] = title = content.get(type)
        if not isinstance(title, str): 
            warn(f"skip: {document.id} bad type information")
            return

        # Add a few useful bits
        content['slug'] = slugify(content.get('title', ''), allow='')
        content['document_id'] = document.id
        content['document_link'] = document.alternateLink
        content['document_title'] = document.title
        try:
            dt = parser.parse(content['date'])
        except: # Easier to ask forgiveness...
            dt = parser.parse(document.modifiedDate)
        content['timestamp'] = int(1000 * dt.timestamp())

        # Convert singular keys to plural keys and split them up as lists
        for plural_key,singular_key in self.config['plural-keys'].items():
            single, plural = content.get(singular_key), content.get(plural_key)
            if single:
                content[plural_key] = [single]
                if plural_key != singular_key:
                    del content[singular_key]
            if plural and not isinstance(plural, list):
                multiline = re.split(r'\s*\n\s*\n\s*', plural)
                content[plural_key] = (multiline if len(multiline) > 1 else 
                                       re.split(self.config['plural-separator-regex'], plural))

        log(f"extract: {document.id} ({type}: {content['title']})")
        return content


    def add_language_tags(self, all_content):
        '''
        Detect the language of each content item and add a language tag

        A document can specify its language with a lang: value. Otherwise it will be
        determined from a corpus of values whose keys specify no language suffix, 
        favoring more heavily those keys specified with the configuration item
        called language-detection-weighted-keys.
        '''

        # Get the set of possible suffixes to weed out text irrelevant for detection
        language_suffixes = {f'-{lang}' for lang in self.config['language-all']}

        language_default = self.config['language-default']
        weighted_keys = {*self.config['language-detection-weighted-keys']}
        omitted_keys = {'_id', '_rev', 'type', 'slug', 'timestamp', 'translations', 
                        'document_id', 'document_link', 'document_title'}

        # Matches http/s, emails and 3-character-suffixed filenames
        an_obvious_computer_thing = re.compile(r'(http|[^\s]+(\.[a-z]{3}|@[^\s]+)$)').match
        # This recursive function concatenates text from nested structures
        r_concat = lambda x: {
            list:   lambda L: '\n'.join(map(r_concat, L)),
            dict:   lambda d: '\n'.join(map(r_concat, d.values())),
            str:    lambda s: '' if an_obvious_computer_thing(s) else s
        }.get(type(x), str)(x)

        for content in all_content:
            if 'lang' not in content:
                text_items = {k: r_concat(v) for k,v in content.items()
                              if k[-3:] not in language_suffixes and k not in omitted_keys}

                corpus = ' '.join(text_items.values()).replace('\n', ' ')
                corpus_weighted = ' '.join(v for k,v in text_items.items() 
                                           if k in weighted_keys).replace('\n', ' ')

                guess = ftlangdetect.detect(corpus)
                content['lang'] = guess['lang']

                if len(corpus_weighted) > 20:
                    guess_weighted = ftlangdetect.detect(corpus_weighted)
                    content['lang'] = max(guess, guess_weighted, key=lambda g: g['score'])['lang']

                log(f"""language: guessed {content['lang']} for "{content['title']}" """)

        return all_content


    def merge_translations(self, all_content):
        '''
        Assuming all necessary documents have already been fetched, merge translations
        into the content object for the default language. They will be placed into a 
        'translations' dictionary under two-letter language code keys.
        '''
        language_all = self.config['language-all']
        language_default = self.config['language-default']
        language_other = set(language_all) - set(language_default)
        #language_omit = self.config['language-omit']
        content_primary = []
        content_primary_by_type = {}
        content_translated = []

        # Sort content by language
        for content in all_content:
            if content['lang'] == language_default:
                content_primary.append(content)
                content_primary_by_type.setdefault(content['type'], []).append(content)
            else:
                content_translated.append(content)

        # Add translated content to a translations dict in each default language piece
        for translation in content_translated:
            default = self.find_content(translation.get('default-language-content', ''), content_primary_by_type[translation['type']], thresh=90)
            if not default:
                warn(f"skip: {translation.get('title')} can't find default language version {translation.get('default-language-content')}")
                continue
            translations = default.setdefault('translations', {})
            translations[translation['lang']] = translation
            log(f"merge: {translation['title']} ({translation['lang']}) => {default['title']}")

        # Recursive in-place dictionary merging function to be used on COPIES of destination dicts
        def merge_dicts(dest, src):
            for k, v in src.items():
                if isinstance(dest.get(k), dict) and isinstance(v, dict):
                    merge_dicts(dest[k], v)
                else:
                    dest[k] = v

        # Look through primary language documents and integrate keys with language code suffixes (-es, -fr)
        for content in content_primary:
            content.setdefault('translations', {})
            # Merge the default language first, simply replacing objects without merging subkeys
            default_language_keys = [k for k in content if isinstance(k, str) and k.endswith('-' + language_default)]
            content.update({k[:-3]: content[k] for k in default_language_keys if content[k]})
            [content.pop(k) for k in default_language_keys]
            # Merge the remaining languages into the default language
            for lang in language_other:
                # Get inline translations to be merged and remove them from the content object
                language_keys = [k for k in content if isinstance(k, str) and k.endswith('-' + lang)]
                language_new = {k[:-3]: content[k] for k in language_keys if content[k]}
                [content.pop(k) for k in language_keys]
                if language_new:
                    # Get any existing translations and update the simple keys
                    language_dict = content['translations'].setdefault(lang, {})
                    language_dict.update(language_new)
                    # Copy dicts from the original content, merge translations into them, then update the existing translations
                    default_language_dicts_to_merge_into = {k: deepcopy(content[k]) for k,v in language_new.items()
                                                            if isinstance(content.get(k), dict)}
                    [merge_dicts(v, language_new[k]) for k,v in default_language_dicts_to_merge_into.items()]
                    language_dict.update(default_language_dicts_to_merge_into)

        # TODO: language-omit (currently performed by API server)

        # All content is now in this merged list
        return content_primary


    def fix_relationships(self, all_content):
        '''
        Replace relationships based on document titles with fuzzy-matched slugs
        '''
        typed_content = {}
        typed_slugged_content = {}
        for content in all_content:
            typed_content.setdefault(content['type'], []).append(content)
            typed_slugged_content.setdefault(content['type'], {})[content['slug']] = content

        # Forward relationships are specified with a mapping of fields to types. This is a time-consuming
        # but important process. Each entry is written by hand and so must be fuzzy matched for spelling
        # errors and non-existent related documents.
        for field, T in self.config['relationships']['forward'].items():
            possibly_related_docs = all_content if T == 'any' else typed_content.get(T, [])
            for content in all_content:
                related_titles = content.get(field)
                if related_titles is not None:
                    if isinstance(related_titles, list):
                        related_docs = (self.find_content(t, possibly_related_docs, 90) for t in related_titles)
                        # Ignore leading hyphens when sorting (Is this sort redundant? Prove it before removing!)
                        content[field] = sorted((c['slug'] for c in related_docs if c), key=lambda s: s.lstrip('-'))
                    elif isinstance(related_titles, str):
                        content[field] = self.find_content(related_titles, possibly_related_docs, 90).get('slug')
                    if not content[field]:
                        del content[field]

        # Backward relationships ensure that groups of interrelated content are linked in both directions,
        # even when related content is only specified going one way. Each group of backward relationships 
        # specifies two things:
        #   1. The fields containing forward relationships so that forward related docs can be found
        #   2. A mapping of types to the fields which, in those related docs, need to be related back
        backward_groups = [(g, {v: k for k, v in g.items()}) for g in self.config['relationships']['backward']]
        for field_to_type_map, type_to_field_map in backward_groups:
            for content in all_content:

                # Get the field which, in other docs, should relate back to this one
                # For example if this is a story, other docs relate to it with stories
                related_name = type_to_field_map.get(content['type'])
                if not related_name:
                    continue # Nothing to do, proceed to next doc

                # Accumulate all forward-related docs by their slug and type, using the
                # fields in the field_to_type_map to figure out which fields in this
                # doc will contain their slugs.
                related_docs = []
                for field, T in field_to_type_map.items():
                    related_slugs = content.get(field)
                    if not related_slugs:
                        continue # Proceed to next field

                    if isinstance(related_slugs, str):
                        related_slugs = [related_slugs]
                    if isinstance(related_slugs, list):
                        related_slugs = [*filter(None, (typed_slugged_content.get(T, {}).get(s) for s in related_slugs))]
                    related_docs.extend(related_slugs)

                # Populate each forward-related doc's appropriate related_name with this doc's
                # slug. This can be done without checking the destination's validity because
                # they will have already been checked and slugified.
                slug = content['slug']
                for doc in related_docs:
                    backward_field = doc.get(related_name)
                    if isinstance(backward_field, str):
                        doc[related_name] = slug
                    elif backward_field is None:
                        doc[related_name] = [slug]
                    elif isinstance(backward_field, list):
                        # Ignore leading hyphens when sorting
                        doc[related_name] = sorted({slug} | set(backward_field), key=lambda s: s.lstrip('-'))

        return all_content


    def generate_previews(self):
        '''
        Launch external preview generation tool
        '''
        if not self.options.no_previews:
            log(f'siteprev: generating for {len(self.preview_queue)} url(s)')
            venv_run('sitepreview', json.dumps(self.preview_queue))
        else:
            warn(f'siteprev: not generating for {len(self.preview_queue)} url(s)')


    def enqueue_previews_and_update_rwes(self, content):
        '''
        Gather RWE urls and update RWEs in-place
        '''
        # TODO: There are better ways to make a relative path
        asset_path_rel = self.config['asset-path'].lstrip('/')

        for e in content.get('real-world-examples', []):
            if 'image' not in e or re.match('rwe_[a-f0-9]{32}_', e['image']):
                hash = md5(e['link'].encode()).hexdigest()
                slug = slugify(e['title'])
                # Note: updating the RWE in-place requires saving to db
                e['image'] = filename = f'rwe_{hash}_{slug}.jpg'
                self.preview_queue[e['link']] = os.path.join(asset_path_rel, filename)


    def download_assets(self, force_conversion=False):
        '''
        Download all top-level assets from top-level folders specified in config['asset-sources']
        '''
        # In case somebody uses this feature to write to a production server :(
        clean_path = lambda p: os.path.normpath(p.replace('\0','').replace('..','').strip('/'))

        destination = clean_path(self.config['asset-path'])
        with script_subdirectory(destination):
            for source in self.config['asset-sources']:
                folder = self.root.folder(source)
                for file in folder.files:
                    convert = force_conversion
                    if file.save_as(file.title):
                        log(f'download: asset "{file.title}"')
                        convert = True
                    if convert and file.attributes['mimeType'] in ('image/gif', 'image/png', 'image/jpeg'):
                        log(f'convert: asset "{file.title}"')
                        for prefix,args in self.config['asset-manipulation'].items():
                            Popen(['convert', *shlex.split(args), file.title, f'{prefix}-{file.title}'])
