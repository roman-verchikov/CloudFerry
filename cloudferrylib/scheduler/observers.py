# Copyright 2015 Mirantis Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


class Observable(object):
    def __init__(self, observers=None):
        if observers is None:
            observers = []

        self.observers = set(observers)

    def add_observer(self, observer):
        self.observers.add(observer)

    def delete_observer(self, observer):
        if observer in self.observers:
            self.observers.remove(observer)

    def notify_observers(self, task, state, namespace=None):
        for observer in self.observers:
            observer.notify(task, state, namespace)
