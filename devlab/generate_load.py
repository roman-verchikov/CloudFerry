# Copyright 2015 Mirantis Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import ConfigParser
import argparse
import itertools
import json
import logging
import os
import time
from logging.config import dictConfig

import yaml
from keystoneclient import exceptions as ks_exceptions
from neutronclient.common import exceptions as nt_exceptions
from novaclient import exceptions as nv_exceptions

import devlab.tests.config as conf
from devlab.tests import base
from devlab.tests import cleanup

dictConfig(conf.logging_configuration)
LOG = logging.getLogger(__name__)
TIMEOUT = 600
VM_SPAWNING_LIMIT = 5
CREATE_CLEAN_METHODS_MAP = {
    'create_tenants': 'clean_tenants',
    'create_users': 'clean_users',
    'create_roles': 'clean_roles',
    'create_flavors': 'clean_flavors',
    'create_all_networking': 'clean_all_networking'
}


def clean_if_exists(func):
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except (ks_exceptions.Conflict,
                nv_exceptions.Conflict,
                nt_exceptions.NeutronClientException):
            LOG.warning('Method "%s" failed, current resource already exists:',
                        func.__name__, exc_info=True)
            clean_method = getattr(self.clean_tools,
                                   CREATE_CLEAN_METHODS_MAP[func.__name__])
            LOG.info('Run cleanup method "%s"', clean_method.__name__)
            clean_method()
            LOG.info('Run method "%s" one more time', func.__name__)
            func(self, *args, **kwargs)
    return wrapper


def is_flavor_public(flavor):
    return flavor.get('is_public', True)


