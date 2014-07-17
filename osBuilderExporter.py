import logging
from utils import forward_agent
from fabric.api import run, settings, env
from osVolumeTransfer import VolumeTransfer
from osImageTransfer import ImageTransfer
import time
import json

__author__ = 'mirrorcoder'

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.DEBUG)
hdlr = logging.FileHandler('exporter.log')
LOG.addHandler(hdlr)

DISK = "/disk"
LOCAL = ".local"
LEN_UUID_INSTANCE = 36


class osBuilderExporter:

    """
    The main class for gathering information from source cloud.
    data -- main dictionary for filling with information from source cloud
    """

    def __init__(self, glance_client, cinder_client, nova_client, network_client, instance, config):
        self.glance_client = glance_client
        self.cinder_client = cinder_client
        self.nova_client = nova_client
        self.network_client = network_client
        self.data = dict()
        self.instance = instance
        self.config = config

    def finish(self):
        return self.data

    def stop_instance(self):
        self.instance.stop()
        self.__wait_for_status(self.nova_client.servers, self.instance.id, 'SHUTOFF')
        return self

    def get_name(self):
        self.data['name'] = getattr(self.instance, 'name')
        return self

    def get_metadata(self):
        self.data['metadata'] = getattr(self.instance, 'metadata')
        return self

    def get_availability_zone(self):
        self.data['availability_zone'] = getattr(self.instance, 'OS-EXT-AZ:availability_zone')
        return self

    def get_config_drive(self):
        self.data['config_drive'] = getattr(self.instance, 'config_drive')
        return self

    def get_disk_config(self):
        self.data['disk_config'] = getattr(self.instance, 'OS-DCF:diskConfig')
        return self

    def get_instance_name(self):
        self.data['instance_name'] = getattr(self.instance, 'OS-EXT-SRV-ATTR:instance_name')
        return self

    def get_image(self):
        if self.instance.image:
            self.data['image'] = ImageTransfer(self.instance.image['id'], self.glance_client)
            self.data['boot_from_volume'] = False
        else:
            self.data['image'] = None
            self.data['boot_from_volume'] = True
        return self

    def get_flavor(self):
        flav = self.__get_flavor_from_instance(self.instance)
        self.data['flavor'] = {'name': flav["name"],
                               'ram': flav["ram"],
                               'vcpus': flav["vcpus"],
                               'swap': flav["swap"],
                               'is_public': flav["os-flavor-access:is_public"],
                               'disk': flav['disk'],
                               'ephemeral': flav['OS-FLV-EXT-DATA:ephemeral'],
                               'rxtx_factor': flav['rxtx_factor']}
        return self

    def get_security_groups(self):
        self.data['security_groups'] = [security_group['name'] for security_group in self.instance.security_groups]
        return self

    def get_key(self):
        self.data['key'] = {'name': self.instance.key_name}
        return self

    def get_networks(self):
        networks = []

        for network in self.instance.networks.items():
            networks.append({
                'name': network[0],
                'ip': network[1][0],
                'mac': self.__get_mac_by_ip(network[1][0])
            })

        self.data['networks'] = networks
        return self

    def get_disk(self):
        """Getting information about diff file of source instance"""
        is_ephemeral = self.__get_flavor_from_instance(self.instance)['OS-FLV-EXT-DATA:ephemeral'] > 0
        if not self.data["boot_from_volume"]:
            self.data['disk'] = {
                'type': 'remote file',
                'host': getattr(self.instance, 'OS-EXT-SRV-ATTR:host'),
                'diff_path': self.__get_instance_diff_path(self.instance, False),
                'ephemeral': self.__get_instance_diff_path(self.instance, True) if is_ephemeral else None
            }
        else:
            self.data["boot_volume_size"] = {}
        return self

    def get_volumes(self):

        """
            Gathering information about attached volumes to source instance and upload these volumes
            to Glance for further importing through image-service on to destination cloud.
        """
        images_from_volumes = []
        for volume_info in self.nova_client.volumes.get_server_volumes(self.instance.id):
            volume = self.cinder_client.volumes.get(volume_info.volumeId)
            LOG.debug("| | uploading volume %s [%s] to image service" % (volume.display_name, volume.id))
            resp, image = self.cinder_client.volumes.upload_to_image(volume=volume,
                                                                     force=True,
                                                                     image_name=volume.id,
                                                                     container_format="bare",
                                                                     disk_format=self.config['cinder']['disk_format'])
            image_upload = image['os-volume_upload_image']
            self.__wait_for_status(self.glance_client.images, image_upload['image_id'], 'active')
            if self.config["cinder"]["backend"] == "ceph":
                image_from_glance = self.glance_client.images.get(image_upload['image_id'])
                with settings(host_string=self.config['host']):
                    out = json.loads(run("rbd -p images info %s --format json" % image_upload['image_id']))
                    image_from_glance.update(size=out["size"])
            if (not volume.bootable) or (not self.data["boot_from_volume"]):
                images_from_volumes.append(VolumeTransfer(volume,
                                                          self.instance,
                                                          image_upload['image_id'],
                                                          self.glance_client))
            else:
                self.data['image'] = ImageTransfer(image_upload['image_id'], self.glance_client)
                self.data['boot_volume_size'] = volume.size

        self.data['volumes'] = images_from_volumes
        return self

    def __get_flavor_from_instance(self, instance):
        return self.nova_client.flavors.get(instance.flavor['id']).__dict__

    def __get_instance_diff_path(self, instance, is_ephemeral):

        """Return path of instance's diff file"""

        disk_host = getattr(self.instance, 'OS-EXT-SRV-ATTR:host')
        libvirt_name = getattr(self.instance, 'OS-EXT-SRV-ATTR:instance_name')
        source_disk = None
        with settings(host_string=self.config['host']):
            with forward_agent(env.key_filename):
                out = run("ssh -oStrictHostKeyChecking=no %s 'virsh domblklist %s'" %
                          (disk_host, libvirt_name))
                source_out = out.split()
                path_disk = (DISK + LOCAL) if is_ephemeral else DISK
                for i in source_out:
                    if instance.id + path_disk == i[-(LEN_UUID_INSTANCE+len(path_disk)):]:
                        source_disk = i
                if not source_disk:
                    raise NameError("Can't find suitable name of the source disk path")
        return source_disk

    def __get_mac_by_ip(self, ip_address):
        for port in self.port_list:
            if port["fixed_ips"][0]["ip_address"] == ip_address:
                return port["mac_address"]

    def __wait_for_status(self, getter, id, status):
        while getter.get(id).status != status:
            time.sleep(1)

    def __getattr__(self, item):
        getter = {
            'port_list': lambda: self.network_client.list_ports()["ports"]
        }[item]

        if getter is None:
            raise AttributeError("Exporter has no attribute %s" % item)

        return getter()