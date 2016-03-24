# Copyright (c) 2014 Mirantis Inc.
#
# Licensed under the Apache License, Version 2.0 (the License);
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an AS IS BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and#
# limitations under the License.

from fabric.api import run, settings, env
import copy

from cloudferrylib.utils import log
from cloudferrylib.utils import utils

LOG = log.getLogger(__name__)


def delete_file_from_rbd(ssh_ip, file_path):
    with settings(host_string=ssh_ip,
                  connection_attempts=env.connection_attempts):
        with utils.forward_agent(env.key_filenames):
            run("rbd rm %s" % file_path)


def convert_to_dest(data, source, dest):
    d = copy.copy(data)
    d[dest] = d['meta'][dest]
    d['meta'][source] = d[source]
    return d


def require_methods(methods, obj):
    for method in dir(obj):
        if method not in methods:
            return False
    return True


def select_boot_volume(info):
    for k, v in info[utils.STORAGE_RESOURCE][utils.VOLUMES_TYPE].iteritems():
        if not((v['num_device'] == 0) and v['bootable']):
            del info[utils.STORAGE_RESOURCE][utils.VOLUMES_TYPE][k]
    return info
