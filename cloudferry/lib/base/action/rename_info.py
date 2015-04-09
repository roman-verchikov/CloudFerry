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
import copy


class RenameInfo(action.Action):

    def __init__(self, init, original_info_name, info_name):
        self.original_info_name = original_info_name
        self.info_name = info_name
        super(RenameInfo, self).__init__(init)

    def run(self, **kwargs):
        return {
            self.original_info_name: None,
            self.info_name: kwargs[self.original_info_name]
        }

