#!/bin/bash
# Fixes invalid glance endpoint URL
# Previously was http://<ip>:9292/v2, which resulted in 'Unauthorized'
# Correct glance endpoint URL is http://<ip>:9292/

set -e
set -x

if [[ $# != 1 ]]; then
    echo <<EOF
Fixes invalid glance endpoint URL.

Usage: $(basename $0) <VM IP address>
EOF
    exit 1
fi

VM_IP=$1

export OS_USERNAME=admin
export OS_PASSWORD=admin
export OS_TENANT_NAME=admin
export OS_AUTH_URL=http://$VM_IP:5000/v2.0

glance_service_id=$(keystone service-get glance | sed -rn 's/.*\<id\>.*\| (\w{32}).*/\1/p')
glance_ep_id=$(keystone endpoint-list | grep ${glance_service_id} | sed -rn 's/^\| (\w{32}).*/\1/p')

keystone endpoint-delete $glance_ep_id
keystone endpoint-create \
      --region myregion \
      --service-id $glance_service_id \
      --publicurl http://$VM_IP:9292 \
      --internalurl http://$VM_IP:9292 \
      --adminurl http://$VM_IP:9292
