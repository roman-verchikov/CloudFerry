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

from fabric.api import task, env

from cloudferrylib.scheduler.namespace import Namespace
from cloudferrylib.scheduler import scheduler
from cloudferrylib.scheduler.scheduler import state
import cfglib
from cloudferrylib.utils import utils as utl
from cloudferrylib.utils import utils
from cloudferrylib.scheduler import scenario
from cloud import cloud_ferry
from cloud import grouping
from dry_run import chain
from condensation import process
env.forward_agent = True
env.user = 'root'
LOG = utl.get_log(__name__)


@task
def migrate(name_config=None, name_instance=None, debug=False,
            tasks_path=scenario.DEFAULT_TAKSKS,
            scenario_path=scenario.DEFAULT_SCENARIO):
    """
        :name_config - name of config yaml-file, example 'config.yaml'
    """
    if debug:
        utl.configure_logging("DEBUG")
    cfglib.collector_configs_plugins()
    cfglib.init_config(name_config)
    utils.init_singletones(cfglib.CONF)
    env.key_filename = cfglib.CONF.migrate.key_filename
    cloud = cloud_ferry.CloudFerry(cfglib.CONF)
    cloud.migrate(scenario.Scenario(path_tasks=tasks_path,
                                    path_scenario=scenario_path))


@task
def get_info(name_config, debug=False):
    if debug:
        utl.configure_logging("DEBUG")
    LOG.info("Init getting information")
    namespace = Namespace({'name_config': name_config})
    scheduler.configured_scheduler(namespace=namespace)


@task
def dry_run():
    chain.process_test_chain()


@task
def get_groups(name_config=None, group_file=None, cloud_id='src',
               validate_users_group=False):
    """
    Function to group VM's by any of those dependencies (f.e. tenants,
    networks, etc.).

    :param name_config: name of config ini-file, example 'config.ini',
    :param group_file: name of groups defined yaml-file, example 'groups.yaml',
    :param validate_users_group: Remove dublicate id's and check if valid
           VM id specified. Takes more time because of nova API multiple calls
    :return: yaml-file with tree-based groups defined based on grouping rules.
    """
    cfglib.collector_configs_plugins()
    cfglib.init_config(name_config)
    group = grouping.Grouping(cfglib.CONF, group_file, cloud_id)
    group.group(validate_users_group)


@task
def condense(name_config=None, debug=False):
    if debug:
        utl.configure_logging("DEBUG")
    cfglib.collector_configs_plugins()
    cfglib.init_config(name_config)
    process.process()


@task
def state_aware_scheduler_dry_run(config=None):
    """Runs very simple scenario using state aware scheduler. Useful for
    testing"""
    if config is None:
        LOG.info("Config is not provided, default values will be used")

    cfglib.init_config(config)
    cfglib.CONF.migrate.scheduler = ("cloudferrylib.scheduler.scheduler."
                                     "StateAwareScheduler")
    chain.process_test_chain()


@task
def state_show_current(config=None):
    """Prints the last run task"""
    cfglib.init_config(config)
    current_state = state.TaskStatePersistent().get_current_task()
    if current_state is None:
        print ('Migration either never started or all tasks in current '
               'scenario finished successfully. Use "fab '
               'state_restart_from_task" to restart from particular task or '
               'provide new scenario to start it from beginning.')
    else:
        print state.pretty_printer(current_state)


@task
def state_show_history(config=None):
    """Prints all previously executed tasks"""
    cfglib.init_config(config)
    task_state = state.TaskStatePersistent()
    history = task_state.get_history()
    current = task_state.get_current_task()
    print state.pretty_printer(history, current=current)


@task
def state_restart_from_task(task_id, config=None):
    """Changes task to NOT STARTED state, so that migration starts from this
       task upon next call"""
    cfglib.init_config(config)
    try:
        task_id = int(task_id)
        state.TaskStatePersistent().reset(task_id)
        LOG.info("The scenario will be restarted from {task} upon next "
                 "migrate. Use 'fab migrate' to restart.".format(task=task_id))
    except ValueError:
        LOG.error("Invalid task ID provided. Please use IDs provided with "
                  "'fab state_show_history'")


@task
def state_cleanup(config=None):
    """Removes all the state data from DB"""
    cfglib.init_config(config)
    LOG.info('Removing all CloudFerry state information')
    state.TaskStatePersistent().cleanup()


if __name__ == '__main__':
    migrate(None)