class Prerequisites(base.BasePrerequisites):

    def __init__(self, config, configuration_ini, cloud_prefix='SRC'):
        super(Prerequisites, self).__init__(config,
                                            configuration_ini,
                                            cloud_prefix)
        # will be filled during create all networking step
        self.ext_net_id = None
        # object of Prerequisites for dst cloud
        self.dst_cloud = None
        self.clean_tools = cleanup.CleanEnv(config, configuration_ini,
                                            cloud_prefix)

    @clean_if_exists
    def create_users(self, users=None):
        def get_params_for_user_creating(_user):
            if 'tenant' in _user:
                _user['tenant_id'] = self.get_tenant_id(_user['tenant'])
            params = ['name', 'password', 'email', 'enabled', 'tenant_id']
            return {param: _user[param] for param in params if param in _user}

        if users is None:
            users = self.config.users
        for user in users:
            self.keystoneclient.users.create(
                **get_params_for_user_creating(user))
            if not user.get('additional_tenants'):
                continue
            for tenant in user['additional_tenants']:
                self.keystoneclient.roles.add_user_role(
                    tenant=self.get_tenant_id(tenant['name']),
                    role=self.get_role_id(tenant['role']),
                    user=self.get_user_id(user['name']))
            # Next action for default sec group creation
            if not user['enabled'] or 'tenant' not in user:
                continue
            self.switch_user(user=user['name'], password=user['password'],
                             tenant=user['tenant'])
            self.novaclient.security_groups.list()
            self.switch_user(user=self.username, password=self.password,
                             tenant=self.tenant)

    @clean_if_exists
    def create_roles(self):
        for role in self.config.roles:
            self.keystoneclient.roles.create(name=role['name'])

    def create_user_tenant_roles(self, user_tenant_roles=None):
        if user_tenant_roles is None:
            user_tenant_roles = self.config.user_tenant_roles
        for user_roles in user_tenant_roles:
            for user, roles in user_roles.iteritems():
                user = self.get_user_id(user)
                for role in roles:
                    try:
                        self.keystoneclient.roles.add_user_role(
                            user=user, role=self.get_role_id(role['role']),
                            tenant=self.get_tenant_id(role['tenant']))
                    except ks_exceptions.Conflict:
                        LOG.warning("There was an error during role creating:",
                                    exc_info=True)
                        continue

    @clean_if_exists
    def create_tenants(self, tenants=None):
        if tenants is None:
            tenants = self.config.tenants
        for tenant in tenants:
            self.keystoneclient.tenants.create(tenant_name=tenant['name'],
                                               description=tenant[
                                                   'description'],
                                               enabled=tenant['enabled'])
            self.keystoneclient.roles.add_user_role(
                self.get_user_id(self.username),
                self.get_role_id('admin'),
                self.get_tenant_id(tenant['name']))

    def create_keypairs(self):
        for keypair in self.config.keypairs:
            for _user in self.config.users:
                if _user['name'] == keypair['user']:
                    user = _user
                    break
            else:
                msg = 'User for keypair %s was not found'
                raise RuntimeError(msg % keypair['name'])
            self.switch_user(user=user['name'], password=user['password'],
                             tenant=user['tenant'])
            self.novaclient.keypairs.create(name=keypair['name'],
                                            public_key=keypair['public_key'])
        self.switch_user(user=self.username, password=self.password,
                         tenant=self.tenant)

    def modify_quotas(self):
        for tenant in self.config.tenants:
            if 'quota' in tenant:
                self.novaclient.quotas.update(tenant_id=self.get_tenant_id(
                    tenant['name']), **tenant['quota'])

    def upload_image(self):

        def _get_body_for_image_creating(_image):
            # Possible parameters for image creating
            params = ['name', 'location', 'disk_format', 'container_format',
                      'is_public', 'copy_from']
            return {param: _image[param] for param in params
                    if param in _image}

        img_ids = []
        for tenant in self.config.tenants:
            if not tenant.get('images'):
                continue
            for image in tenant['images']:

                self.switch_user(user=self.username, password=self.password,
                                 tenant=tenant['name'])

                img = self.glanceclient.images.create(
                    **_get_body_for_image_creating(image))

                img_ids.append(img.id)
        self.switch_user(user=self.username, password=self.password,
                         tenant=self.tenant)

        for image in self.config.images:
            img = self.glanceclient.images.create(
                **_get_body_for_image_creating(image))
            img_ids.append(img.id)
        self.wait_until_objects_created(img_ids, self.check_image_state,
                                        TIMEOUT)

        tenant_list = self.keystoneclient.tenants.list()
        for image_id in img_ids:
            if self.glanceclient.images.get(image_id).name in \
                    self.config.img_to_add_members:
                for tenant in tenant_list:
                    tenant = tenant.__dict__
                    if tenant['name'] in self.config.members:
                        member_id = tenant['id']
                        self.glanceclient.image_members.create(image_id,
                                                               member_id)

        if getattr(self.config, 'create_zero_image', None):
            self.glanceclient.images.create()

    def update_filtering_file(self):
        src_cloud = Prerequisites(cloud_prefix='SRC',
                                  configuration_ini=self.configuration_ini,
                                  config=self.config)
        src_img = [x.__dict__ for x in
                   src_cloud.glanceclient.images.list()]
        src_vms = [x.__dict__ for x in
                   src_cloud.novaclient.servers.list(
                       search_opts={'all_tenants': 1})]
        image_dict = {}
        for image in src_img:
            img_members = self.glanceclient.image_members.list(image['id'])
            if len(img_members) > 1:
                img_mem_list = []
                for img_member in img_members:
                    img_member = img_member.__dict__
                    img_mem_list.append(img_member['member_id'])
                image_dict[image['id']] = img_mem_list
        vm_id_list = []
        for vm in src_vms:
            vm_id = vm['id']
            vm_id_list.append(vm_id)
        filter_dict = {
            'tenants': {
                'tenant_id': []
            },
            'instances': {
                'id': []
            },
            'images': {
                'images_list': []
            }
        }
        all_img_ids = []
        not_incl_img = []
        for image in src_img:
            all_img_ids.append(image['id'])
        for img in self.config.images_not_included_in_filter:
            not_incl_img.append(self.get_image_id(img))
        for key in filter_dict.keys():
            if key == 'images':
                for img_id in all_img_ids:
                    if img_id not in not_incl_img:
                        filter_dict[key]['images_list'].append(str(img_id))
            elif key == 'instances':
                for vm in vm_id_list:
                    if vm != self.get_vm_id('not_in_filter'):
                        filter_dict[key]['id'].append(str(vm))
        for tenant in self.config.tenants + [{'name': self.tenant}]:
            if tenant.get('deleted'):
                continue
            filter_dict['tenants']['tenant_id'] = [str(
                self.get_tenant_id(tenant['name']))]
            file_path = self.config.filters_file_naming_template.format(
                tenant_name=tenant['name'])
            file_path = base.get_abs_path(file_path)
            with open(file_path, "w") as f:
                yaml.dump(filter_dict, f, default_flow_style=False)

    @clean_if_exists
    def create_flavors(self, flavor_list=None):
        def _create_flavors(flavors, ten_id):
            for flavor in flavors:
                if flavor.get('is_deleted'):
                    flavor.pop('is_deleted')
                    fl = self.novaclient.flavors.create(**flavor)
                    self.novaclient.flavors.delete(fl.id)
                else:
                    fl = self.novaclient.flavors.create(**flavor)
                if not is_flavor_public(flavor):
                    self.novaclient.flavor_access.add_tenant_access(
                        flavor=fl.id, tenant=ten_id)

        if flavor_list is not None:
            _create_flavors(flavor_list, self.get_tenant_id(self.tenant))
            return
        _create_flavors(self.config.flavors, self.get_tenant_id(self.tenant))
        for tenant in self.config.tenants:
            if tenant.get('flavors'):
                tenant_id = self.get_tenant_id(tenant['name'])
                _create_flavors(tenant['flavors'], tenant_id)

    def _get_parameters_for_vm_creating(self, vm):
        def get_vm_nics(_vm):
            if 'nics' in _vm:
                for _nic in _vm['nics']:
                    _nic['net-id'] = self.get_net_id(_nic['net-id'])
                return _vm['nics']
            nets = self.neutronclient.list_networks()['networks']
            _t = self.keystoneclient.tenant_id
            nics = [{'net-id': net['id']} for net in nets
                    if not net['router:external'] and net['tenant_id'] == _t]
            return nics
        image_id = self.get_image_id(vm['image']) if vm.get('image') else ''
        params = {'image': image_id,
                  'flavor': self.get_flavor_id(vm['flavor']),
                  'nics': get_vm_nics(vm),
                  'name': vm['name'],
                  'key_name': vm.get('key_name')}
        if 'server_group' in vm and self.server_groups_supported:
            params['scheduler_hints'] = {'group': self.get_server_group_id(
                vm['server_group'])}
        return params

    def create_server_groups(self):
        def _create_groups(server_groups_list):
            for server_group in server_groups_list:
                self.novaclient.server_groups.create(**server_group)
        # Create server group for admin tenant
        _create_groups(self.config.server_groups)
        for tenant in self.config.tenants:
            if not tenant.get('server_groups'):
                continue
            self.switch_user(self.username, self.password, tenant['name'])
            _create_groups(tenant['server_groups'])
        self.switch_user(self.username, self.password, self.tenant)

    def create_vms(self, vm_list):

        def wait_for_vm_creating():
            """ When limit for creating vms in nova is reached, we receive
                exception from nova: 'novaclient.exceptions.OverLimit:
                This request was rate-limited. (HTTP 413)'. To handle this we
                set limit for vm spawning.
            """
            for _ in range(TIMEOUT):
                all_vms = self.novaclient.servers.list(
                    search_opts={'all_tenants': 1})
                spawning_vms = [vm.id for vm in all_vms
                                if vm.status == 'BUILD']
                if len(spawning_vms) >= VM_SPAWNING_LIMIT:
                    time.sleep(1)
                else:
                    break
            else:
                raise RuntimeError(
                    'VMs with ids {0} were in "BUILD" state more than {1} '
                    'seconds'.format(spawning_vms, TIMEOUT))

        vm_ids = []
        for vm in vm_list:
            wait_for_vm_creating()
            _vm = self.novaclient.servers.create(
                **self._get_parameters_for_vm_creating(vm))
            vm_ids.append(_vm.id)
            if not vm.get('fip'):
                continue
            self.wait_until_objects_created([_vm.id], self.check_vm_state,
                                            TIMEOUT)
            fip = self.neutronclient.create_floatingip(
                {"floatingip": {"floating_network_id": self.ext_net_id}})
            _vm.add_floating_ip(fip['floatingip']['floating_ip_address'])
        return vm_ids

    def create_all_vms(self):

        vms = self.create_vms(self.config.vms)
        for tenant in self.config.tenants:
            if not tenant.get('vms'):
                continue

            # To create vm with proper keypair, need to switch to right user
            keypairs = set([vm['key_name'] for vm in tenant['vms']
                            if vm.get('key_name')])
            # Split all vms on with and without keypair
            keypairs_vms = {keypair: [] for keypair in keypairs}
            vms_wo_keypairs = []
            for vm in tenant['vms']:
                if vm.get('key_name'):
                    keypairs_vms[vm['key_name']].append(vm)
                else:
                    vms_wo_keypairs.append(vm)
            for keypair in keypairs_vms:
                username = [kp['user'] for kp in self.config.keypairs
                            if kp['name'] == keypair][0]
                user = [user for user in self.config.users
                        if user['name'] == username][0]
                if user['tenant'] != tenant['name']:
                    msg = 'Keypair "{0}" not accessible from tenant "{1}"'
                    raise RuntimeError(msg.format(keypair[0], tenant['name']))
                self.switch_user(user=user['name'], password=user['password'],
                                 tenant=tenant['name'])
                vms.extend(self.create_vms(keypairs_vms[keypair]))
            # Create vms without keypair
            user = [u for u in self.config.users
                    if u.get('tenant') == tenant['name']][0]
            self.switch_user(user=user['name'], password=user['password'],
                             tenant=tenant['name'])
            vms.extend(self.create_vms(vms_wo_keypairs))
        self.switch_user(user=self.username, password=self.password,
                         tenant=self.tenant)

        self.wait_until_objects_created(vms, self.check_vm_state, TIMEOUT)

    def create_vm_snapshots(self):

        snp_ids = []
        for snapshot in self.config.snapshots:
            self.novaclient.servers.create_image(
                server=self.get_vm_id(snapshot['server']),
                image_name=snapshot['image_name'])
            snp = self.glanceclient.images.get(self.get_image_id(
                snapshot['image_name']))
            snp_ids.append(snp.id)
        self.wait_until_objects_created(snp_ids, self.check_snapshot_state,
                                        TIMEOUT)

    def create_networks(self, networks):

        def get_body_for_network_creating(_net):
            # Possible parameters for network creating
            params = ['name', 'admin_state_up', 'shared', 'router:external',
                      'provider:network_type', 'provider:segmentation_id',
                      'provider:physical_network', 'tenant_id']
            return {param: _net[param] for param in params if param in _net}

        def get_body_for_subnet_creating(_subnet):
            # Possible parameters for subnet creating
            params = ['name', 'cidr', 'allocation_pools', 'dns_nameservers',
                      'host_routes', 'ip_version', 'network_id', 'tenant_id']
            return {param: _subnet[param] for param in params
                    if param in _subnet}

        for network in networks:
            net = self.neutronclient.create_network(
                {'network': get_body_for_network_creating(network)})
            for subnet in network['subnets']:
                subnet['network_id'] = net['network']['id']
                _subnet = self.neutronclient.create_subnet(
                    {'subnet': get_body_for_subnet_creating(subnet)})
                self.neutronclient.create_port(
                    {"port": {"network_id": net['network']['id']}})
                # If network has attribute routers_to_connect, interface to
                # this network is created for given router.
                # If network has attribute set_as_gateway_for_routers, it will
                # be set as router's gateway.
                if subnet.get('routers_to_connect') is not None:
                    for router in subnet['routers_to_connect']:
                        router_id = self.get_router_id(router)
                        self.neutronclient.add_interface_router(
                            router_id, {"subnet_id": _subnet['subnet']['id']})
                if not network.get('router:external') or \
                        subnet.get('set_as_gateway_for_routers') is None:
                    continue
                routers = subnet['set_as_gateway_for_routers']
                for router, gw_info in routers.iteritems():
                    router_id = self.get_router_id(router)
                    parameters = {"network_id": net['network']['id']}
                    if self.openstack_release in ['icehouse', 'juno'] and \
                            gw_info.get('enable_snat') is not None:
                        parameters['enable_snat'] = gw_info.get('enable_snat')
                    self.neutronclient.add_gateway_router(
                        router_id, parameters)

    def create_routers(self):
        for router in self.config.routers:
            self.neutronclient.create_router(router)
        for tenant in self.config.tenants:
            if tenant.get('routers'):
                self.switch_user(user=self.username, password=self.password,
                                 tenant=tenant['name'])
                for router in tenant['routers']:
                    self.neutronclient.create_router(router)
        self.switch_user(user=self.username, password=self.password,
                         tenant=self.tenant)

    def get_subnet_id(self, name):
        subs = self.neutronclient.list_subnets()['subnets']
        for sub in subs:
            if sub['name'] == name:
                return sub['id']

    def create_unassociated_fips(self, neutronclient, fips_count, net_id):
        for _ in range(fips_count):
            neutronclient.create_floatingip(
                    {"floatingip": {"floating_network_id": net_id}})

    def get_pool_id(self, name):
        pools = self.neutronclient.list_pools()['pools']
        for pool in pools:
            if pool['name'] == name:
                return pool['id']

    def create_pools(self, pools):
        for pool in pools:
            pool["tenant_id"] = self.get_tenant_id(pool["tenant_name"])
            pool["subnet_id"] = self.get_subnet_id(pool["subnet_name"])
            pool = {i: v for i, v in pool.iteritems()
                    if i not in ["tenant_name", "subnet_name"]}
            self.neutronclient.create_pool({'pool': pool})

    def create_members(self, members):
        for member in members:
            member["pool_id"] = self.get_pool_id(member["pool_name"])
            member["tenant_id"] = self.get_tenant_id(member["tenant_name"])
            member = {i: v for i, v in member.iteritems()
                      if i not in ["pool_name", "tenant_name"]}
            self.neutronclient.create_member({"member": member})

    def create_monitors(self, monitors):
        for mon in monitors:
            mon["tenant_id"] = self.get_tenant_id(mon["tenant_name"])
            mon = {i: v for i, v in mon.iteritems()
                   if i not in ["tenant_name"]}
            self.neutronclient.create_health_monitor({"health_monitor": mon})

    def create_vips(self, vips):
        for vip in vips:
            vip["pool_id"] = self.get_pool_id(vip["pool_name"])
            vip["tenant_id"] = self.get_tenant_id(vip["tenant_name"])
            vip["subnet_id"] = self.get_subnet_id(vip["subnet_name"])
            vip = {i: v for i, v in vip.iteritems()
                   if i not in ["tenant_name", "pool_name", "subnet_name"]}
            self.neutronclient.create_vip({"vip": vip})

    @clean_if_exists
    def create_all_networking(self):
        self.create_routers()
        self.create_networks(self.config.networks)
        # Getting ip address for real network. This networks will be used to
        # allocation floating ips.
        self.ext_net_id = self.get_net_id(
            [n['name'] for n in self.config.networks
             if n.get('real_network')][0])
        self.create_pools(self.config.pools)
        self.create_members(self.config.members_lbaas)
        self.create_monitors(self.config.monitors)
        self.create_vips(self.config.vips)

        for tenant in self.config.tenants:
            if tenant.get('networks'):
                self.switch_user(user=self.username, password=self.password,
                                 tenant=tenant['name'])
                self.create_networks(tenant['networks'])
            if tenant.get('pools'):
                self.create_pools(tenant['pools'])
            if tenant.get('members_lbaas'):
                self.create_members(tenant['members_lbaas'])
            if tenant.get('monitors'):
                self.create_monitors(tenant['monitors'])
            if tenant.get('vips'):
                self.create_vips(tenant['vips'])
            if tenant.get('unassociated_fip'):
                self.create_unassociated_fips(self.neutronclient,
                                              tenant.get('unassociated_fip'),
                                              self.ext_net_id)
        self.switch_user(user=self.username, password=self.password,
                         tenant=self.tenant)

    def create_security_group(self, sg_list):
        for security_group in sg_list:
            gid = self.novaclient.security_groups.create(
                name=security_group['name'],
                description=security_group['description']).id
            if 'rules' in security_group:
                for rule in security_group['rules']:
                    self.novaclient.security_group_rules.create(
                        gid,
                        ip_protocol=rule['ip_protocol'],
                        from_port=rule['from_port'], to_port=rule['to_port'],
                        cidr=rule['cidr'])

    def create_security_groups(self):
        for tenant in self.config.tenants:
            if 'security_groups' in tenant:
                self.switch_user(user=self.username, password=self.password,
                                 tenant=tenant['name'])
                self.create_security_group(tenant['security_groups'])
        self.switch_user(user=self.username, password=self.password,
                         tenant=self.tenant)

    def create_security_group_rule(self, group_id, tenant_id, protocol='tcp',
                                   port_range_min=22, port_range_max=22,
                                   direction='ingress'):
        sec_rule = {'security_group_rule': {"security_group_id": group_id,
                                            "protocol": protocol,
                                            "direction": direction,
                                            'tenant_id': tenant_id,
                                            "port_range_min": port_range_min,
                                            "port_range_max": port_range_max}}
        self.neutronclient.create_security_group_rule(sec_rule)

    def create_cinder_volumes(self, volumes_list):

        def wait_until_vms_with_fip_accessible(_vm_id):
            vm = self.novaclient.servers.get(_vm_id)
            self.migration_utils.open_ssh_port_secgroup(self, vm.tenant_id)
            try:
                fip_addr = self.migration_utils.get_vm_fip(vm)
            except RuntimeError:
                return
            self.migration_utils.wait_until_vm_accessible_via_ssh(fip_addr)

        def get_params_for_volume_creating(_volume):
            params = ['display_name', 'size', 'imageRef', 'metadata']
            vt_exists = 'volume_type' in _volume and \
                [vt for vt in self.cinderclient.volume_types.list()
                 if vt.name == _volume['volume_type']]
            if vt_exists:
                params.append('volume_type')
            if 'image' in _volume:
                _volume['imageRef'] = self.get_image_id(_volume['image'])
            return {param: _volume[param] for param in params
                    if param in _volume}

        vlm_ids = []
        for volume in volumes_list:
            if 'user' in volume:
                user = [u for u in self.config.users
                        if u['name'] == volume['user']][0]
                self.switch_user(user=user['name'], password=user['password'],
                                 tenant=user['tenant'])
            vlm = self.cinderclient.volumes.create(
                **get_params_for_volume_creating(volume))
            vlm_ids.append(vlm.id)
        self.switch_user(user=self.username, password=self.password,
                         tenant=self.tenant)
        self.wait_until_objects_created(vlm_ids, self.check_volume_state,
                                        TIMEOUT)
        vlm_ids = []
        for volume in volumes_list:
            if 'server_to_attach' not in volume:
                continue
            vlm_id = self.get_volume_id(volume['display_name'])
            vm_id = self.get_vm_id(volume['server_to_attach'])
            # To correct attaching volume, vm should be fully ready
            wait_until_vms_with_fip_accessible(vm_id)
            self.novaclient.volumes.create_server_volume(
                server_id=vm_id, volume_id=vlm_id, device=volume['device'])
            vlm_ids.append(vlm_id)
        self.wait_until_objects_created(vlm_ids, self.check_volume_state,
                                        TIMEOUT)

    def create_cinder_snapshots(self, snapshot_list):
        for snapshot in snapshot_list:
            self.cinderclient.volume_snapshots.create(**snapshot)

    def create_cinder_objects(self):
        self.create_cinder_volumes(self.config.cinder_volumes)
        self.create_cinder_snapshots(self.config.cinder_snapshots)
        for tenant in self.config.tenants:
            if 'cinder_volumes' in tenant:
                self.switch_user(user=self.username, password=self.password,
                                 tenant=tenant['name'])
                self.create_cinder_volumes(tenant['cinder_volumes'])
                if 'cinder_snapshots' in tenant:
                    self.create_cinder_snapshots(tenant['cinder_snapshots'])
        self.switch_user(user=self.username, password=self.password,
                         tenant=self.tenant)

    def write_data_to_volumes(self):
        """Method creates file and md5sum of this file on volume
        """
        volumes = self.config.cinder_volumes
        volumes += itertools.chain(*[tenant['cinder_volumes'] for tenant
                                     in self.config.tenants if 'cinder_volumes'
                                     in tenant])
        for volume in volumes:
            attached_volume = volume.get('server_to_attach')
            if not volume.get('write_to_file') or not attached_volume:
                continue
            if not volume.get('mount_point'):
                msg = 'Please specify mount point for volume %s'
                raise RuntimeError(msg % volume['name'])

            vm = self.novaclient.servers.get(
                self.get_vm_id(volume['server_to_attach']))
            vm_ip = self.migration_utils.get_vm_fip(vm)
            # Make filesystem on volume. The OS assigns the volume to the next
            # available device, which /dev/vda
            cmd = '/usr/sbin/mkfs.ext2 /dev/vdb'
            self.migration_utils.execute_command_on_vm(vm_ip, cmd)
            # Create directory for mount point
            cmd = 'mkdir -p %s' % volume['mount_point']
            self.migration_utils.execute_command_on_vm(vm_ip, cmd)
            # Mount volume
            cmd = 'mount {0} {1}'.format(volume['device'],
                                         volume['mount_point'])
            self.migration_utils.execute_command_on_vm(vm_ip, cmd)
            for _file in volume['write_to_file']:
                _path, filename = os.path.split(_file['filename'])
                path = '{0}/{1}'.format(volume['mount_point'], _path)
                if _path:
                    cmd = 'mkdir -p {path}'.format(path=path)
                    self.migration_utils.execute_command_on_vm(vm_ip, cmd)
                cmd = 'sh -c "echo \'{content}\' > {path}/{filename}"'
                self.migration_utils.execute_command_on_vm(vm_ip, cmd.format(
                    path=path, content=_file['data'], filename=filename))
                cmd = 'sh -c "md5sum {path}/{_file} > {path}/{_file}_md5"'
                self.migration_utils.execute_command_on_vm(vm_ip, cmd.format(
                    path=path, _file=filename))

    def create_invalid_cinder_objects(self):
        invalid_volume_tmlt = 'cinder_volume_%s'
        volumes = [
            {
                'display_name': invalid_volume_tmlt % st,
                'size': 1,
            }
            for st in self.config.INVALID_STATUSES
        ]
        existing = [vol.display_name
                    for vol in self.cinderclient.volumes.list(
                        search_opts={'all_tenants': 1})]
        volumes = [vol
                   for vol in volumes if vol['display_name'] not in existing]
        if volumes:
            self.create_cinder_volumes(volumes)
        for st in self.config.INVALID_STATUSES:
            vol = self.cinderclient.volumes.find(
                display_name=invalid_volume_tmlt % st)
            self.cinderclient.volumes.reset_state(vol, state=st)

    def emulate_vm_states(self):
        for vm_state in self.config.vm_states:
            # emulate error state:
            if vm_state['state'] == u'error':
                self.novaclient.servers.reset_state(
                    server=self.get_vm_id(vm_state['name']),
                    state=vm_state['state'])
            # emulate suspend state:
            elif vm_state['state'] == u'suspend':
                self.novaclient.servers.suspend(self.get_vm_id(
                    vm_state['name']))
            # emulate resize state:
            elif vm_state['state'] == u'pause':
                self.novaclient.servers.pause(self.get_vm_id(vm_state['name']))
            # emulate stop/shutoff state:
            elif vm_state['state'] == u'stop':
                self.novaclient.servers.stop(self.get_vm_id(vm_state['name']))
            # emulate resize state:
            elif vm_state['state'] == u'resize':
                self.novaclient.servers.resize(
                    self.get_vm_id(vm_state['name']), '2')

    def generate_vm_state_list(self):
        data = {}
        for vm in self.novaclient.servers.list(search_opts={'all_tenants': 1}):
            for _ in range(TIMEOUT):
                _vm = self.novaclient.servers.get(vm.id)
                if _vm.status != u'RESIZE':
                    break
                time.sleep(1)
            vm_state = self.novaclient.servers.get(vm.id).status
            data[vm.name] = vm_state

        file_path = conf.pre_migration_vm_states_file
        file_path = base.get_abs_path(file_path)
        with open(file_path, 'w') as outfile:
            json.dump(data, outfile, sort_keys=True, indent=4,
                      ensure_ascii=False)

    def delete_flavor(self, flavor='del_flvr'):
        """
        Method for flavor deletion.
        """
        try:
            self.novaclient.flavors.delete(
                self.get_flavor_id(flavor))
        except nv_exceptions.ClientException:
            LOG.warning("Flavor %s failed to delete:", flavor, exc_info=True)

    def update_network_quotas(self):
        tenants = {ten.name: ten.id
                   for ten in self.keystoneclient.tenants.list()}
        for tenant in self.config.tenants:
            if "quota_network" not in tenant:
                continue
            ten_id = tenants[tenant["name"]]
            quota_net = tenant["quota_network"]
            self.neutronclient.update_quota(ten_id,
                                            {"quota": quota_net})

    def modify_admin_tenant_quotas(self):
        for tenant in self.config.tenants:
            if 'quota' in tenant:
                self.novaclient.quotas.update(tenant_id=self.get_tenant_id(
                    self.tenant), **tenant['quota'])
                break

    def change_admin_role_in_tenants(self):
        for tenant in self.config.tenants:
            self.keystoneclient.roles.remove_user_role(
                self.get_user_id(self.username),
                self.get_role_id('admin'),
                self.get_tenant_id(tenant['name']))
            self.switch_user(self.username, self.password, self.tenant)

    def delete_users(self):
        for user in self.config.users:
            if user.get('deleted'):
                self.keystoneclient.users.delete(
                    self.get_user_id(user['name']))

    def delete_tenants(self):
        for tenant in self.config.tenants:
            if tenant.get('deleted'):
                self.keystoneclient.tenants.delete(
                    self.get_tenant_id(tenant['name']))

    def create_tenant_wo_sec_group_on_dst(self):
        """
        Method for check fixed issue, when on dst tenant does not have
        security group, even default, while on src this tenant has security
        group.
        """
        self.dst_cloud = Prerequisites(
            cloud_prefix='DST',
            configuration_ini=self.configuration_ini,
            config=self.config)
        for t in self.config.tenants:
            if not t.get('deleted') and t['enabled']:
                try:
                    self.dst_cloud.keystoneclient.tenants.create(
                        tenant_name=t['name'], description=t['description'],
                        enabled=t['enabled'])
                except ks_exceptions.Conflict:
                    pass

    def create_user_on_dst(self):
        """
        Method for check fixed issue, when on dst and src exists user with
        same user tenant role.
        1. Get one user tenant role from config
        2. Get user from config, which should be created
        3. Create roles, which specified in user tenant roles
        4. Get tenants, in which user has roles and tenant which the user
         belongs
        5. Create tenants
        6. Create user
        7. Create user tenant roles
        """
        user_tenant_role = self.config.user_tenant_roles[0]
        username, roles_to_create = user_tenant_role.items()[0]
        user = [user for user in self.config.users
                if username == user['name']][0]
        tenants_names = [user['tenant']]
        for role in roles_to_create:
            try:
                self.dst_cloud.keystoneclient.roles.create(name=role['role'])
            except ks_exceptions.Conflict:
                LOG.warning("There was an error during role creating on dst:",
                            exc_info=True)
                continue
            if role['tenant'] not in tenants_names:
                tenants_names.append(role['tenant'])

        tenants_to_create = [t for t in self.config.tenants
                             if t['name'] in tenants_names and
                             not self.dst_cloud.tenant_exists(t['name'])]
        self.dst_cloud.create_tenants(tenants_to_create)
        self.dst_cloud.create_users([user])
        self.dst_cloud.create_user_tenant_roles([user_tenant_role])

    def create_volumes_from_images(self):
        self.create_cinder_volumes(self.config.cinder_volumes_from_images)

    def boot_vms_from_volumes(self):
        for vm in self.config.vms_from_volumes:
            params = self._get_parameters_for_vm_creating(vm)
            params['block_device_mapping_v2'] = []
            params['block_device_mapping_v2'].append(
                {'source_type': 'volume',
                 'delete_on_termination': False,
                 'boot_index': 0,
                 'uuid': self.get_volume_id(vm['volume']),
                 'destination_type': 'volume'}
            )
            self.novaclient.servers.create(**params)

    def break_vm(self):
        """ Method delete vm via virsh to emulate situation, when vm is valid
        and active in nova db, but in fact does not exist
        """
        vms_to_break = []
        for vm in self.migration_utils.get_all_vms_from_config():
            if vm.get('broken'):
                vms_to_break.append(self.get_vm_id(vm['name']))

        for vm in vms_to_break:
            inst_name = getattr(self.novaclient.servers.get(vm),
                                'OS-EXT-SRV-ATTR:instance_name')
            cmd = 'virsh destroy {0} && virsh undefine {0}'.format(inst_name)
            self.migration_utils.execute_command_on_vm(
                self.get_vagrant_vm_ip(), cmd, username='root', password='')

    def break_images(self):
        all_images = self.migration_utils.get_all_images_from_config()
        images_to_break = [image for image in all_images
                           if image.get('broken')]
        images_to_delete = [image for image in all_images
                            if image.get('is_deleted')]
        for image in images_to_break:
            image_id = self.get_image_id(image['name'])
            cmd = 'rm -rf /var/lib/glance/images/%s' % image_id
            self.migration_utils.execute_command_on_vm(
                self.get_vagrant_vm_ip(), cmd, username='root', password='')
        for image in images_to_delete:
            image_id = self.get_image_id(image['name'])
            self.glanceclient.images.delete(image_id)

    def create_dst_networking(self):
        if not self.dst_cloud:
            self.dst_cloud = Prerequisites(
                cloud_prefix='DST',
                configuration_ini=self.configuration_ini,
                config=self.config)
        self.dst_cloud.switch_user(user=self.dst_cloud.username,
                                   password=self.dst_cloud.password,
                                   tenant=self.dst_cloud.tenant)
        self.dst_cloud.create_networks(self.config.dst_networks)
        for net in self.config.dst_networks:
            if net.get('real_network'):
                ext_net_id = self.dst_cloud.get_net_id(net.get('name'))
                self.create_unassociated_fips(self.dst_cloud.neutronclient,
                                              self.config.dst_unassociated_fip,
                                              ext_net_id)
        dst_tenants = [t for t in self.config.tenants
                       if t.get('exists_on_dst')]
        for tenant in dst_tenants:
            tenant_id = self.dst_cloud.get_tenant_id(tenant['name'])
            for net in tenant['networks']:
                net['tenant_id'] = tenant_id
                for subnet in net['subnets']:
                    subnet['tenant_id'] = tenant_id
            self.dst_cloud.create_networks(tenant['networks'])

    def create_vms_on_dst(self):
        def get_user_for_tenant(tenant_name):
            for user in self.config.users:
                if user.get('tenant') == tenant_name:
                    return user
            raise RuntimeError(
                'User for tenant %s was not found' % tenant_name)

        tenants = {}
        for vm in self.config.dst_vms:
            if vm['tenant'] in tenants:
                tenants[vm['tenant']].append(vm)
            else:
                tenants[vm['tenant']] = [vm]

        img_names = [vm['image'] for vm in self.config.dst_vms]
        img_ids = [self.dst_cloud.glanceclient.images.create(**img).id
                   for img in self.config.images if img['name'] in img_names]
        self.wait_until_objects_created(
            img_ids, self.dst_cloud.check_image_state, TIMEOUT)
        flv_names = {vm['flavor'] for vm in self.config.dst_vms}
        flavors = [f for f in self.config.flavors if f['name'] in flv_names]
        self.dst_cloud.create_flavors(flavors)

        for tenant_name, vms in tenants.iteritems():
            user = get_user_for_tenant(tenant_name)
            self.dst_cloud.switch_user(
                user['name'], user['password'], user['tenant'])
            self.dst_cloud.create_vms(vms)

    def run_preparation_scenario(self):
        LOG.info('Creating tenants')
        self.create_tenants()
        LOG.info('Creating users')
        self.create_users()
        LOG.info('Creating roles')
        self.create_roles()
        LOG.info('Creating keypairs')
        self.create_keypairs()
        LOG.info('Modifying quotas')
        self.modify_quotas()
        LOG.info('Creating flavors')
        self.create_flavors()
        LOG.info('Uploading images')
        self.upload_image()
        LOG.info('Creating networking')
        self.create_all_networking()
        if self.openstack_release in ['icehouse', 'juno']:
            LOG.info('Creating server groups')
            self.create_server_groups()
            LOG.info('Create bootable volume from image')
            self.create_volumes_from_images()
            LOG.info('Boot vm from volume')
            self.boot_vms_from_volumes()
        LOG.info('Creating vms')
        self.create_all_vms()
        LOG.info('Breaking VMs')
        self.break_vm()
        LOG.info('Breaking Images')
        self.break_images()
        LOG.info('Updating filtering')
        self.update_filtering_file()
        LOG.info('Creating vm snapshots')
        self.create_vm_snapshots()
        LOG.info('Creating security groups')
        self.create_security_groups()
        LOG.info('Creating cinder objects')
        self.create_cinder_objects()
        LOG.info('Writing data into the volumes')
        self.write_data_to_volumes()
        LOG.info('Creating invalid cinder objects')
        self.create_invalid_cinder_objects()
        LOG.info('Emulating vm states')
        self.emulate_vm_states()
        LOG.info('Generating vm states list')
        self.generate_vm_state_list()
        LOG.info('Deleting flavor')
        self.delete_flavor()
        LOG.info('Modifying admin tenant quotas')
        self.modify_admin_tenant_quotas()
        LOG.info('Update network quotas')
        self.update_network_quotas()
        LOG.info('Change admin role in tenants')
        self.change_admin_role_in_tenants()
        LOG.info('Creating user tenant roles')
        self.create_user_tenant_roles()
        LOG.info('Delete users which should be deleted')
        self.delete_users()
        LOG.info('Delete tenants which should be deleted')
        self.delete_tenants()
        LOG.info('Create tenant on dst, without security group')
        self.create_tenant_wo_sec_group_on_dst()
        LOG.info('Create role on dst')
        self.create_user_on_dst()
        LOG.info('Creating networks on dst')
        self.create_dst_networking()
        LOG.info('Creating vms on dst:')
        self.create_vms_on_dst()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Script to generate load for Openstack and delete '
                    'generated objects')
    parser.add_argument('--clean', help='clean objects, described'
                                        ' in configuration.ini file',
                        action='store_true')
    parser.add_argument('--env', default='SRC',
                        help='choose cloud: SRC or DST')
    parser.add_argument('cloudsconf',
                        help='Please point configuration.ini file location')

    _args = parser.parse_args()
    confparser = ConfigParser.ConfigParser()
    confparser.read(_args.cloudsconf)
    cloudsconf = base.get_dict_from_config_file(confparser)

    preqs = Prerequisites(config=conf, configuration_ini=cloudsconf,
                          cloud_prefix=_args.env)
    if _args.clean:
        preqs.clean_tools.clean_objects()
    else:
        preqs.run_preparation_scenario()
