API_ADMIN_TOKEN = "MADE UP TOKEN"
API_NOTIFICATION_EMAIL = "api-notify@example.com"
API_NOTIFICATION_PATH = "/notify"
API_NOTIFICATION_TOKEN = "MADE UP TOKEN"
API_PATH = "/api/v1"
API_SERVER = "https://api.example.com"

DB_NAME = "database"
DB_SERVER = "http://127.0.0.1:5984/"

DRIVE_CACHE_FILE_NAME = "local_cache.json"
DRIVE_CLIENT_NAME = "drive-client"
DRIVE_CONFIG_FILE_NAME = "CONFIG"
DRIVE_ROOT_FOLDER_NAME = "CONTENT"
DRIVE_SERVICE_ACCOUNT_JSON_FILENAME = "account.json"

GOOGLE_VERIFICATION = "googleabc123.html"

JOBS_PRE = []
JOBS_POST = []

# @@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@

import sys

DEBUG = "--debug" in sys.argv
DEVELOP = "--develop" in sys.argv

if DEVELOP:
    DB_NAME = DB_NAME + "_develop"
