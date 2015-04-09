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


import copy

from fabric.api import env
from fabric.api import run
from fabric.api import settings

from cloudferry.lib.base.action import action
from cloudferry.lib.os.actions import convert_file_to_image
from cloudferry.lib.os.actions import convert_image_to_file
from cloudferry.lib.os.actions import convert_volume_to_image
from cloudferry.lib.os.actions import copy_g2g
from cloudferry.lib.os.actions import task_transfer
from cloudferry.lib.utils import utils as utl, forward_agent


CLOUD = 'cloud'
BACKEND = 'backend'
CEPH = 'ceph'
ISCSI = 'iscsi'
COMPUTE = 'compute'
INSTANCES = 'instances'
INSTANCE_BODY = 'instance'
INSTANCE = 'instance'
DIFF = 'diff'
EPHEMERAL = 'ephemeral'
DIFF_OLD = 'diff_old'
EPHEMERAL_OLD = 'ephemeral_old'

PATH_DST = 'path_dst'
HOST_DST = 'host_dst'
PATH_SRC = 'path_src'
HOST_SRC = 'host_src'

TEMP = 'temp'
FLAVORS = 'flavors'


TRANSPORTER_MAP = {CEPH: {CEPH: 'ssh_ceph_to_ceph',
                          ISCSI: 'ssh_ceph_to_file'},
                   ISCSI: {CEPH: 'ssh_file_to_ceph',
                           ISCSI: 'ssh_file_to_file'}}


class TransportInstance(action.Action):
    # TODO constants

    def run(self, info=None, **kwargs):
        info = copy.deepcopy(info)
        new_info = {
            utl.INSTANCES_TYPE: {
            }
        }

        #Get next one instance
        for instance_id, instance in info[utl.INSTANCES_TYPE].iteritems():
            one_instance = {
                utl.INSTANCES_TYPE: {
                    instance_id: instance
                }
            }
            one_instance = self.deploy_instance(self.dst_cloud, one_instance)

            new_info[utl.INSTANCES_TYPE].update(
                one_instance[utl.INSTANCES_TYPE])

        return {
            'info': new_info
        }

    def deploy_instance(self, dst_cloud, info):
        info = copy.deepcopy(info)
        dst_compute = dst_cloud.resources[COMPUTE]

        new_ids = dst_compute.deploy(info)
        for i in new_ids.iterkeys():
            dst_compute.wait_for_status(i, 'active')
        new_info = dst_compute.read_info(search_opts={'id': new_ids.keys()})
        for i in new_ids.iterkeys():
            dst_compute.change_status('shutoff', instance_id=i)
        for new_id, old_id in new_ids.iteritems():
            new_info['instances'][new_id]['old_id'] = old_id
            new_info['instances'][new_id]['meta'] = \
                info['instances'][old_id]['meta']
        info = self.prepare_ephemeral_drv(info, new_info, new_ids)
        return info

    def prepare_ephemeral_drv(self, info, new_info, map_new_to_old_ids):
        info = copy.deepcopy(info)
        new_info = copy.deepcopy(new_info)
        for new_id, old_id in map_new_to_old_ids.iteritems():
            instance_old = info[INSTANCES][old_id]
            instance_new = new_info[INSTANCES][new_id]

            ephemeral_path_dst = instance_new[EPHEMERAL][PATH_SRC]
            instance_new[EPHEMERAL][PATH_DST] = ephemeral_path_dst            
            ephemeral_host_dst = instance_new[EPHEMERAL][HOST_SRC]
            instance_new[EPHEMERAL][HOST_DST] = ephemeral_host_dst
            
            diff_path_dst = instance_new[DIFF][PATH_SRC]
            instance_new[DIFF][PATH_DST] = diff_path_dst            
            diff_host_dst = instance_new[DIFF][HOST_SRC]
            instance_new[DIFF][HOST_DST] = diff_host_dst

            ephemeral_path_src = instance_old[EPHEMERAL][PATH_SRC]
            instance_new[EPHEMERAL][PATH_SRC] = ephemeral_path_src            
            ephemeral_host_src = instance_old[EPHEMERAL][HOST_SRC]
            instance_new[EPHEMERAL][HOST_SRC] = ephemeral_host_src
            
            diff_path_src = instance_old[DIFF][PATH_SRC]
            instance_new[DIFF][PATH_SRC] = diff_path_src            
            diff_host_src = instance_old[DIFF][HOST_SRC]
            instance_new[DIFF][HOST_SRC] = diff_host_src

        return new_info
