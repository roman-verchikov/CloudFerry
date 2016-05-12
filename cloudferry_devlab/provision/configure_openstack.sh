#!/usr/bin/env bash

SYSTEM_IP=$1
RELEASE=$2

cat << EOF | sudo bash
if [ -f /etc/quantum/plugins/openvswitch/ovs_quantum_plugin.ini ]; then
    service quantum-plugin-openvswitch-agent stop
    crudini --set /etc/quantum/plugins/openvswitch/ovs_quantum_plugin.ini OVS local_ip ${SYSTEM_IP}
    service quantum-plugin-openvswitch-agent start
fi

if [ -f /etc/neutron/plugins/openvswitch/ovs_neutron_plugin.ini ]; then
    service neutron-plugin-openvswitch-agent stop
    crudini --set /etc/neutron/plugins/openvswitch/ovs_neutron_plugin.ini OVS local_ip ${SYSTEM_IP}
    service neutron-plugin-openvswitch-agent start
fi

service nova-compute stop
service nova-conductor stop
service nova-api stop
if [ "${RELEASE}" -eq "grizzly" ]; then
    crudini --set /etc/nova/nova.conf DEFAULT novncproxy_base_url http://${SYSTEM_IP}:6080/vnc_auto.html
else
    crudini --set /etc/nova/nova.conf DEFAULT vncproxy_base_url http://${SYSTEM_IP}:6080/vnc_auto.html
fi
crudini --set /etc/nova/nova.conf DEFAULT vncserver_proxyclient_address ${SYSTEM_IP}
crudini --set /etc/nova/nova.conf DEFAULT osapi_compute_workers 1
crudini --set /etc/nova/nova.conf DEFAULT metadata_workers 1
crudini --set /etc/nova/nova.conf conductor workers 1
service nova-api start
service nova-conductor start
service nova-compute start

EOF