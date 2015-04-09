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


from cloudferry.lib.base.action import action
from cloudferry.lib.utils.ssh_util import SshUtil


class RemoteExecution(action.Action):

    def __init__(self, cloud, host=None, int_host=None, config_migrate=None):
        self.cloud = cloud
        self.host = host
        self.int_host = int_host
        self.config_migrate = config_migrate
        self.remote_exec_obj = SshUtil(self.cloud, self.config_migrate, self.host)
        super(RemoteExecution, self).__init__({})

    def run(self, command, **kwargs):
        self.remote_exec_obj.execute(command, self.int_host)
        return {}
