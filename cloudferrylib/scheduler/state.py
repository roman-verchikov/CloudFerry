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

import datetime
import itertools
import signal

import prettytable

from cloudferrylib.utils import utils
from cloudferrylib.scheduler import observers
import data_storage

LOG = utils.get_log(__name__)

TASK_NOT_STARTED = "not started"
TASK_STARTED = "started"
TASK_FAILED = "failed"
TASK_SUCCEEDED = "succeeded"
TASK_INTERRUPTED = "interrupted"

TASKS_DB_KEY = "tasks"


class TaskState(dict):
    def __init__(self, task=None, sequence=None, state=TASK_NOT_STARTED,
                 **kwargs):
        super(TaskState, self).__init__(**kwargs)
        self.update({
            'key': task_key(sequence),
            'state': state,
            'id': int(sequence),
            'name': str(task),
            'timestamp': str(datetime.datetime.now())
        })

    def __eq__(self, other):
        return (self['key'] == other['key'] and
                self['id'] == other['id'] and
                self['name'] == other['name'])

    def __ne__(self, other):
        return not self == other

    @classmethod
    def from_dict(cls, d):
        return cls(task=d['name'], sequence=d['id'], state=d['state'])


def task_key(sequence):
    return ':'.join([TASKS_DB_KEY, str(sequence)])


def task_data(task, sequence, state=TASK_NOT_STARTED):
    """
    DB representation of task state
    """
    return TaskState(task, sequence, state)


class TaskStateObserver(object):
    """Observer which handles notifications on task state changes"""

    def __init__(self, redis=None):
        if redis is None:
            redis = data_storage.get_connection()
        self.db = TaskStatePersistent(redis=redis)

    def notify(self, task, task_state):
        """
        Task state notification handler. Handles scheduler-initiated events.
        """
        if task_state == TASK_STARTED:
            self.db.task_started(task, task_state)
        else:
            self.db.update_current_tasks_state(task_state)


class TaskStatePersistent(object):
    """
    Handles all DB-related stuff.

    The DB backend is Redis.

    Data model:
     1. Each task execution state;
        Format:
            key: tasks:<task_uuid>
            value: {
                'key': 'tasks:<task_uuid>',
                'id': '<task_uuid>',
                'state': one_of(TASK_NOT_STARTED,
                                TASK_STARTED,
                                TASK_FAILED,
                                TASK_SUCCEEDED,
                                TASK_INTERRUPTED),
                'name': <task_name>,
                'timestamp': <timestamp>
            }
     2. Pointer to last executed task;
        Format:
            key: CURRENT_TASK_KEY
            value: {str}<task_key>
     3. List of task IDs in execution order:
        Format:
            key: TASKS
            value: {list}<task ID>
    """

    CURRENT_TASK_KEY = "current_task"
    TASKS = "tasks"

    def __init__(self, redis=None):
        if redis is None:
            redis = data_storage.get_connection()
        self.redis = redis

    def update_current_tasks_state(self, state):
        current_task = self.get_current_task()
        tk = current_task['key']
        td = current_task
        td['state'] = state

        pipe = self.redis.pipeline()
        pipe.hmset(tk, td)
        if state == TASK_SUCCEEDED:
            pipe.incr(self.CURRENT_TASK_KEY)
        pipe.execute()

    def task_started(self, task, task_state):
        i = int(self.redis.get(self.CURRENT_TASK_KEY))
        tk = task_key(i)
        td = task_data(task, i, task_state)

        def register_started_task(pipe):
            pipe.hmset(tk, td)

        self.redis.transaction(register_started_task, self.CURRENT_TASK_KEY)

    def get_current_task(self):
        """Returns the last executed task"""
        current_task = self.redis.get(self.CURRENT_TASK_KEY)
        if current_task is not None:
            num_tasks = self.redis.llen(self.TASKS)
            all_tasks_finished = num_tasks <= int(current_task)
            if not all_tasks_finished:
                return TaskState.from_dict(
                    self.redis.hgetall(task_key(current_task)))

    def get_history(self):
        all_task_ids = self.redis.lrange(self.TASKS, 0, -1)
        pipe = self.redis.pipeline()
        for task_id in all_task_ids:
            pipe.hgetall(task_id)
        history = pipe.execute()
        return map(TaskState.from_dict, history)

    def reset(self, task_id):
        """Changes state of :task_id and all the following to not started"""
        num_tasks = self.redis.llen(self.TASKS)
        task_id = num_tasks if num_tasks < task_id else task_id
        self.redis.set(self.CURRENT_TASK_KEY, task_id)

    def cleanup(self):
        """Removes all state-related data from persistent storage. In other words,
        CloudFerry 'forgets' all the tasks it executed previously."""
        all_tasks = self.get_history()

        self.redis.delete(all_tasks, self.TASKS, self.CURRENT_TASK_KEY)

    def already_started(self, task_id):
        task_id = task_key(task_id)
        return self.redis.hget(task_id, 'state') not in [TASK_NOT_STARTED,
                                                         None]

    def build_schedule(self, migration):
        """
        Fills DB with scenario information on start following the algorithm
        below:

        if scenario changed since previous run:
            remove previous scenario from DB
            write new scenario to DB
        else:
            fast forward scenario to CURRENT_STATE
        """
        history = self.get_history()
        new_scenario = [task_data(task, i) for i, task in enumerate(migration)]

        scenario_changed = (len(history) == 0 or len(new_scenario) == 0 or
                            len(history) != len(new_scenario) or
                            any([old != new for old, new in
                                 itertools.izip(history, new_scenario)]))

        migration.to_start()

        if scenario_changed:
            pipe = self.redis.pipeline()
            pipe.delete(history, self.TASKS)
            pipe.set(self.CURRENT_TASK_KEY, 0)
            for i, task in enumerate(migration):
                tk = task_key(i)
                td = task_data(task, i, TASK_NOT_STARTED)
                pipe.hmset(tk, td)
                pipe.rpush(self.TASKS, tk)
            pipe.execute()
            migration.to_start()
        else:
            current_task = int(self.redis.get(self.CURRENT_TASK_KEY))
            migration.fast_forward_to(current_task)


class SignalHandler(observers.Observable):
    def __init__(self, siglist=None, handler=None, **kwargs):
        super(SignalHandler, self).__init__(**kwargs)
        if siglist is None:
            siglist = [signal.SIGTERM, signal.SIGINT]
        if handler is None:
            handler = self.system_signal_handler
        for signum in siglist:
            signal.signal(signum, handler)

    def system_signal_handler(self, signum, frame):
        LOG.info("Signal %d caught, setting current task state to INTERRUPTED",
                 signum)
        self.notify_observers(None, TASK_INTERRUPTED)
        signal.signal(signum, signal.SIG_DFL)


def pretty_printer(tasks=None, current=None):
    """Returns pretty-formatted table with :tasks. If :current is provided,
    table highlights current task with '<---' symbol in additional column.
    """
    if tasks is None:
        tasks = []
    elif isinstance(tasks, dict):
        tasks = [tasks]

    columns = ['#', 'Name', 'State', 'Started at', 'Current']
    pt = prettytable.PrettyTable(columns)
    for t in tasks:
        row = [t.get('id'), t.get('name'), t.get('state'), t.get('timestamp')]
        if current is not None and current.get('id') == t.get('id'):
            row.append('<------')
        else:
            row.append('')
        pt.add_row(row)

    return pt
