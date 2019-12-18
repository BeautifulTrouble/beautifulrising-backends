
API_ADMIN_TOKEN = 'MADE UP TOKEN'
API_NOTIFICATION_EMAIL = 'api-notify@example.com'
API_NOTIFICATION_PATH = '/notify'
API_NOTIFICATION_TOKEN = 'MADE UP TOKEN'
API_PATH = '/api/v1'
API_SERVER = 'https://api.example.com'

DB_NAME = 'database'
DB_SERVER = 'http://127.0.0.1:5984/'

DRIVE_CACHE_FILE_NAME = 'local_cache.json'
DRIVE_CLIENT_NAME = 'drive-client'
DRIVE_CONFIG_FILE_NAME = 'CONFIG'
DRIVE_ROOT_FOLDER_NAME = 'CONTENT'
DRIVE_SERVICE_ACCOUNT_JSON_FILENAME = 'account.json'

FB_APP_ID = 'APP ID'

GOOGLE_VERIFICATION = 'googleabc123.html'

MAILGUN_API_KEY = 'TOKEN'

PUPPETEER_ARGS = '--no-sandbox'

RECAPTCHA_V2_SITE_KEY = 'KEY'
RECAPTCHA_V2_SITE_SECRET = 'SECRET'
RECAPTCHA_INVISIBLE_SITE_KEY = 'KEY'
RECAPTCHA_INVISIBLE_SITE_SECRET = 'SECRET'

#@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@

import sys
DEBUG = '--debug' in sys.argv
DEVELOP = '--develop' in sys.argv

if DEVELOP:
    DB_NAME = DB_NAME + '_develop'

