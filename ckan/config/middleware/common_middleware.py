# encoding: utf-8

"""Common middleware used by both Flask and Pylons app stacks."""

import urllib2
import hashlib
import json
import cgi
import re

import sqlalchemy as sa
from webob.request import FakeCGIBody
from ckan.common import config


class RootPathMiddleware(object):
    '''
    Prevents the SCRIPT_NAME server variable conflicting with the ckan.root_url
    config. The routes package uses the SCRIPT_NAME variable and appends to the
    path and ckan addes the root url causing a duplication of the root path.

    This is a middleware to ensure that even redirects use this logic.
    '''
    def __init__(self, app, config):
        self.app = app

    def __call__(self, environ, start_response):
        # Prevents the variable interfering with the root_path logic
        if 'SCRIPT_NAME' in environ:
            environ['SCRIPT_NAME'] = ''

        return self.app(environ, start_response)


class CloseWSGIInputMiddleware(object):
    '''
    webob.request.Request has habit to create FakeCGIBody. This leads(
    during file upload) to creating temporary files that are not closed.
    For long lived processes this means that for each upload you will
    spend the same amount of temporary space as size of uploaded
    file additionally, until server restart(this will automatically
    close temporary files).

    This middleware is supposed to close such files after each request.
    '''
    def __init__(self, app, config):
        self.app = app

    def __call__(self, environ, start_response):
        wsgi_input = environ['wsgi.input']
        if isinstance(wsgi_input, FakeCGIBody):
            for _, item in wsgi_input.vars.items():
                if not isinstance(item, cgi.FieldStorage):
                    continue
                fp = getattr(item, 'fp', None)
                if fp is not None:
                    fp.close()
        return self.app(environ, start_response)


class TrackingMiddleware(object):

    def __init__(self, app, config):
        self.app = app
        self.engine = sa.create_engine(config.get('sqlalchemy.url'))

    def __call__(self, environ, start_response):
        path = environ['PATH_INFO']
        method = environ.get('REQUEST_METHOD')
        if path == '/_tracking' and method == 'POST':
            # do the tracking
            # get the post data
            payload = environ['wsgi.input'].read()
            parts = payload.split('&')
            data = {}
            for part in parts:
                k, v = part.split('=')
                data[k] = urllib2.unquote(v).decode("utf8")

            # patch
            # If CKAN runs with a root_path there's a problem with the URLs stored in the tracks
            # The root_path should be removed from the stored URLs
            root_path = config.get('ckan.root_path', None)
            if root_path:
                # convert the root_path to a regular expression by replacing {{LANG}} by [^\/]+
                # Example: if the root_path is `/data-portal/ckan/{{LANG}}` we want to remove that
                # part from the URL, applying this regular expression to this string:
                #   reg = '/data-portal/ckan/[^\/]+'
                #
                # Appplying gsub with that regular expression the url is converted:
                #   - from: /data-portal/ckan/en/datasets/foo
                #   - to:   /datasets/foo
                reg = re.sub('{{LANG}}', '[^\/]+', root_path)
                data['url'] = re.sub(reg, '', data.get('url'))
            # /end patch

            start_response('200 OK', [('Content-Type', 'text/html')])
            # we want a unique anonomized key for each user so that we do
            # not count multiple clicks from the same user.
            key = ''.join([
                environ['HTTP_USER_AGENT'],
                environ['REMOTE_ADDR'],
                environ.get('HTTP_ACCEPT_LANGUAGE', ''),
                environ.get('HTTP_ACCEPT_ENCODING', ''),
            ])
            key = hashlib.md5(key).hexdigest()
            # store key/data here
            sql = '''INSERT INTO tracking_raw
                     (user_key, url, tracking_type)
                     VALUES (%s, %s, %s)'''
            self.engine.execute(sql, key, data.get('url'), data.get('type'))
            return []
        return self.app(environ, start_response)
