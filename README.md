# google-docs-etl

> An extract/transform/load layer to transform a collection of Google Docs containing ArchieML into a RESTful API

## Instructions:

Requires a local couchdb installation running on port 5984. **It will delete any database named "toolbox"!** _You have been warned!_ 

Requires python3. Virtualenv is also required for python3.2 (try `sudo apt-get install python3 python-virtualenv` on debian-based systems). 

You will also need a `client_secret.json` file and `google-docs-etl-????????????.json` file representing the private key of a service account with access to the google docs collection.

#### Python 3.2:

1. `git clone https://github.com/BeautifulTrouble/google-docs-etl.git`
2. `cd google-docs-etl`
3. `virtualenv -p $(which python3) venv`
4. `. ./activate`
5. `pip install -r requirements.txt`
6. `./contentloader.py`
7. `./apiadapter.py`

#### Python >= 3.3:

1. `git clone https://github.com/BeautifulTrouble/google-docs-etl.git`
2. `cd google-docs-etl`
5. `./contentloader.py`
6. `./apiadapter.py`

For python3.2, it is necessary to run `. ./activate` before `./contentloader.py` or `./apiadapter.py`. After an initial run of the contentloader, the apiadapter can be left running. It listens on port 6969. Contentloader must be re-run to reload content.

## TODO:

- [ ] Make API more configurable from config document or find API generator
- [ ] Add document hashes or timestamps and update rather than re-creating entire dataset
- [ ] Add listener process to receive notifications from drive api
- [ ] Image/asset management (lots of options here)
- [ ] Front-end (adapt proto-rising)
