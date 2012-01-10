#!/usr/bin/env python
# encoding: utf-8
#
# Simple tool to bulk-replicate a datastore.
#
# Copyright 2009 Kaspars Dancis
# Copyright 2012 Maximillian Dornseif
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
import os
import sys
import time
import urllib
import hashlib
from datetime import timedelta
from optparse import OptionParser
from sys import stdout
from xml.etree import ElementTree
from xml.parsers.expat import ExpatError

import MySQLdb as db

from common import *


TIMESTAMP_FIELD = 'updated_at'
BATCH_SIZE = 5


def get_db_connection():
    """Get a database connection."""
    global options
    kwargs = {
        'charset': 'utf8',
        'use_unicode': True,
    }
    if options.database_user:
        kwargs['user'] = options.database_user
    if options.database_name:
        kwargs['db'] = options.database_name
    if options.database_password:
        kwargs['passwd'] = options.database_password
    if options.database_host.startswith('/'):
        kwargs['unix_socket'] = options.database_host
    elif options.database_host:
        kwargs['host'] = options.database_host
    if options.database_port:
        kwargs['port'] = int(options.database_port)
    return db.connect(**kwargs)


def get_state(kind):
    """Get the current replication state."""
    ret = (None, None)
    con = get_db_connection()
    cur = con.cursor()
    # reading existing replication state if available
    cur.execute('SHOW TABLES LIKE "_rocket_station"')
    if cur.fetchone():
        # table exists
        cur.execute("SELECT receive_state, receive_cursor FROM _rocket_station WHERE kind = '%s'" % kind)
        row = cur.fetchone()
        if row:
            # entry exists, return it
            ret = (row[0], row[1])
            logging.debug("loaded state for %s: %s", kind, ret)
        else:
            # create empty entry
            cur.execute("""INSERT INTO _rocket_station (kind) VALUES (%s)""", kind)
            logging.info("inserting _rocket_station.%s" % kind)
    else:
        # create table and insert empty entry
        cur.execute("""CREATE TABLE _rocket_station
                       (kind VARCHAR(255),
                        send_state VARCHAR(255),
                        receive_state VARCHAR(255),
                        receive_cursor VARCHAR(255),
                        PRIMARY KEY (kind))
                        ENGINE = %s CHARACTER SET utf8 COLLATE utf8_general_ci""" % options.database_engine)
        logging.info("creeating _rocket_station")
        cur.execute("""INSERT INTO _rocket_station (kind) VALUES (%s)""", kind)
        logging.info("inserting _rocket_station.%s" % kind)
    con.commit()
    cur.close()
    return ret


class Table:
    def __init__(self, kind, timestamp_field):
        self.table_name = self.name = kind.lower()
        self.kind = kind
        self.timestamp_field = timestamp_field
        self.key_field = '_key'

        self.fields = {}
        self.fields[timestamp_field] = TYPE_TIMESTAMP

        self.list_fields = {}


def setup_table(kind):
    """Set-Up destination table."""
    table_name = kind.lower()
    con = get_db_connection()
    cur = con.cursor()
    # retrieve table metadata if available
    cur.execute('SHOW tables LIKE "%s"' % table_name)
    if cur.fetchone():  # table exist
        # start with empty definition
        table = Table(kind, TIMESTAMP_FIELD)
        # add table fields
        cur.execute('SHOW COLUMNS FROM %s' % table_name)
        for col in cur.fetchall():
            field_name = col[0]
            field_type = normalize_type(field_name, col[1])
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
                    field_type = normalize_type(field_name, col[1])
                    table.list_fields[field_name] = field_type
                    break
    else:
        # self.table is missing
        cur.execute(
            """CREATE TABLE %s (%s VARCHAR(255) NOT NULL,
                                %s TIMESTAMP, PRIMARY KEY(%s),
                                INDEX %s(%s))
               ENGINE = %s CHARACTER SET utf8 COLLATE utf8_general_ci""" % (
                table_name,
                '_key',
                TIMESTAMP_FIELD,
                '_key',
                TIMESTAMP_FIELD,
                TIMESTAMP_FIELD,
                options.database_engine,
            ))
        table = Table(table_name, TIMESTAMP_FIELD)
    con.commit()
    cur.close()
    # reading existing replication state if available
    table._receive_state, table._receive_cursor = get_state(kind)
    logging.info("Table setup for %s done", kind)
    return table


