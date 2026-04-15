''' unit tests for na_ontap_service_processor Ansible module '''

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type
import pytest

from ansible_collections.netapp.ontap.tests.unit.compat.mock import patch
import ansible_collections.netapp.ontap.plugins.module_utils.netapp as netapp_utils
# pylint: disable=unused-import
from ansible_collections.netapp.ontap.tests.unit.plugins.module_utils.ansible_mocks import patch_ansible, \
    create_module, create_and_apply, expect_and_capture_ansible_exception
from ansible_collections.netapp.ontap.tests.unit.framework.rest_factory import rest_responses
from ansible_collections.netapp.ontap.tests.unit.framework.mock_rest_and_zapi_requests import patch_request_and_invoke, \
    register_responses
from ansible_collections.netapp.ontap.plugins.modules.na_ontap_service_processor \
    import NetAppOntapServiceProcessor as sp_module  # module under test


DEFAULT_ARGS = {
    'node': 'test-node-1',
    'action': 'reboot',
    'hostname': 'host',
    'username': 'admin',
    'password': 'password',
    'wait_for_completion': False
}

NODE_UUID = '5dd7aed0-1234-5678-9abc-def012345678'

SRR = rest_responses({
    'node_record': (200, {"records": [{
        'name': 'test-node-1',
        'uuid': NODE_UUID
    }]}, None),
    'sp_online': (200, {
        'service_processor': {
            'state': 'online',
            'firmware_version': '3.10'
        }
    }, None),
    'sp_rebooting': (200, {
        'service_processor': {
            'state': 'rebooting',
            'firmware_version': '3.10'
        }
    }, None),
    'unexpected_action_error': (400, None, 'Unexpected argument "service_processor.action"'),
})


def test_module_fail_when_required_args_missing():
    ''' required arguments are reported as errors '''
    fragments = ["missing required arguments:", "hostname", "node", "action"]
    error = create_module(sp_module, {}, fail=True)['msg']
    for fragment in fragments:
        assert fragment in error


def test_reboot_sp_success():
    ''' test successful SP reboot without waiting '''
    register_responses([
        ('GET', 'cluster', SRR['is_rest']),
        ('GET', 'cluster/nodes', SRR['node_record']),
        ('PATCH', 'cluster/nodes/%s' % NODE_UUID, SRR['success']),
    ])
    result = create_and_apply(sp_module, DEFAULT_ARGS)
    assert result['changed']


def test_reboot_sp_check_mode():
    ''' test check mode does not execute reboot '''
    register_responses([
        ('GET', 'cluster', SRR['is_rest']),
    ])
    result = create_and_apply(sp_module, DEFAULT_ARGS, check_mode=True)
    assert result['changed']


@patch('time.sleep')
def test_reboot_sp_wait_for_completion(mock_sleep):
    ''' test SP reboot with wait_for_completion '''
    register_responses([
        ('GET', 'cluster', SRR['is_rest']),
        ('GET', 'cluster/nodes', SRR['node_record']),                              # get UUID for reboot
        ('PATCH', 'cluster/nodes/%s' % NODE_UUID, SRR['success']),                 # reboot
        ('GET', 'cluster/nodes/%s' % NODE_UUID, SRR['sp_rebooting']),              # poll 1: still rebooting
        ('GET', 'cluster/nodes/%s' % NODE_UUID, SRR['sp_rebooting']),              # poll 2: still rebooting
        ('GET', 'cluster/nodes/%s' % NODE_UUID, SRR['sp_online']),                 # poll 3: online
    ])
    args = {'wait_for_completion': True, 'wait_timeout': 300, 'poll_interval': 15}
    result = create_and_apply(sp_module, DEFAULT_ARGS, args)
    assert result['changed']
    assert result.get('sp_state') == 'online'


