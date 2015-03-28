CloudFerry
==========

CloudFerry is a tool for resources and workloads migration between OpenStack clouds. First of all CloudFerry tool
migrates cloud resources as tenants, users (preserving their passwords or generating new ones), roles, flavors and after
that, it transfers virtual workloads as instances with their own data (instance
image, root disk data, ephemeral drives, attached volumes) and network settings.


CloudFerry was tested on Openstack releases from Grizzly to Ice-House.
It supports migration process for clouds using any iSCSI-like mechanism for volumes or Ceph as a backend for
Cinder&Glance services, including case with Nova - Ephemeral volumes in Ceph.
Supported cases are listed below. Tool supports any iscsi-like mechanism for Cinder backend as for Cinder service with
LVM backend:


- 1) Source - Cinder (LVM), Glance (file) --> Destination - Cinder (LVM), Glance (file)
- 2) Source - Cinder & Glance (Ceph) --> Destination - Cinder (LVM), Glance (file)
- 3) Source - Cinder & Glance (Ceph) and Nova ephemeral volumes (Ceph) -->   Destination - Cinder (LVM), Glance (file)
- 4) Source - Cinder (LVM), Glance (file) --> Destination - Cinder & Glance (Ceph)
- 5) Source - Cinder (LVM), Glance (file) --> Destination - Cinder & Glance (Ceph) and Nova ephemeral volumes (Ceph)
- 6) Source - Cinder & Glance (Ceph) --> Destination - Cinder & Glance (Ceph)
- 7) Source - Cinder & Glance (Ceph) and Nova ephemeral volumes (Ceph) -->   Destination - Cinder & Glance (Ceph) and
Nova ephemeral volumes (Ceph)


Also CloudFerry can migrate instances, which were booted from bootable  volumes with the same storage backends as in
previous listed cases.


CloudFerry uses External network as a transfer network, so you need to have a connectivity from host where you want
to execute the tool (transition zone) to both clouds through external network.
CloudFerry can migrate instances from clouds with nova-network or quantum/neutron to new cloud with neutron network
service. Also the tool can transfer instances in to the fixed networks with the same CIDR (they will be found
automatically) or list new networks for instances in config.yaml file in overwrite section.


Cloudferry also allow keep ip addresses of instances and transfer security groups (with rules) with automatic detection
of network manager on source and destination clouds (quantum/neutron or nova).


All functions are configured in yaml file, you can see the examples in configs directory.
At the moment config file does not support default values so you need to set up all settings manually. Please note,
that if any valuable setting appeared to be missing in config file, process will crash. Default settings is planned
to implement in nearest future.


## Requirements


- Connection to source and destination clouds through external(public) network from host with CloudFerry.
- Valid private ssh-key for both clouds which will be using by CloudFerry for data transferring.
- Credentials of global cloud admin for both clouds.
- All the Python requirements are listed in requirements.txt.


## Usage

### Migration

#### Scenario-based

Following command starts migration process:
```
fab migrate:configs/config_iscsi_to_iscsi.yaml
```
Arguments:
 - `migrate`: migration process start command;
 - `configs/config_iscsi_to_iscsi.yaml`: path to config file

#### Single VM
Starts migration of a given instance `<instance_name>`
```
fab migrate:configs/config_iscsi_to_iscsi.yaml,name_instance=<instance_name>
```

### State

#### Overview
CloudFerry supports different kinds of internal scheduler: stateless and 
state-aware. These can be chosen through modifying configuration:
```
[migrate]
# default scheduler is stateless:
scheduler = 'cloudferrylib.scheduler.scheduler.Scheduler'

# to choose stateful scheduler:
scheduler = "cloudferrylib.scheduler.scheduler.StateAwareScheduler"
```

State is stored in Redis DB, which requires that user has redis server running
at `<config.database.host>`:`<config.database.port>`.

State-aware scheduler:
  - Scans provided scenario for tasks to be executed;
  - Tracks execution state of each task;
  - Allows user to 'rewind' or 'fast-forward' to a particular task from scenario

#### Usage

 - All state-related commands start with 'state\_' prefix;
 - Show state of tasks in current scenario:
   ```
   fab state_show_history[:config]
   ```
 - Show currently executed task:
   ```
   fab state_show_current[:config]
   ```
 - Reset current task to any other:
   ```
   fab state_restart_from_task:<task_id>[:config]
   ```
 - Remove all state information:
   ```
   fab state_cleanup[:config]
   ```

## Config description

TODO