def replicate(kind, options):
    table = setup_table(kind)
    con = get_db_connection()
    cur = con.cursor()
    count = options.batchsize
    timestamps = []
    while count == options.batchsize:
        count = 0
        url = "%s/%s?secret_key=%s&count=%d" % (options.rocketurl, kind, options.secretkey, options.batchsize)
        if table._receive_cursor:
            url += "&cursor=%s" % table._receive_cursor
            logging.info("Receive %s: from %s" % (kind, table._receive_cursor))
        else:
            url += "&timestamp=%s" % table.timestamp_field
            if table._receive_state:
                url += "&from=%s" % table._receive_state
                logging.info("Receive %s: from %s" % (kind, table._receive_state))
            else:
                logging.info("Receive %s: from beginning" % (kind))

        result = urllib.urlopen(url)
        response = result.read()
        if result.code != 200:
            raise RuntimeError("Receive %s: error retrieving updates, code=%d, URL=%s, response=%s" % (kind, result.code, url, response))
        logging.debug("Received %s: %d bytes" % (kind, len(response)))

        xml = ElementTree.XML(response)
        for entity in xml:
            if entity.tag == '_cursor':
                table._receive_cursor = entity.text
            else:
                receive_row(cur, table, entity)
                timestamps.append(entity.findtext(table.timestamp_field))
                count += 1

        if count > 0:
            table._receive_state = min(timestamps)
            logging.info("""UPDATE _rocket_station SET receive_state = %s, receive_cursor = %s WHERE kind = %s""", table._receive_state, table._receive_cursor, kind)
            cur.execute("""UPDATE _rocket_station SET receive_state = %s, receive_cursor = %s WHERE kind = %s""", (table._receive_state, table._receive_cursor, kind))
        logging.info("Receive %s: batch end, count=%d, cursor=%s" % (kind, count, table._receive_cursor))
        con.commit()
    cur.close()


def receive_row(cur, table, entity):
    fields = []
    values = []

    table = get_table_metadata(cur, table)
    key = hashlib.md5(entity.attrib["datastorekey"]).hexdigest()
    row = None
    parent = entity.attrib["parent"]

    for field in entity:
        field_name = field.tag
        field_type = field.attrib["type"]
        if field_type == TYPE_REFERENCE:
            field_name += "_ref"
        is_list = "list" in field.attrib
        if field_name == '_key':
            continue
        synchronize_field(cur, table, field_name, field_type, is_list, key)

        if is_list:
            list_table_name = '%s_%s' % (table.table_name, field_name)
            sql = 'DELETE FROM ' + list_table_name + ' WHERE ' + table.key_field + """ = %s"""
            cur.execute(sql, (key))
            for item in field:
                sql = 'INSERT INTO ' + list_table_name + ' (' + table.key_field + ',' + field_name + """) VALUES (%s, %s)"""
                cur.execute(sql, (key, rocket_to_mysql(field_type, item.text)))
        else:
            fields.append("`%s`" % field_name)
            values.append(rocket_to_mysql(field_type, field.text))

    cur.execute("SELECT * FROM " + table.table_name + " WHERE " + table.key_field + """ = %s""", (key))
    if cur.fetchone():
        # record already exist
        if len(fields) > 0:
            values.append(key)
            sql = 'UPDATE `%s` SET %s WHERE %s = ' % (table.table_name, ','.join(map(lambda f: f + """=%s""", fields)), table.key_field) + """%s"""
            cur.execute(sql, values)
            # logging.debug("updating %s" % entity)
    else:
        fields.append(table.key_field)
        values.append(key)
        sql = 'INSERT INTO `%s` (%s) VALUES (%s)' % (table.table_name, ','.join(fields), ','.join(map(lambda f: """%s""", fields)))
        cur.execute(sql, values)


def get_table_metadata(cur, table):
    cur.execute('SHOW tables LIKE "%s"' % table.table_name)
    if cur.fetchone():  # table exist
        # add table fields
        cur.execute('SHOW columns FROM %s' % table.table_name)
        for col in cur.fetchall():
            field_name = col[0]
            field_type = normalize_type(field_name, col[1])
            table.fields[field_name] = field_type
        # add list fields stored in separate self.tables (TableName_ListField)
        cur.execute('SHOW tables LIKE "%s_%%"' % table.table_name)
        for row in cur.fetchall():
            list_table_name = row[0]
            list_field_name = list_table_name[len(table.table_name) + 1:]
            cur.execute('SHOW columns FROM %s' % list_table_name)
            for col in cur.fetchall():
                field_name = col[0]
                if field_name == list_field_name:
                    field_type = normalize_type(field_name, col[1])
                    table.list_fields[field_name] = field_type
                    break
    else:
        raise RuntimeError("Table %s missing" % table.table_name)
    return table


