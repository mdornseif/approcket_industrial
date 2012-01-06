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
import urllib
from datetime import timedelta
from xml.etree import ElementTree
from xml.parsers.expat import ExpatError

import MySQLdb as db

from daemon import Service
from common import *
from key import SECRET_KEY


import_config = "from config import *"

for arg in sys.argv[1:]:
    if arg.startswith("-c="):
        if arg.endswith(".py"):
            config = arg[3:len(arg) - 3]
        else:
            config = arg[3:]

        import_config = "from %s import *" % config

exec import_config


class Table:
    def __init__(self, name, timestamp_field, key_field):
        self.name = name
        self.timestamp_field = timestamp_field
        self.key_field = key_field

        self.fields = {}
        self.fields[key_field] = TYPE_KEY
        self.fields[timestamp_field] = TYPE_TIMESTAMP

        self.list_fields = {}


class ReplicationService(Service):
    def on_start(self):
        self.can_process = False

        if KIND in self.config:
            self.kind = self.config[KIND]
        else:
            logging.error("Replication service is not configured properly - KIND parameter missing %s", self.config)
            return

        self.table_name = self.config.get(TABLE_NAME, self.kind.lower())
        self.timestamp_field = self.config.get(TIMESTAMP_FIELD, DEFAULT_TIMESTAMP_FIELD)
        self.table_key_field = self.config.get(TABLE_KEY_FIELD, DEFAULT_KEY_FIELD)
        self.send_fields = set(self.config.get(SEND_FIELDS, []))
        if not self.send_fields:
            self.send_fields = None
        self.send_fields_exclude = set(self.config.get(SEND_FIELDS_EXCLUDE, []))
        self.receive_fields = set(self.config.get(RECEIVE_FIELDS, []))
        if not self.receive_fields:
            self.receive_fields = None
        self.receive_fields_exclude = set(self.config.get(RECEIVE_FIELDS_EXCLUDE, []))
        self.embedded_list_fields = set(self.config.get(EMBEDDED_LIST_FIELDS, []))
        self.after_send = self.config.get('AFTER_SEND')
        self.mode = self.config.get('MODE', SEND_RECEIVE)

        kwargs = {
            'charset': 'utf8',
            'use_unicode': True,
        }

        if DATABASE_USER:
            kwargs['user'] = DATABASE_USER
        if DATABASE_NAME:
            kwargs['db'] = DATABASE_NAME
        if DATABASE_PASSWORD:
            kwargs['passwd'] = DATABASE_PASSWORD
        if DATABASE_HOST.startswith('/'):
            kwargs['unix_socket'] = DATABASE_HOST
        elif DATABASE_HOST:
            kwargs['host'] = DATABASE_HOST
        if DATABASE_PORT:
            kwargs['port'] = int(DATABASE_PORT)

        self.con = db.connect(**kwargs)

        cur = self.con.cursor()

        try:
            # retrieve table metadata if available

            cur.execute('SHOW tables LIKE "%s"' % self.table_name)
            if cur.fetchone():
                # table exist

                # start with empty definition
                self.table = Table(self.table_name, self.timestamp_field, self.table_key_field)

                # add table fields
                cur.execute('SHOW COLUMNS FROM %s' % self.table_name)
                for col in cur.fetchall():
                    field_name = col[0]
                    if field_name in self.embedded_list_fields:
                        self.table.fields[field_name] = TYPE_EMB_LIST
                    else:
                        field_type = self.normalize_type(field_name, col[1])
                        self.table.fields[field_name] = field_type

                # add list fields stored in separate self.tables (TableName_ListField)
                cur.execute('SHOW tables LIKE "%s_%%"' % self.table_name)
                for row in cur.fetchall():
                    list_table_name = row[0]
                    list_field_name = list_table_name[len(self.table_name) + 1:]
                    cur.execute('SHOW COLUMNS FROM %s' % list_table_name)
                    for col in cur.fetchall():
                        field_name = col[0]
                        if field_name == list_field_name:
                            field_type = self.normalize_type(field_name, col[1])
                            self.table.list_fields[field_name] = field_type
                            break

            else:
                # self.tables is missing
                cur.execute(
                    "CREATE TABLE %s (%s VARCHAR(255) NOT NULL, %s TIMESTAMP, PRIMARY KEY(%s), INDEX %s(%s)) ENGINE = %s CHARACTER SET utf8 COLLATE utf8_general_ci" % (
                        self.table_name,
                        self.table_key_field,
                        self.timestamp_field,
                        self.table_key_field,
                        self.timestamp_field,
                        self.timestamp_field,
                        DATABASE_ENGINE,
                    ))

                self.table = Table(self.table_name, self.timestamp_field, self.table_key_field)

            # reading existing replication state if available

            cur.execute('show tables like "rocket_station"')

            self.send_state = None
            self.receive_state = None

            if cur.fetchone():
                cur.execute("select send_state, receive_state from rocket_station where kind = '%s'" % self.kind)
                row = cur.fetchone()
                if row:
                    self.send_state = row[0]
                    self.receive_state = row[1]
            else:
                cur.execute("CREATE TABLE rocket_station (kind VARCHAR(255), send_state VARCHAR(500), receive_state VARCHAR(500), PRIMARY KEY (kind)) ENGINE = %s CHARACTER SET utf8 COLLATE utf8_general_ci" % DATABASE_ENGINE)

            self.con.commit()

        finally:
            cur.close()

        self.can_process = True

    def on_stop(self):
        self.con.close()

    def process(self):
        if not self.can_process:
            return False

        updates = False

        if self.mode == SEND_RECEIVE or self.mode == SEND:
            if self.send_updates(
                self.kind,
                self.table_name,
                self.timestamp_field,
                self.table_key_field,
                self.send_fields,
                self.send_fields_exclude,
                self.embedded_list_fields
                ):
                updates = True

        if self.mode == RECEIVE_SEND or self.mode == SEND_RECEIVE or self.mode == RECEIVE:
            if self.receive_updates(
                self.kind,
                self.table_name,
                self.timestamp_field,
                self.table_key_field,
                self.receive_fields,
                self.receive_fields_exclude,
                self.embedded_list_fields
                ):
                updates = True

        if self.mode == RECEIVE_SEND:
            if self.send_updates(
                self.kind,
                self.table_name,
                self.timestamp_field,
                self.table_key_field,
                self.send_fields,
                self.send_fields_exclude,
                self.embedded_list_fields
                ):
                updates = True

        return updates

    def send_updates(self, kind, table_name, timestamp_field, table_key_field, send_fields, send_fields_exclude, embedded_list_fields):
        cur = self.con.cursor()

        table = self.get_table_metadata(cur, table_name, timestamp_field, table_key_field, embedded_list_fields)

        if timestamp_field not in table.fields:
            logging.error(self.name + ' Error: table %s is missing timestamp field "%s"' % (table_name, timestamp_field))
            return

        if table_key_field not in table.fields:
            logging.error(self.name + ' Error: table %s is missing key field "%s"' % (table_name, table_key_field))
            return

        cur.execute("select current_timestamp")
        to_timestamp = cur.fetchone()[0] - timedelta(seconds=1)  # -1 second to ensure there will be no more updates with that timestamp
        params = [to_timestamp]

        sql = "select %s from %s where %s < " % (', '.join(["`" + k[0] + "`" for k in table.fields.items()]), table_name, timestamp_field) + """%s """

        if self.send_state:
            sql += "and " + timestamp_field + """ > %s """
            params.append(from_iso(self.send_state))
            logging.info(self.name + " Send %s: from %s" % (kind, self.send_state))
        else:
            logging.info(self.name + " Send %s: from beginning" % (kind))

        sql += "order by %s " % timestamp_field

        offset = 0
        count = BATCH_SIZE
        while count == BATCH_SIZE:
            count = 0
            batch_sql = sql + " limit %d, %d" % (offset, BATCH_SIZE)
            cur.execute(batch_sql, params)
            intermediate_timestamp = None
            for row in cur.fetchall():
                count += 1

                key = None

                entity = {
                }

                i = 0
                for field_name, field_type in table.fields.items():

                    field_value = row[i]

                    if field_name == timestamp_field and field_value:
                        intermediate_timestamp = field_value - timedelta(seconds=1)
                        # do not send time stamp to avoid send/receive loop
                        # entity["%s|%s" % (field_type, field_name)] = self.mysql_to_rocket(field_type, field_value) # test

                    elif field_name == table_key_field:
                        key = field_value
                        entity[TYPE_KEY] = self.mysql_to_rocket(TYPE_KEY, field_value)

                    elif field_type == TYPE_EMB_LIST:
                        field_type = TYPE_STR
                        if field_value:
                            if field_value.startswith("integer:"):
                                field_value = field_value[8:]
                                field_type = TYPE_INT
                            value = '|'.join(map(lambda v: self.mysql_to_rocket(TYPE_STR, v), field_value.split('|')))
                        else:
                            value = ''
                        entity["*%s|%s" % (field_type, field_name)] = value

                    else:
                        if field_name.endswith("_ref"):
                            field_name = field_name[:len(field_name) - 4]

                        if (not send_fields or field_name in send_fields) and (not field_name in send_fields_exclude):
                            entity["%s|%s" % (field_type, field_name)] = self.mysql_to_rocket(field_type, field_value)

                    i += 1

                if not key:
                    logging.error(self.name + ' Send %s: key field %s is empty' % (kind, table_key_field))
                    continue

                # retrieve lists
                for field_name, field_type in table.list_fields.items():
                    if (not send_fields or field_name in send_fields) and (not field_name in send_fields_exclude):
                        cur.execute('select %s from %s_%s where %s = ' % (field_name, table_name, field_name, table_key_field) + """%s""", (key))

                        items = []
                        for item in cur.fetchall():
                            items.append(self.mysql_to_rocket(field_type, item[0]))

                        entity["*%s|%s" % (field_type, field_name)] = '|'.join(items)

                logging.debug(self.name + ' Send %s: key=%s' % (kind, key))

                for attempt in range(SEND_RETRY_COUNT):
                    if self.send_row(kind, key, entity, attempt + 1):
                        break
                else:
                    logging.error(" Send %s: all %d attempts failed, giving up until next cycle" % (kind, attempt + 1))
                    # if all retries failed - rollback and return
                    self.con.rollback()
                    return

            logging.info(self.name + ' Send %s: batch end, count=%d, offset=%d' % (kind, count, offset))
            offset += count

            if intermediate_timestamp:
                intermediate_timestamp = to_iso(intermediate_timestamp)
                self.write_send_state(cur, kind, intermediate_timestamp)
                self.con.commit()
                self.send_state = intermediate_timestamp

        to_timestamp = to_iso(to_timestamp)
        self.write_send_state(cur, kind, to_timestamp)
        self.con.commit()
        self.send_state = to_timestamp

        cur.close()

        return count > 0 or offset > 0

    def send_row(self, kind, key, entity, attempt):
        #logging.error(entity)

        url = "%s/%s?secret_key=%s" % (ROCKET_URL, kind, SECRET_KEY)

        if self.after_send:
            url += "&after_send=%s" % self.after_send

        try:
            result = urllib.urlopen(url, urllib.urlencode(entity))
            response = ''.join(result).strip(" \r\n")
        except:
            logging.exception(self.name + ' Send %s: key=%s, attempt #%d failed' % (kind, key, attempt + 1))
            return False

        try:
            if result.code != 200:
                logging.error(self.name + " Send %s: key=%s, attempt #%d failed, code=%d, URL=%s, response=%s" % (kind, key, attempt, result.code, url, response))
                return False
        finally:
            result.close()

        return True

    def receive_updates(self, kind, table_name, timestamp_field, table_key_field, receive_fields, receive_fields_exclude, embedded_list_fields):
        updates = False

        # receive updates
        count = BATCH_SIZE
        while count == BATCH_SIZE:
            count = 0

            url = "%s/%s?secret_key=%s&timestamp=%s&count=%d" % (ROCKET_URL, kind, SECRET_KEY, timestamp_field, BATCH_SIZE)
            if self.receive_state:
                url += "&from=%s" % self.receive_state
                logging.info(self.name + " Receive %s: from %s" % (kind, self.receive_state))
            else:
                logging.info(self.name + " Receive %s: from beginning" % (kind))

            try:
                result = urllib.urlopen(url)
                response = ''.join(result)
            except:
                logging.exception(self.name + " Receive %s: error retrieving updates, URL=%s" % (kind, url))
                return False

            if result.code != 200:
                logging.error(self.name + " Receive %s: error retrieving updates, code=%d, URL=%s, response=%s" % (kind, result.code, url, response))
                return False

            cur = self.con.cursor()

            try:

                xml = ElementTree.XML(response)
                for entity in xml:
                    self.receive_row(cur, kind, table_name, timestamp_field, table_key_field, receive_fields, receive_fields_exclude, embedded_list_fields, entity)
                    count += 1
                    last_timestamp = entity.findtext(timestamp_field)

                if count > 0:
                    updates = True
                    self.write_receive_state(cur, kind, last_timestamp)
                    self.con.commit()
                    self.receive_state = last_timestamp

            except ExpatError, e:
                logging.exception(self.name + " Receive %s: error parsing response: %s, response:\n%s" % (kind, e, response))
                self.con.rollback()

            except:
                logging.exception(self.name + " Receive %s: error" % kind)
                self.con.rollback()

            cur.close()

            logging.info(self.name + " Receive %s: batch end, count=%d" % (kind, count))

        return updates

    def receive_row(self, cur, kind, table_name, timestamp_field, table_key_field, receive_fields, receive_fields_exclude, embedded_list_fields, entity):
        fields = []
        values = []

        table = self.get_table_metadata(cur, table_name, timestamp_field, table_key_field, embedded_list_fields)

        key = self.rocket_to_mysql(TYPE_KEY, entity.attrib[TYPE_KEY])

        logging.debug(self.name + " Receive %s: key=%s" % (kind, key))

        row = None

        for field in entity:
            field_name = field.tag

            if (not receive_fields or field_name in receive_fields) and (not field_name in receive_fields_exclude):
                # only receive fields if no receive fields are specified (means ALL will be received
                # or the field is in receive fields list

                field_type = field.attrib["type"]

                if field_type == TYPE_REFERENCE:
                    field_name += "_ref"

                is_list = "list" in field.attrib
                is_embedded_list = field_name in embedded_list_fields
                self.synchronize_field(cur, table, field_name, field_type, is_list, is_embedded_list)

                if is_embedded_list:
                    list_values = []
                    for item in field:
                        list_values.append(item.text)
                    fields.append("`%s`" % field_name)
                    values.append('|'.join(list_values))
                elif is_list:
                    list_table_name = '%s_%s' % (table_name, field_name)
                    sql = 'DELETE FROM ' + list_table_name + ' WHERE ' + table_key_field + """ = %s"""
                    cur.execute(sql, (key))
                    for item in field:
                        sql = 'INSERT INTO ' + list_table_name + ' (' + table_key_field + ',' + field_name + """) VALUES (%s, %s)"""
                        cur.execute(sql, (key, self.rocket_to_mysql(field_type, item.text)))
                else:
                    fields.append("`%s`" % field_name)
                    values.append(self.rocket_to_mysql(field_type, field.text))

        cur.execute("SELECT * FROM " + table_name + " WHERE " + table_key_field + """ = %s""", (key))
        if cur.fetchone():
            # record already exist
            if len(fields) > 0:
                values.append(key)
                sql = 'UPDATE `%s` SET %s WHERE %s = ' % (table_name, ','.join(map(lambda f: f + """=%s""", fields)), table_key_field) + """%s"""
                cur.execute(sql, values)

        else:
            fields.append(table_key_field)
            values.append(key)
            sql = 'INSERT INTO `%s` (%s) VALUES (%s)' % (table_name, ','.join(fields), ','.join(map(lambda f: """%s""", fields)))
            cur.execute(sql, values)

    def get_table_metadata(self, cur, table_name, timestamp_field, table_key_field, embedded_list_fields):
        if not self.table:
            cur.execute('SHOW tables LIKE "%s"' % table_name)
            if cur.fetchone():
                # table exist

                # start with empty definition
                self.table = Table(table_name, timestamp_field, table_key_field)

                # add table fields
                cur.execute('SHOW COLUMNS FROM %s' % table_name)
                for col in cur.fetchall():
                    field_name = col[0]
                    if field_name in embedded_list_fields:
                        table.fields[field_name] = TYPE_EMB_LIST
                    else:
                        field_type = self.normalize_type(field_name, col[1])
                        table.fields[field_name] = field_type

                # add list fields stored in separate self.tables (TableName_ListField)
                cur.execute('SHOW tables LIKE "%s_%%"' % table_name)
                for row in cur.fetchall():
                    list_table_name = row[0]
                    list_field_name = list_table_name[len(table_name) + 1:]
                    cur.execute('SHOW COLUMNS FROM %s' % list_table_name)
                    for col in cur.fetchall():
                        field_name = col[0]
                        if field_name == list_field_name:
                            field_type = self.normalize_type(field_name, col[1])
                            table.list_fields[field_name] = field_type
                            break

            else:
                # self.tables is missing
                cur.execute("CREATE TABLE %s (%s VARCHAR(255) NOT NULL, %s TIMESTAMP, PRIMARY KEY(%s), INDEX %s(%s)) ENGINE = %s CHARACTER SET utf8 COLLATE utf8_general_ci" % (table_name, table_key_field, timestamp_field, table_key_field, timestamp_field, timestamp_field, DATABASE_ENGINE))

                self.table = Table(table_name, timestamp_field, table_key_field)

        return self.table

    def normalize_type(self, field_name, field_type):
        if field_name.endswith("_ref"):
            return TYPE_REFERENCE
        elif field_type.startswith("tinyint(1)"):
            return TYPE_BOOL
        elif field_type.startswith("varchar"):
            return TYPE_STR
        elif field_type.startswith("int") or field_type.startswith("bigint"):
            return TYPE_INT
        else:
            return field_type

    def synchronize_field(self, cur, table, field_name, field_type, is_list, is_embedded_list):
        if is_embedded_list:
            if field_name not in table.fields.has_key:
                # table doesn't have this field yet - add it
                self.create_field(cur, table.name, table.key_field, field_name, TYPE_EMB_LIST, False)
                table.fields[field_name] = TYPE_EMB_LIST
        elif is_list:
            if field_name not in table.list_fields:
                # table doesn't have this field yet - add it
                self.create_field(cur, table.name, table.key_field, field_name, field_type, is_list)
                table.list_fields[field_name] = field_type
        else:
            if field_name not in table.fields:
                # table doesn't have this field yet - add it
                self.create_field(cur, table.name, table.key_field, field_name, field_type, is_list)
                table.fields[field_name] = field_type

    def create_field(self, cur, table_name, table_key_field, field_name, field_type, is_list):
        if is_list:
            # this is list field - create a separate table for it
            list_table_name = "%s_%s" % (table_name, field_name)
            cur.execute("CREATE TABLE %s (id BIGINT NOT NULL AUTO_INCREMENT, %s VARCHAR(255) NOT NULL, PRIMARY KEY(id), INDEX k(%s)) ENGINE = %s CHARACTER SET utf8 COLLATE utf8_general_ci" % (list_table_name, table_key_field, table_key_field, DATABASE_ENGINE))
            self.create_field(cur, list_table_name, table_key_field, field_name, field_type, False)
        else:
            if field_type == TYPE_DATETIME:
                cur.execute("ALTER TABLE %s ADD COLUMN `%s` DATETIME" % (table_name, field_name))
            elif field_type == TYPE_TIMESTAMP:
                cur.execute("ALTER TABLE %s ADD COLUMN `%s` TIMESTAMP NOT NULL, ADD INDEX %s(%s)" % (table_name, field_name, field_name, field_name))
            elif field_type == TYPE_INT:
                cur.execute("ALTER TABLE %s ADD COLUMN `%s` BIGINT" % (table_name, field_name))
            elif field_type == TYPE_LONG:
                cur.execute("ALTER TABLE %s ADD COLUMN `%s` BIGINT" % (table_name, field_name))
            elif field_type == TYPE_FLOAT:
                cur.execute("ALTER TABLE %s ADD COLUMN `%s` FLOAT" % (table_name, field_name))
            elif field_type == TYPE_BOOL:
                cur.execute("ALTER TABLE %s ADD COLUMN `%s` BOOLEAN" % (table_name, field_name))
            elif field_type == TYPE_TEXT or field_type == TYPE_EMB_LIST:
                cur.execute("ALTER TABLE %s ADD COLUMN `%s` TEXT" % (table_name, field_name))
            elif field_type == TYPE_KEY or field_type == TYPE_REFERENCE:
                cur.execute("ALTER TABLE %s ADD COLUMN `%s` VARCHAR(500)" % (table_name, field_name))
            elif field_type == TYPE_BLOB:
                cur.execute("ALTER TABLE %s ADD COLUMN `%s` BLOB" % (table_name, field_name))
            else:  # str
                cur.execute("ALTER TABLE %s ADD COLUMN `%s` VARCHAR(500)" % (table_name, field_name))

    def mysql_to_rocket(self, field_type, mysql_value):
        if mysql_value == None:
            rocket_value = ""
        elif (field_type == TYPE_DATETIME or field_type == TYPE_TIMESTAMP):
            rocket_value = to_iso(mysql_value)
        elif field_type == TYPE_KEY:
            rocket_value = self.mysql_to_rocket(TYPE_STR, mysql_value)
            if rocket_value[0] in '0123456789':
                # MYSQL ID
                rocket_value = '_%s' % rocket_value
            elif mysql_value[0] == '_':
                # APPENGINE ID
                rocket_value = rocket_value[1:]

        elif field_type == TYPE_REFERENCE:
            slash = mysql_value.find("/")
            if slash > 0:
                kind = mysql_value[:slash]
                key_name_or_id = self.mysql_to_rocket(TYPE_KEY, mysql_value[slash + 1:])
                rocket_value = "%s/%s" % (kind, key_name_or_id)
            else:
                logging.error(self.name + " Error: Invalid reference value: %s" % mysql_value)
                rocket_value = ""
        elif field_type == TYPE_BLOB:
            rocket_value = base64.b64encode(mysql_value)
        else:
            rocket_value = (u'%s' % mysql_value).replace('|', '&#124;').encode('utf-8')

        return rocket_value

    def rocket_to_mysql(self, field_type, rocket_value):
        if not rocket_value:
            mysql_value = None
        elif field_type == TYPE_DATETIME or field_type == TYPE_TIMESTAMP:
            mysql_value = from_iso(rocket_value)
        elif field_type == TYPE_BOOL:
            mysql_value = bool(int(rocket_value))
        elif field_type == TYPE_INT:
            mysql_value = int(rocket_value)
        elif field_type == TYPE_LONG:
            mysql_value = long(rocket_value)
        elif field_type == TYPE_FLOAT:
            mysql_value = float(rocket_value)
        elif field_type == TYPE_KEY:
            if rocket_value[0] in '0123456789':
                # APPENGINE ID
                mysql_value = u'_%s' % rocket_value
            elif rocket_value[0] == '_':
                # MYSQL ID
                mysql_value = rocket_value[1:]
            else:
                mysql_value = rocket_value

        elif field_type == TYPE_REFERENCE:
            slash = rocket_value.find("/")
            if slash > 0:
                kind = rocket_value[:slash]
                key_name_or_id = self.rocket_to_mysql(TYPE_KEY, rocket_value[slash + 1:])
                mysql_value = "%s/%s" % (kind, key_name_or_id)
            else:
                logging.error(self.name + " Error: invalid reference value: %s" % rocket_value)
                mysql_value = None
        elif field_type == TYPE_BLOB:
            mysql_value = base64.b64decode(rocket_value)
        else:
            mysql_value = rocket_value

        return mysql_value

    def write_send_state(self, cur, kind, send_state):
        if self.send_state or self.receive_state:
            cur.execute("""UPDATE rocket_station SET send_state =  %s WHERE kind = %s""", (send_state, kind))
        else:
            cur.execute("""INSERT INTO rocket_station (kind, send_state) VALUES (%s, %s)""", (kind, send_state))

    def write_receive_state(self, cur, kind, receive_state):
        if self.send_state or self.receive_state:
            cur.execute("""UPDATE rocket_station SET receive_state =  %s WHERE kind = %s""", (receive_state, kind))
        else:
            cur.execute("""INSERT INTO rocket_station (kind, receive_state) VALUES (%s, %s)""", (kind, receive_state))