@patch('time.sleep')
def test_reboot_sp_wait_timeout(mock_sleep):
    ''' test SP reboot with wait timeout - SP stays rebooting '''
    register_responses([
        ('GET', 'cluster', SRR['is_rest']),
        ('GET', 'cluster/nodes', SRR['node_record']),                              # get UUID for reboot
        ('PATCH', 'cluster/nodes/%s' % NODE_UUID, SRR['success']),                 # reboot
        ('GET', 'cluster/nodes/%s' % NODE_UUID, SRR['sp_rebooting']),              # poll 1
        ('GET', 'cluster/nodes/%s' % NODE_UUID, SRR['sp_rebooting']),              # poll 2
        ('GET', 'cluster/nodes/%s' % NODE_UUID, SRR['sp_rebooting']),              # poll 3 (timeout reached)
        ('GET', 'cluster/nodes/%s' % NODE_UUID, SRR['sp_rebooting']),              # final check after timeout
    ])
    args = {'wait_for_completion': True, 'wait_timeout': 45, 'poll_interval': 15}
    result = create_and_apply(sp_module, DEFAULT_ARGS, args)
    assert result['changed']


def test_reboot_sp_fallback_to_cli():
    ''' test fallback to REST CLI passthrough for older ONTAP '''
    register_responses([
        ('GET', 'cluster', SRR['is_rest']),
        ('GET', 'cluster/nodes', SRR['node_record']),
        ('PATCH', 'cluster/nodes/%s' % NODE_UUID, SRR['unexpected_action_error']),  # native REST fails
        ('PATCH', 'private/cli/sp/reboot-sp', SRR['success']),                      # CLI passthrough succeeds
    ])
    result = create_and_apply(sp_module, DEFAULT_ARGS)
    assert result['changed']


def test_reboot_sp_error():
    ''' test error on SP reboot '''
    register_responses([
        ('GET', 'cluster', SRR['is_rest']),
        ('GET', 'cluster/nodes', SRR['node_record']),
        ('PATCH', 'cluster/nodes/%s' % NODE_UUID, SRR['generic_error']),
    ])
    error = 'Error rebooting service processor on node test-node-1'
    assert error in create_and_apply(sp_module, DEFAULT_ARGS, fail=True)['msg']


def test_reboot_sp_fallback_cli_error():
    ''' test error when both native REST and CLI passthrough fail '''
    register_responses([
        ('GET', 'cluster', SRR['is_rest']),
        ('GET', 'cluster/nodes', SRR['node_record']),
        ('PATCH', 'cluster/nodes/%s' % NODE_UUID, SRR['unexpected_action_error']),  # native REST fails
        ('PATCH', 'private/cli/sp/reboot-sp', SRR['generic_error']),                # CLI passthrough also fails
    ])
    error = 'reboot_sp requires ONTAP 9.10.1 or newer'
    assert error in create_and_apply(sp_module, DEFAULT_ARGS, fail=True)['msg']


def test_node_not_found():
    ''' test error when node does not exist '''
    register_responses([
        ('GET', 'cluster', SRR['is_rest']),
        ('GET', 'cluster/nodes', SRR['empty_records']),
    ])
    error = 'Error: node not found: test-node-1'
    assert error in create_and_apply(sp_module, DEFAULT_ARGS, fail=True)['msg']


def test_node_uuid_error():
    ''' test error when fetching node UUID fails '''
    register_responses([
        ('GET', 'cluster', SRR['is_rest']),
        ('GET', 'cluster/nodes', SRR['generic_error']),
    ])
    error = 'Error getting node UUID for test-node-1'
    assert error in create_and_apply(sp_module, DEFAULT_ARGS, fail=True)['msg']


def test_get_sp_state_error():
    ''' test error when getting SP state '''
    register_responses([
        ('GET', 'cluster', SRR['is_rest']),
    ])
    sp_obj = create_module(sp_module, DEFAULT_ARGS)
    sp_obj._node_uuid = NODE_UUID
    register_responses([
        ('GET', 'cluster/nodes/%s' % NODE_UUID, SRR['generic_error']),
    ])
    error = 'Error getting SP state for node test-node-1'
    assert error in expect_and_capture_ansible_exception(sp_obj.get_sp_state, 'fail')['msg']