def normalize_type(field_name, field_type):
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


def synchronize_field(cur, table, field_name, field_type, is_list, key):
    if is_list:
        if field_name not in table.list_fields:
            # table doesn't have this field yet - add it
            create_field(cur, table.name, table.key_field, field_name, field_type, is_list)
            table.list_fields[field_name] = field_type
    else:
        if field_name not in table.fields:
            # table doesn't have this field yet - add it
            create_field(cur, table.name, table.key_field, field_name, field_type, is_list)
            table.fields[field_name] = field_type


def create_field(cur, table_name, table_key_field, field_name, field_type, is_list):
    if is_list:
        # this is list field - create a separate table for it
        list_table_name = "%s_%s" % (table_name, field_name)
        cur.execute("""CREATE TABLE %s (id BIGINT NOT NULL AUTO_INCREMENT,
                                        _key VARCHAR(255) NOT NULL,
                                        PRIMARY KEY(id),
                                        INDEX k(%s))
                       ENGINE = %s CHARACTER SET utf8 COLLATE utf8_general_ci""" % (list_table_name, table_key_field, options.database_engine))
        create_field(cur, list_table_name, table_key_field, field_name, field_type, False)
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
            cur.execute("ALTER TABLE %s ADD COLUMN `%s` VARCHAR(255)" % (table_name, field_name))
        elif field_type == TYPE_BLOB:
            cur.execute("ALTER TABLE %s ADD COLUMN `%s` BLOB" % (table_name, field_name))
        else:  # str
            cur.execute("ALTER TABLE %s ADD COLUMN `%s` VARCHAR(255)" % (table_name, field_name))


def rocket_to_mysql(field_type, rocket_value):
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
            key_name_or_id = rocket_to_mysql(TYPE_KEY, rocket_value[slash + 1:])
            mysql_value = "%s/%s" % (kind, key_name_or_id)
        else:
            logging.error("Error: invalid reference value: %s" % rocket_value)
            mysql_value = None
    elif field_type == TYPE_BLOB:
        mysql_value = base64.b64decode(rocket_value)
    else:
        mysql_value = rocket_value

    return mysql_value


def get_model_list(options):
    url = "%s/_modellist.txt?secret_key=%s" % (options.rocketurl, options.secretkey)
    logging.debug('requesting %s', url)
    response = urllib.urlopen(url)
    result = response.read()
    if response.code != 200:
        raise RuntimeError("error retrieving %s: %s" % (url, result))
    models = result.split()
    return models


def main():
    global options
    parser = OptionParser()
    parser.add_option("-d", "--debug", action="store_true",
                      help="output debugging information")
    parser.add_option("-q", "--quiet", action="store_true",
                      help="output only error messing")
    parser.add_option("-m", "--model",
                      help="transfer a single Model")
    parser.add_option("-s", "--secretkey",
                      help="secret key used by server")
    parser.add_option("-r", "--rocketurl",
                      help="url where approcket is running on GAE")
    parser.add_option("-b", "--batchsize", type="int", default=250,
                      help="entitys to transfer per HTTP-Request [%default]")
    parser.add_option("--database_host", default="localhost",
                      help="MySQL host [%default]")
    parser.add_option("--database_name", default="approcket",
                      help="MySQL database [%default]")
    parser.add_option("--database_user", default="approcket",
                      help="MySQL username [%default]")
    parser.add_option("--database_password", default="approcket_pw",
                      help="MySQL password [%default]")
    parser.add_option("--database_port", type="int", default=3306,
                      help="MySQL password [%default]")
    parser.add_option("--database_engine", default="InnoDB",
                      help="MySQL password [%default]")

    (options, args) = parser.parse_args()
    if options.debug:
        logging.basicConfig(level=logging.DEBUG,
                            format='%(asctime)s %(levelname)s %(message)s')
    elif options.quiet:
        logging.basicConfig(level=logging.ERROR,
                            format='%(asctime)s %(levelname)s %(message)s')
    else:
        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s %(levelname)s %(message)s')
    if options.model:
        replicate(options.model, options)
    else:
        for model in get_model_list(options):
            replicate(model, options)


if __name__ == "__main__":
    main()
