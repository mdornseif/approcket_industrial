# Approcket replication handler for AppEngine
# To be included in you app.yaml
#
# Copyright 2009 Kaspars Dancis, Kurt Daal
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import base64
import logging
import re

from google.appengine.api import datastore, datastore_types
from google.appengine.api import lib_config
from google.appengine.datastore import datastore_query
from google.appengine.ext.db import stats
from google.appengine.ext import webapp
from google.appengine.ext.webapp.util import run_wsgi_app

# Try to import additional libraries in `/lib/` subdirectory.
try:
    import lib
except ImportError:
    pass

from rocket import key
from rocket.common import *

CHANGE_THIS = "change_this"

# see http://code.google.com/appengine/docs/python/tools/appengineconfig.html
_config = lib_config.register('approcket', {'SECRET_KEY': key.SECRET_KEY,
                                            'READONLY': False})


class Rocket(webapp.RequestHandler):
    """Handels all replication requests."""
    def unauthorized(self, error=None):
        self.error(403)
        if error:
            logging.error(u"Unauthorized: %s" % error)
            self.response.out.write(u'<error>Unauthorized: %s</error>\n' % error)
        else:
            logging.error(u"Unauthorized")

    def bad_request(self, error):
        self.error(400)
        logging.error(u"Bad Request: %s" % error)
        self.response.out.write(u'<error>%s</error>\n' % error)

    def not_found(self, error):
        self.error(404)
        logging.error(u"Not Found: %s" % error)
        self.response.out.write(u'<error>%s</error>\n' % error)

    def server_error(self, error, exception=None):
        self.error(500)

        if exception != None:
            logging.exception(u"Server Error: %s" % error)
            self.response.out.write(u'<error>Server Error: %s\n%s</error>\n' % (error, exception))
        else:
            logging.error(u"Server Error: %s" % error)
            self.response.out.write(u'<error>Server Error: %s</error>\n' % error)

    def get(self, kind):
        self.response.headers['Content-Type'] = 'text/xml'

        if _config.SECRET_KEY == CHANGE_THIS:
            return self.unauthorized("Please change the default secret key in key.py")

        if self.request.get("secret_key") != _config.SECRET_KEY:
            return self.unauthorized()

        self.response.out.write(u'<?xml version="1.0" encoding="UTF-8"?>\n')
        self.response.out.write(u'<updates>\n')

        timestamp_field = self.request.get("timestamp", 'updated_at')
        batch_size = int(self.request.get("count", 100))

        if self.request.get("cursor"):
            cursor = self.request.get("cursor")
            cursor = datastore_query.Cursor.from_websafe_string(cursor)
            query = datastore.Query(kind, cursor=cursor)
        else:
            query = datastore.Query(kind)

        f = self.request.get("from")
        if f:
            query['%s >' % timestamp_field] = from_iso(f)
            # "Note that a sort order implies an existence filter! In other
            # words, Entities without the sort order property are filtered
            # out, and *not* included in the query results."
            query.Order(timestamp_field)
        entities = query.Get(batch_size)

        for entity in entities:
            parent = entity.key().parent()
            if not parent:
                parent = ''
            self.response.out.write(u'    <%s key="%s" datastorekey="%s" parent="%s">\n' % (kind, ae_to_rocket(TYPE_KEY, entity.key()), str(entity.key()), parent))
            for field, value in entity.items():
                if isinstance(value, list):
                    if len(value) > 0 and value[0] != None:
                        field_type = get_type(value[0])
                        self.response.out.write(u'        <%s type="%s" list="true">\n' % (field, field_type))
                        for item in value:
                            self.response.out.write(u"            <item>%s</item>\n" % ae_to_rocket(field_type, item))
                        self.response.out.write(u'</%s>\n' % field)
                else:
                    if value != None:
                        if field == timestamp_field:
                            field_type = TYPE_TIMESTAMP
                        else:
                            field_type = get_type(value)

                        self.response.out.write(u'        <%s type="%s">%s</%s>\n' % (field, field_type, ae_to_rocket(field_type, value), field))

            self.response.out.write(u'    </%s>\n' % kind)

        self.response.out.write(u'     <_cursor type="str">%s</_cursor>' % str(query.GetCursor().to_websafe_string()))
        self.response.out.write(u'</updates>')


def get_type(value):
    if isinstance(value, datetime):
        return TYPE_DATETIME
    elif isinstance(value, bool):
        return TYPE_BOOL
    elif isinstance(value, long):
        return TYPE_LONG
    elif isinstance(value, float):
        return TYPE_FLOAT
    elif isinstance(value, int):
        return TYPE_INT
    elif isinstance(value, datastore_types.Text):
        return TYPE_TEXT
    elif isinstance(value, datastore_types.Key):
        return TYPE_REFERENCE
    elif isinstance(value, datastore_types.Blob):
        return TYPE_BLOB
    else:
        return TYPE_STR

    return None


def ae_to_rocket(field_type, ae_value):
    if ae_value == None:
        rocket_value = ""
    elif field_type == TYPE_DATETIME or field_type == TYPE_TIMESTAMP:
        rocket_value = to_iso(ae_value)
    elif field_type == TYPE_REFERENCE:
        rocket_value = "%s/%s" % (ae_value.kind(), ae_to_rocket(TYPE_KEY, ae_value))
    elif field_type == TYPE_KEY:
        if ae_value.name():
            rocket_value = escape(ae_value.name())
        else:
            rocket_value = "%d" % ae_value.id()
    elif field_type == TYPE_BOOL:
        rocket_value = "%d" % ae_value
    elif field_type == TYPE_BLOB:
        rocket_value = base64.b64encode(ae_value)
    else:
        rocket_value = escape(u"%s" % ae_value)

    # certain chars < 0x20 are illegal in XML.
    # http://www.w3.org/TR/REC-xml/#charsets
    return re.sub('[\x01\x02\x03\x04\x05\x06\x07\x08\x0b\x0c\x0e\x0f]+',
                  '',
                  rocket_value)


class RocketModelList(Rocket):
    """Get a List of all Models in this Application"""

    def get(self):
        if self.request.get("secret_key") != _config.SECRET_KEY:
            return self.unauthorized()

        self.response.headers['Content-Type'] = 'text/plain'

        for kind in stats.KindStat.all().run():
            if not kind.kind_name.startswith('_'):
                self.response.out.write('%s\n' % kind.kind_name)


def escape(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace(
                        '>', '&gt;').replace('"', '&quot;').replace(
                        "'", '&#39;')


def to_iso(dt):
    return dt.isoformat()


application = webapp.WSGIApplication(
    [('/rocket/_modellist.txt', RocketModelList),
     ('/rocket/(.*)', Rocket)]
    )


def main():
    run_wsgi_app(application)


if __name__ == "__main__":
    main()
