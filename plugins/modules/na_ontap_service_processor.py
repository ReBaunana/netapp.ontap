#!/usr/bin/python

# (c) 2026, NetApp, Inc
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type


DOCUMENTATION = '''
module: na_ontap_service_processor
short_description: NetApp ONTAP service processor operations
extends_documentation_fragment:
    - netapp.ontap.netapp.na_ontap_rest
version_added: "22.13.0"
author: NetApp Ansible Team (@carchi8py) <ng-ansible-team@netapp.com>
description:
  - Perform operations on the ONTAP Service Processor (SP) / BMC.
  - Currently supports rebooting the service processor.
  - This module only supports REST and requires ONTAP 9.10.1 or later for native REST.
  - For older ONTAP versions (9.6+), the module falls back to the REST CLI passthrough.
options:
  node:
    description:
      - The node where the service processor is located.
    required: true
    type: str
  action:
    description:
      - The action to perform on the service processor.
      - C(reboot) - Reboots the service processor.
    required: true
    type: str
    choices: ['reboot']
  wait_for_completion:
    description:
      - Set this parameter to C(true) to wait until the SP action completes.
      - Set this parameter to C(false) for asynchronous execution.
      - When C(true), the module polls the SP state until it is no longer in the transitional state (e.g., rebooting).
    type: bool
    default: true
  wait_timeout:
    description:
      - Maximum time in seconds to wait for the SP action to complete.
      - Only used when C(wait_for_completion) is C(true).
    type: int
    default: 300
  poll_interval:
    description:
      - Time in seconds between status polls when waiting for completion.
      - Only used when C(wait_for_completion) is C(true).
    type: int
    default: 15
'''

EXAMPLES = """
- name: Reboot Service Processor
  netapp.ontap.na_ontap_service_processor:
    node: "{{ netapp_node }}"
    action: reboot
    hostname: "{{ netapp_hostname }}"
    username: "{{ netapp_username }}"
    password: "{{ netapp_password }}"

- name: Reboot Service Processor and wait for completion
  netapp.ontap.na_ontap_service_processor:
    node: "{{ netapp_node }}"
    action: reboot
    wait_for_completion: true
    wait_timeout: 600
    hostname: "{{ netapp_hostname }}"
    username: "{{ netapp_username }}"
    password: "{{ netapp_password }}"

- name: Reboot Service Processor without waiting
  netapp.ontap.na_ontap_service_processor:
    node: "{{ netapp_node }}"
    action: reboot
    wait_for_completion: false
    hostname: "{{ netapp_hostname }}"
    username: "{{ netapp_username }}"
    password: "{{ netapp_password }}"
"""

RETURN = """
sp_state:
  description: The state of the service processor after the action.
  returned: success, when wait_for_completion is true
  type: str
  sample: "online"
"""

import time

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.netapp.ontap.plugins.module_utils.netapp_module import NetAppModule
import ansible_collections.netapp.ontap.plugins.module_utils.netapp as netapp_utils
from ansible_collections.netapp.ontap.plugins.module_utils import rest_generic


class NetAppOntapServiceProcessor:
    """
    Perform operations on the ONTAP Service Processor
    """

    def __init__(self):
        self.argument_spec = netapp_utils.na_ontap_rest_only_spec()
        self.argument_spec.update(dict(
            node=dict(required=True, type='str'),
            action=dict(required=True, type='str', choices=['reboot']),
            wait_for_completion=dict(required=False, type='bool', default=True),
            wait_timeout=dict(required=False, type='int', default=300),
            poll_interval=dict(required=False, type='int', default=15),
        ))

        self.module = AnsibleModule(
            argument_spec=self.argument_spec,
            supports_check_mode=True
        )

        self.na_helper = NetAppModule()
        self.parameters = self.na_helper.set_parameters(self.module.params)

        self.rest_api = netapp_utils.OntapRestAPI(self.module)
        self.use_rest = self.rest_api.is_rest()
        if not self.use_rest:
            self.module.fail_json(msg='REST is required for na_ontap_service_processor, '
                                     'cannot connect: %s' % self.rest_api.errors)

        self._node_uuid = None

    def get_node_uuid(self):
        """Get the UUID for the specified node"""
        if self._node_uuid is not None:
            return self._node_uuid
        api = 'cluster/nodes'
        query = {'name': self.parameters['node']}
        record, error = rest_generic.get_one_record(self.rest_api, api, query, fields='uuid')
        if error:
            self.module.fail_json(msg='Error getting node UUID for %s: %s' % (self.parameters['node'], error))
        if not record:
            self.module.fail_json(msg='Error: node not found: %s' % self.parameters['node'])
        self._node_uuid = record['uuid']
        return self._node_uuid

    def get_sp_state(self):
        """Get the current service processor state for the node"""
        uuid = self.get_node_uuid()
        api = 'cluster/nodes'
        fields = 'service_processor.state,service_processor.firmware_version'
        record, error = rest_generic.get_one_record(self.rest_api, api + '/' + uuid, fields=fields)
        if error:
            self.module.fail_json(msg='Error getting SP state for node %s: %s' % (self.parameters['node'], error))
        if not record:
            return None
        return self.na_helper.safe_get(record, ['service_processor', 'state'])

    def reboot_sp(self):
        """Reboot the service processor using native REST API (ONTAP 9.10.1+)"""
        uuid = self.get_node_uuid()
        api = 'cluster/nodes'
        body = {'service_processor.action': 'reboot'}
        dummy, error = rest_generic.patch_async(self.rest_api, api, uuid, body)
        if error and 'Unexpected argument "service_processor.action"' in error:
            # Fall back to REST CLI passthrough for older ONTAP versions
            error = self.reboot_sp_rest_cli()
            if error:
                error = ('reboot_sp requires ONTAP 9.10.1 or newer, '
                         'falling back to CLI passthrough failed: %s' % error)
        if error:
            self.module.fail_json(msg='Error rebooting service processor on node %s: %s'
                                  % (self.parameters['node'], error))

    def reboot_sp_rest_cli(self):
        """Fall back to REST CLI passthrough for older ONTAP versions"""
        api = 'private/cli/sp/reboot-sp'
        query = {'node': self.parameters['node']}
        dummy, error = rest_generic.patch_async(self.rest_api, api, None, None, query)
        return error

    def wait_for_sp(self, expected_transitional_state='rebooting'):
        """Wait for SP to finish its transitional state and come back online"""
        timeout = self.parameters['wait_timeout']
        poll_interval = self.parameters['poll_interval']
        elapsed = 0
        final_state = None

        while elapsed < timeout:
            time.sleep(poll_interval)
            elapsed += poll_interval
            state = self.get_sp_state()
            if state is not None and state != expected_transitional_state:
                final_state = state
                break
        else:
            self.module.warn('Service processor did not finish %s within %d seconds'
                             % (expected_transitional_state, timeout))
            final_state = self.get_sp_state()

        return final_state

    def apply(self):
        """Run module based on playbook"""
        action = self.parameters['action']
        extra_responses = {}

        if action == 'reboot':
            if not self.module.check_mode:
                self.reboot_sp()
                if self.parameters.get('wait_for_completion'):
                    final_state = self.wait_for_sp(expected_transitional_state='rebooting')
                    if final_state:
                        extra_responses['sp_state'] = final_state

        result = netapp_utils.generate_result(True, extra_responses=extra_responses if extra_responses else None)
        self.module.exit_json(**result)


def main():
    """
    Create the NetApp ONTAP Service Processor object and run the action
    """
    obj = NetAppOntapServiceProcessor()
    obj.apply()


if __name__ == '__main__':
    main()
