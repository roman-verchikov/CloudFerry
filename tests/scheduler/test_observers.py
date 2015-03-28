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

from cloudferrylib.scheduler import observers

from tests import test


class ObservableTestCase(test.TestCase):
    def test_observable_does_not_duplicate_observer_list(self):
        num_observers = 10
        observer = 'o'
        observers_ = [observer for _ in xrange(num_observers)]
        o = observers.Observable(observers=observers_)
        self.assertEqual(o.observers, {observer})

        for _ in xrange(num_observers):
            o.add_observer(observer)

        self.assertEqual(o.observers, {observer})

    def test_creating_empty_observable_does_not_raise_errors(self):
        try:
            observers.Observable()
        except:
            self.fail('Unexpected exception: Observable() should not raise '
                      'errors with empty observers list')

    def test_removing_non_existing_observer_does_not_raise_exception(self):
        o = observers.Observable()

        try:
            o.delete_observer('o')
        except:
            self.fail('Unexpected exception caught. delete_observer should '
                      'not raise errors if object is not present')
