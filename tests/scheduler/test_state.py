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

import os
import signal

import fakeredis
import mock
import operator

from cloudferrylib.scheduler import cursor
from cloudferrylib.scheduler import namespace
from cloudferrylib.scheduler import scheduler
from cloudferrylib.scheduler import state
from cloudferrylib.scheduler import task
from tests import test


class Task(task.Task):
    def __init__(self, restart_callback=lambda: None):
        super(Task, self).__init__()
        self.restart_callback = restart_callback
        self.was_run = False

    def run(self, **kwargs):
        self.restart_callback()
        self.was_run = True


class SucceedingTask(Task):
    pass


class FailingTask(Task):
    is_fixed_key = 'failing_task_fixed'

    def run(self, **kwargs):
        fixed = kwargs.get(self.is_fixed_key, False)
        if not fixed:
            raise Exception()
        super(FailingTask, self).run(**kwargs)


class TaskStateTestCase(test.TestCase):
    def test_equal_for_same_tasks_with_different_timestamps(self):
        num_task_states = 10
        t = SucceedingTask()
        self.assertTrue(all([state.TaskState(t, i) == state.TaskState(t, i)
                            for i in xrange(num_task_states)]))


class StateAwareSchedulerTestCase(test.TestCase):
    def __init__(self, *args, **kwargs):
        super(StateAwareSchedulerTestCase, self).__init__(*args, **kwargs)
        self.redis = None

    def setUp(self):
        super(StateAwareSchedulerTestCase, self).setUp()
        self.redis = fakeredis.FakeStrictRedis()

    def tearDown(self):
        super(StateAwareSchedulerTestCase, self).tearDown()
        self.redis.flushall()

    def test_scenario_is_restarted_from_where_it_failed(self):
        post_fail_task = mock.Mock()
        pre_fail_task = mock.Mock()

        t1 = SucceedingTask(pre_fail_task)
        t2 = SucceedingTask(pre_fail_task)
        t3 = FailingTask(post_fail_task)
        t4 = SucceedingTask(post_fail_task)
        scenario = (t1 >> t2 >> t3 >> t4)

        s = scheduler.StateAwareScheduler(redis=self.redis,
                                          migration=cursor.Cursor(scenario))
        s.start()

        self.assertTrue(t1.was_run)
        self.assertTrue(t2.was_run)
        self.assertFalse(t3.was_run)
        self.assertFalse(t4.was_run)

        self.assertEqual(s.status_error, scheduler.ERROR)
        self.assertTrue(pre_fail_task.called)
        self.assertFalse(post_fail_task.called)

        post_fail_task = mock.Mock()
        pre_fail_task = mock.Mock()

        t1 = SucceedingTask(pre_fail_task)
        t2 = SucceedingTask(pre_fail_task)
        t3 = FailingTask(post_fail_task)
        t4 = SucceedingTask(post_fail_task)
        scenario = (t1 >> t2 >> t3 >> t4)

        ns = namespace.Namespace({FailingTask.is_fixed_key: True})
        s = scheduler.StateAwareScheduler(redis=self.redis,
                                          namespace=ns,
                                          migration=cursor.Cursor(scenario))
        s.start()

        self.assertFalse(t1.was_run)
        self.assertFalse(t2.was_run)
        self.assertTrue(t3.was_run)
        self.assertTrue(t4.was_run)

        self.assertNotEqual(s.status_error, scheduler.ERROR)
        self.assertTrue(post_fail_task.called)
        self.assertFalse(pre_fail_task.called)

    def test_state_is_stored_for_identical_tasks(self):
        num_identical_tasks = 5
        scenario = self._generate_scenario(num_identical_tasks)

        s = scheduler.StateAwareScheduler(redis=self.redis,
                                          migration=cursor.Cursor(scenario))
        s.start()

        history = s.db.get_history()
        self.assertEqual(len(history), num_identical_tasks)

    def test_history_is_stored_in_execution_order(self):
        num_tasks = 10
        all_tasks = [SucceedingTask() for _ in xrange(num_tasks)]
        expected_history = [state.task_data(t, i)
                            for i, t in enumerate(all_tasks)]
        scenario = all_tasks[0]
        for t in all_tasks[1:]:
            scenario = scenario >> t

        s = scheduler.StateAwareScheduler(redis=self.redis,
                                          migration=cursor.Cursor(scenario))
        s.start()

        actual_history = s.db.get_history()

        self.assertTrue(all(map(operator.eq,
                                actual_history,
                                expected_history)))

    def test_state_is_set_to_error_for_failing_tasks(self):
        failing_task = FailingTask()
        scenario = (SucceedingTask() >> SucceedingTask() >> failing_task)
        s = scheduler.StateAwareScheduler(redis=self.redis,
                                          migration=cursor.Cursor(scenario))
        s.start()

        current_task = s.db.get_current_task()
        self.assertEqual(state.TASK_FAILED, current_task['state'])

    def test_current_state_is_set_to_the_last_failed_task(self):
        num_tasks = 10
        expected_task = FailingTask()
        scenario = self._generate_scenario(num_tasks) >> expected_task
        s = scheduler.StateAwareScheduler(redis=self.redis,
                                          migration=cursor.Cursor(scenario))
        s.start()
        actual_task = s.db.get_current_task()
        last_task = s.db.get_history()[-1]
        self.assertEqual(last_task, actual_task)

    def test_started_task_is_marked_as_started(self):
        num_tasks = 5
        scenario, ids = self._scenario_with_ids_returned(num_tasks)
        s = scheduler.StateAwareScheduler(redis=self.redis,
                                          migration=cursor.Cursor(scenario))
        s.start()

        for task_id in ids:
            self.assertTrue(s.db.already_started(task_id))

    def test_cleanup_deletes_only_state_data(self):
        num_tasks = 5
        num_non_state_items = 100

        def put_random_data(redis):
            pipe = redis.pipeline()
            for i in xrange(num_non_state_items):
                pipe.set(i, i)
            pipe.execute()

        put_random_data(self.redis)
        scenario = self._generate_scenario(num_tasks)
        s = scheduler.StateAwareScheduler(redis=self.redis,
                                          migration=cursor.Cursor(scenario))
        s.start()
        s.db.cleanup()

        for i in xrange(num_non_state_items):
            value = self.redis.get(i)
            self.assertEqual(str(i), value)

    def test_does_not_rewrite_schedule_for_previously_run_scenario(self):
        num_tasks = 10
        scenario = self._generate_scenario(num_tasks)
        s = scheduler.StateAwareScheduler(redis=self.redis,
                                          migration=cursor.Cursor(scenario))
        s.start()
        original_history = s.db.get_history()

        s = scheduler.StateAwareScheduler(redis=self.redis,
                                          migration=cursor.Cursor(scenario))
        new_history = s.db.get_history()

        self.assertEqual(original_history, new_history,
                         "History should not change for the same scenario")

    def test_history_is_not_empty_for_scenario(self):
        num_tasks = 10
        scenario = self._generate_scenario(num_tasks)
        s = scheduler.StateAwareScheduler(redis=self.redis,
                                          migration=cursor.Cursor(scenario))
        history = s.db.get_history()
        not_empty = lambda h: len(history) != 0
        self.assertTrue(not_empty(history))

    @staticmethod
    def _scenario_with_ids_returned(num_tasks):
        scenario = SucceedingTask()
        for _ in xrange(num_tasks - 1):
            t = SucceedingTask()
            scenario = scenario >> t
        return scenario, [i for i in xrange(num_tasks)]

    @staticmethod
    def _generate_scenario(num_tasks):
        scenario = SucceedingTask()
        for _ in xrange(num_tasks - 1):
            scenario = scenario >> SucceedingTask()
        return scenario


class SignalInterruptionTestCase(test.TestCase):
    def test_term_and_int_signals_are_caught(self):
        signals = [signal.SIGINT, signal.SIGTERM]
        signal_handler = mock.Mock()
        state.SignalHandler(siglist=signals, handler=signal_handler)
        for s in signals:
            os.kill(os.getpid(), s)
            self.assertTrue(signal_handler.called)

    def test_observers_are_notified_on_signal(self):
        signals = [signal.SIGINT, signal.SIGTERM]
        sh = state.SignalHandler(siglist=signals)
        sh.notify_observers = mock.Mock()
        for s in signals:
            os.kill(os.getpid(), s)
            self.assertTrue(sh.notify_observers.called)
