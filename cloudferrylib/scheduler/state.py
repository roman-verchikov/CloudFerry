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
import pickle

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


class Serializer(object):
    @classmethod
    def is_serializable(cls, obj):
        try:
            pickle.dumps(obj)
        except (pickle.PicklingError, RuntimeError) as e:
            LOG.warning("Unable to serialize object: %s", e)
            return False
        return True

    @classmethod
    def serialize(cls, obj):
        try:
            return pickle.dumps(obj)
        except (pickle.PicklingError, RuntimeError) as e:
            LOG.warning("Task namespace serialization failed: %s! All the "
                        "consecutive attempts to restart from this task "
                        "may fail due to invalid namespace", e)
            return pickle.dumps(None)

    @classmethod
    def deserialize(cls, string):
        try:
            return pickle.loads(string)
        except RuntimeError as e:
            LOG.warning("Namespace deserialization failed with %s. Attempts "
                        "to restart from this task may fail due to invalid "
                        "namespace.", e)

    @classmethod
    def remove_nonserializable_items(cls, namespace):
        """OS2OS inserts non-serializable objects to namespace which do not
        impact the migration process under '__init_task__' key. This method
        makes sure those objects never get into the DB"""
        if namespace:
            if hasattr(namespace, 'vars'):
                return {k: v for k, v in namespace.vars.items()
                        if cls.is_serializable(v)}
            elif isinstance(namespace, dict):
                return {k: v for k, v in namespace.items()
                        if cls.is_serializable(v)}


class TaskState(dict):
    def __init__(self, task=None, sequence=None, state=TASK_NOT_STARTED,
                 namespace=None, timestamp=None, **kwargs):
        super(TaskState, self).__init__(**kwargs)

        self.update({
            'key': task_key(sequence),
            'state': state,
            'id': int(sequence),
            'name': str(task),
            'namespace': Serializer.remove_nonserializable_items(namespace),
            'timestamp': timestamp
        })

    def __eq__(self, other):
        return (self['key'] == other['key'] and
                self['id'] == other['id'] and
                self['name'] == other['name'])

    def __ne__(self, other):
        return not self == other

    @classmethod
    def from_dict(cls, d):
        return cls(task=d.get('name'),
                   sequence=d.get('id', -1),
                   state=d.get('state', TASK_NOT_STARTED),
                   namespace=d.get('namespace'),
                   timestamp=d.get('timestamp', datetime.datetime.now()))

    @classmethod
    def from_db_record(cls, db_record):
        ns = db_record.get('namespace')
        if ns:
            db_record['namespace'] = Serializer.deserialize(ns)
        return cls.from_dict(db_record)

    def to_db_record(self):
        ns = self.get('namespace')
        self.update({'namespace': Serializer.serialize(ns)})
        return self


def task_key(sequence):
    return ':'.join([TASKS_DB_KEY, str(sequence)])


class TaskStateObserver(object):
    """Observer which handles notifications on task state changes"""

    def __init__(self, redis=None):
        if redis is None:
            redis = data_storage.get_connection()
        self.db = TaskStatePersistent(redis=redis)

    def notify(self, task, task_state, namespace):
        """
        Task state notification handler. Handles scheduler-initiated events.
        """
        if task_state == TASK_STARTED:
            self.db.task_started(task, task_state, namespace)
        else:
            self.db.update_current_task_state(task_state)


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
                'timestamp': <timestamp>,
                'namespace': <pickled task arguments (namespace)>
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

    def update_current_task_state(self, state):
        current_task = self.get_current_task()
        tk = current_task['key']
        td = current_task
        td['state'] = state

        pipe = self.redis.pipeline()
        pipe.hmset(tk, td.to_db_record())

        # proceed to next task only if current succeeded
        if state == TASK_SUCCEEDED:
            pipe.incr(self.CURRENT_TASK_KEY)
        pipe.execute()

    def task_started(self, task, task_state, namespace):
        i = int(self.redis.get(self.CURRENT_TASK_KEY))
        tk = task_key(i)
        td = TaskState.from_dict({'name': str(task),
                                  'id': i,
                                  'namespace': namespace,
                                  'state': task_state})

        def register_started_task(pipe):
            pipe.hmset(tk, td.to_db_record())

        self.redis.transaction(register_started_task, self.CURRENT_TASK_KEY)

    def get_current_task(self):
        """Returns the last executed task"""
        current_task = self.redis.get(self.CURRENT_TASK_KEY)
        if current_task is not None:
            num_tasks = self.redis.llen(self.TASKS)
            all_tasks_finished = num_tasks <= int(current_task)
            if not all_tasks_finished:
                return TaskState.from_db_record(
                    self.redis.hgetall(task_key(current_task)))

    def get_history(self):
        all_task_ids = self.redis.lrange(self.TASKS, 0, -1)
        pipe = self.redis.pipeline()
        for task_id in all_task_ids:
            pipe.hgetall(task_id)
        history = pipe.execute()
        return [TaskState.from_db_record(task) for task in history]

    def reset(self, task_id):
        """Changes state of :task_id and all the following to not started"""
        num_tasks = self.redis.llen(self.TASKS)
        task_id = num_tasks if num_tasks < task_id else task_id
        self.redis.set(self.CURRENT_TASK_KEY, task_id)

    def cleanup(self):
        """Removes all state-related data from persistent storage. In other
        words, CloudFerry 'forgets' all the tasks it executed previously."""
        all_tasks = self.get_history()

        self.redis.delete(all_tasks, self.TASKS, self.CURRENT_TASK_KEY)

    def already_started(self, task_id):
        task_id = task_key(task_id)
        return self.redis.hget(task_id, 'state') not in [TASK_NOT_STARTED,
                                                         None]

    def build_schedule(self, migration, namespace):
        """
        Fills DB with scenario information on start following the algorithm
        below:

        if scenario changed since previous run:
            remove previous scenario from DB
            write new scenario to DB
        else:
            fast forward scenario to CURRENT_STATE
            update namespace with values from previous run
        """
        history = self.get_history()
        new_scenario = [TaskState.from_dict({'name': str(task), 'id': i})
                        for i, task in enumerate(migration)]

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
                td = TaskState.from_dict({'name': str(task), 'id': i})
                pipe.hmset(tk, td.to_db_record())
                pipe.rpush(self.TASKS, tk)
            pipe.execute()
            migration.to_start()
        else:
            current_task_id = int(self.redis.get(self.CURRENT_TASK_KEY))
            migration.fast_forward_to(current_task_id)
            current_task = TaskState.from_db_record(
                self.redis.hgetall(task_key(current_task_id)))
            current_task_ns = current_task['namespace']
            if current_task and hasattr(namespace, 'vars') and current_task_ns:
                namespace.vars.update(current_task_ns)


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
