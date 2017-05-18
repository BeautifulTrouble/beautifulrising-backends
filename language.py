#encoding: utf-8

'''
This module provides functionality necessary for language detection
'''


import langdetect


def add_language_tags(document_collection, language_default, weighted_keys):
    '''
    Detect the language of each document and add a language tag

    A document can specify its language with a lang: value. Otherwise it will be
    determined from a corpus of values whose keys have no language suffix, 
    favoring more heavily those keys specified with the configuration item
    called language-detection-weighted-keys.

    Language detection is difficult to perform correctly in an automatic way. 
    Some documents are inherently multilingual and others are inherently 
    independent of language. This method attempts to do an intelligent thing for
    most cases, but it could be smarter. If you find yourself questioning the 
    process herein, by all means improve it!
    '''

    # Get the set of possible suffixes to weed out text irrelevant for detection
    langdetect.detector_factory.init_factory()
    language_suffixes = {'-'+lang for lang in langdetect.detector_factory._factory.langlist}

    weighted_keys = set(weighted_keys)
    omitted_keys = {'_id', '_rev', 'type', 'slug', 'timestamp', 'translations', 
                    'document_id', 'document_link', 'document_title'}

    # Matches http/s, emails and 3-character-suffixed filenames
    an_obvious_computer_thing = re.compile(r'(http|[^\s]+(\.[a-z]{3}|@[^\s]+)$)').match
    # This recursive function concatenates text from nested structures
    r_concat = lambda x: {
        list:   lambda L: '\n'.join(map(r_concat, L)),
        dict:   lambda d: '\n'.join(map(r_concat, d.values())),
        str:    lambda s: '' if an_obvious_computer_thing(s) else s
    }.get(dict if isinstance(x, dict) else type(x), str)(x)

    for document in document_collection:
        if 'lang' not in document:
            text_items = {k: r_concat(v) for k,v in document.items()
                          if k[-3:] not in language_suffixes and k not in omitted_keys}
            
            # Create two identical language guesses (lists) as fallback for the case when detection
            # fails on either or both. Both are low confidence guesses of the default language, with
            # the guess being extremely low confidence when a translation-specific key is present.
            lang, lang_weighted = [[langdetect.language.Language(language_default,
                                    0.5 if 'default-language-content' in document else 0.7)]] * 2

            # A high confidence non-weighted guess will be squashed a little, or "normalized"
            corpus = '\n'.join(text_items.values())
            try:
                lang = langdetect.detect_langs(corpus)[:1]
                lang[0].prob = 1 / (1 + math.exp(-lang[0].prob))
                # ^ Rather than using this clumsy sigmoid function to squash high values, it might
                #   make more sense to compare the deviation between all guesses and the top two,
                #   then make a decision about the weighted guess based on this difference. Multi-
                #   lingal docs would have low deviation between the top two guesses when compared
                #   with deviation overall, and a weighted guess would break the tie.
            except langdetect.lang_detect_exception.LangDetectException: ...

            # A "weighted guess" (based on >20 chars of weighted keys) is left as-is
            corpus_weighted = '\n'.join(v for k,v in text_items.items() if k in weighted_keys)
            if len(corpus_weighted) > 20:
                try: 
                    lang_weighted = langdetect.detect_langs(corpus_weighted)
                except langdetect.lang_detect_exception.LangDetectException: ...

            # And the best guess wins.
            document['lang'] = max(lang + lang_weighted).lang

    return document_collection


def merge_translations(document_collection, language_default, language_all):
    '''
    '''
