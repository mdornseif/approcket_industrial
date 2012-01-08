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


import sys
from datetime import datetime

TYPE_DATETIME = "datetime"
TYPE_TIMESTAMP = "timestamp"
TYPE_BOOL = "bool"
TYPE_LONG = "long"
TYPE_FLOAT = "float"
TYPE_INT = "int"
TYPE_TEXT = "text"
TYPE_KEY = "key"
TYPE_REFERENCE = "ref"
TYPE_STR = "str"
TYPE_EMB_LIST = "emb_list"
TYPE_BLOB = "blob"

DEFAULT_TIMESTAMP_FIELD = "timestamp"
DEFAULT_KEY_FIELD = "k"
DEFAULT_BATCH_SIZE = 100

TYPE = "TYPE"

REPLICATION_SERVICE = "rocket.station.ReplicationService"

KIND = "KIND"

TIMESTAMP_FIELD = "TMSF"
TABLE_NAME = "TBLN"
TABLE_KEY_FIELD = "TBLK"

MODE = "MODE"

SEND_RECEIVE = "SR"
RECEIVE_SEND = "RS"
SEND = "S"
RECEIVE = "R"

RECEIVE_FIELDS = "RF"
RECEIVE_FIELDS_EXCLUDE = "RFE"

SEND_FIELDS = "SF"
SEND_FIELDS_EXCLUDE = "SFE"

EMBEDDED_LIST_FIELDS = "ELF"

AFTER_SEND = "AFTER_SEND"

IDLE_TIME = "IDLE"


def escape(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace(
                        '>', '&gt;').replace('"', '&quot;').replace(
                        "'", '&#39;')


def from_iso(s):
    dt = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
    try:
        dt = dt.replace(microsecond=int(s[20:]))
    except:
        pass
    return dt


def to_iso(dt):
    return dt.isoformat()


class Log:
    def __init__(self, f):
        self.f = f

    def write(self, s):
        sys.stdout.write(s)
        self.f.write(s)

    def flush(self):
        self.f.flush()
