# Copyright 2010-2011 OpenStack Foundation
# Copyright (c) 2013 Hewlett-Packard Development Company, L.P.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import collections
import time
import uuid

from distutils import version as du_version
import fixtures
import mock
import os
import os_client_config as occ
from requests import structures
from requests_mock.contrib import fixture as rm_fixture
import tempfile

import shade.openstackcloud
from shade.tests import base


_ProjectData = collections.namedtuple(
    'ProjectData',
    'project_id, project_name, enabled, domain_id, description, '
    'json_response, json_request')


_UserData = collections.namedtuple(
    'UserData',
    'user_id, password, name, email, description, domain_id, enabled, '
    'json_response, json_request')


_GroupData = collections.namedtuple(
    'GroupData',
    'group_id, group_name, domain_id, description, json_response, '
    'json_request')


_DomainData = collections.namedtuple(
    'DomainData',
    'domain_id, domain_name, description, json_response, '
    'json_request')


_ServiceData = collections.namedtuple(
    'Servicedata',
    'service_id, service_name, service_type, description, enabled, '
    'json_response_v3, json_response_v2, json_request')


_EndpointDataV3 = collections.namedtuple(
    'EndpointData',
    'endpoint_id, service_id, interface, region, url, enabled, '
    'json_response, json_request')


_EndpointDataV2 = collections.namedtuple(
    'EndpointData',
    'endpoint_id, service_id, region, public_url, internal_url, '
    'admin_url, v3_endpoint_list, json_response, '
    'json_request')


# NOTE(notmorgan): Shade does not support domain-specific roles
# This should eventually be fixed if it becomes a main-stream feature.
_RoleData = collections.namedtuple(
    'RoleData',
    'role_id, role_name, json_response, json_request')


class BaseTestCase(base.TestCase):

    def setUp(self, cloud_config_fixture='clouds.yaml'):
        """Run before each test method to initialize test environment."""

        super(BaseTestCase, self).setUp()

        # Sleeps are for real testing, but unit tests shouldn't need them
        realsleep = time.sleep

        def _nosleep(seconds):
            return realsleep(seconds * 0.0001)

        self.sleep_fixture = self.useFixture(fixtures.MonkeyPatch(
                                             'time.sleep',
                                             _nosleep))
        self.fixtures_directory = 'shade/tests/unit/fixtures'

        # Isolate os-client-config from test environment
        config = tempfile.NamedTemporaryFile(delete=False)
        cloud_path = '%s/clouds/%s' % (self.fixtures_directory,
                                       cloud_config_fixture)
        with open(cloud_path, 'rb') as f:
            content = f.read()
            config.write(content)
        config.close()

        vendor = tempfile.NamedTemporaryFile(delete=False)
        vendor.write(b'{}')
        vendor.close()

        # set record mode depending on environment
        record_mode = os.environ.get('BETAMAX_RECORD_FIXTURES', False)
        if record_mode:
            self.record_fixtures = 'new_episodes'
        else:
            self.record_fixtures = None

        test_cloud = os.environ.get('SHADE_OS_CLOUD', '_test_cloud_')
        self.config = occ.OpenStackConfig(
            config_files=[config.name],
            vendor_files=[vendor.name],
            secure_files=['non-existant'])
        self.cloud_config = self.config.get_one_cloud(
            cloud=test_cloud, validate=False)
        self.cloud = shade.OpenStackCloud(
            cloud_config=self.cloud_config,
            log_inner_exceptions=True)
        self.strict_cloud = shade.OpenStackCloud(
            cloud_config=self.cloud_config,
            log_inner_exceptions=True,
            strict=True)
        self.op_cloud = shade.OperatorCloud(
            cloud_config=self.cloud_config,
            log_inner_exceptions=True)


class TestCase(BaseTestCase):

    def setUp(self, cloud_config_fixture='clouds.yaml'):

        super(TestCase, self).setUp(cloud_config_fixture=cloud_config_fixture)
        self.session_fixture = self.useFixture(fixtures.MonkeyPatch(
            'os_client_config.cloud_config.CloudConfig.get_session',
            mock.Mock()))


class RequestsMockTestCase(BaseTestCase):

    def setUp(self, cloud_config_fixture='clouds.yaml'):

        super(RequestsMockTestCase, self).setUp(
            cloud_config_fixture=cloud_config_fixture)

        # FIXME(notmorgan): Convert the uri_registry, discovery.json, and
        # use of keystone_v3/v2 to a proper fixtures.Fixture. For now this
        # is acceptable, but eventually this should become it's own fixture
        # that encapsulates the registry, registering the URIs, and
        # assert_calls (and calling assert_calls every test case that uses
        # it on cleanup). Subclassing here could be 100% eliminated in the
        # future allowing any class to simply
        # self.useFixture(shade.RequestsMockFixture) and get all the benefits.

        # NOTE(notmorgan): use an ordered dict here to ensure we preserve the
        # order in which items are added to the uri_registry. This makes
        # the behavior more consistent when dealing with ensuring the
        # requests_mock uri/query_string matchers are ordered and parse the
        # request in the correct orders.
        self._uri_registry = collections.OrderedDict()
        self.discovery_json = os.path.join(
            self.fixtures_directory, 'discovery.json')
        self.use_keystone_v3()
        self.__register_uris_called = False

    def get_mock_url(self, service_type, interface, resource=None,
                     append=None, base_url_append=None,
                     qs_elements=None):
        endpoint_url = self.cloud.endpoint_for(
            service_type=service_type, interface=interface)
        to_join = [endpoint_url]
        qs = ''
        if base_url_append:
            to_join.append(base_url_append)
        if resource:
            to_join.append(resource)
        to_join.extend(append or [])
        if qs_elements is not None:
            qs = '?%s' % '&'.join(qs_elements)
        return '%(uri)s%(qs)s' % {'uri': '/'.join(to_join), 'qs': qs}

    def mock_for_keystone_projects(self, project=None, v3=True,
                                   list_get=False, id_get=False,
                                   project_list=None, project_count=None):
        if project:
            assert not (project_list or project_count)
        elif project_list:
            assert not (project or project_count)
        elif project_count:
            assert not (project or project_list)
        else:
            raise Exception('Must specify a project, project_list, '
                            'or project_count')
        assert list_get or id_get

        base_url_append = 'v3' if v3 else None
        if project:
            project_list = [project]
        elif project_count:
            # Generate multiple projects
            project_list = [self._get_project_data(v3=v3)
                            for c in range(0, project_count)]
        uri_mock_list = []
        if list_get:
            uri_mock_list.append(
                dict(method='GET',
                     uri=self.get_mock_url(
                         service_type='identity',
                         interface='admin',
                         resource='projects',
                         base_url_append=base_url_append),
                     status_code=200,
                     json={'projects': [p.json_response['project']
                                        for p in project_list]})
            )
        if id_get:
            for p in project_list:
                uri_mock_list.append(
                    dict(method='GET',
                         uri=self.get_mock_url(
                             service_type='identity',
                             interface='admin',
                             resource='projects',
                             append=[p.project_id],
                             base_url_append=base_url_append),
                         status_code=200,
                         json=p.json_response)
                )
        self.__do_register_uris(uri_mock_list)
        return project_list

    def _get_project_data(self, project_name=None, enabled=None,
                          domain_id=None, description=None, v3=True):
        project_name = project_name or self.getUniqueString('projectName')
        project_id = uuid.uuid4().hex
        response = {'id': project_id, 'name': project_name}
        request = {'name': project_name}
        domain_id = (domain_id or uuid.uuid4().hex) if v3 else None
        if domain_id:
            request['domain_id'] = domain_id
            response['domain_id'] = domain_id
        if enabled is not None:
            enabled = bool(enabled)
            response['enabled'] = enabled
            request['enabled'] = enabled
        response.setdefault('enabled', True)
        if description:
            response['description'] = description
            request['description'] = description
        if v3:
            project_key = 'project'
        else:
            project_key = 'tenant'
        return _ProjectData(project_id, project_name, enabled, domain_id,
                            description, {project_key: response},
                            {project_key: request})

    def _get_group_data(self, name=None, domain_id=None, description=None):
        group_id = uuid.uuid4().hex
        name or self.getUniqueString('groupname')
        domain_id = uuid.UUID(domain_id or uuid.uuid4().hex).hex
        response = {'id': group_id, 'name': name, 'domain_id': domain_id}
        request = {'name': name}
        if description is not None:
            response['description'] = description
            request['description'] = description

        return _GroupData(group_id, name, domain_id, description,
                          {'group': response}, {'group': request})

    def _get_user_data(self, name=None, password=None, **kwargs):

        name = name or self.getUniqueString('username')
        password = password or self.getUniqueString('user_password')
        user_id = uuid.uuid4().hex

        response = {'name': name, 'id': user_id}
        request = {'name': name, 'password': password, 'tenantId': None}

        if kwargs.get('domain_id'):
            kwargs['domain_id'] = uuid.UUID(kwargs['domain_id']).hex
            response['domain_id'] = kwargs.pop('domain_id')

        response['email'] = kwargs.pop('email', None)
        request['email'] = response['email']

        response['enabled'] = kwargs.pop('enabled', True)
        request['enabled'] = response['enabled']

        response['description'] = kwargs.pop('description', None)
        if response['description']:
            request['description'] = response['description']

        self.assertIs(0, len(kwargs), message='extra key-word args received '
                                              'on _get_user_data')

        return _UserData(user_id, password, name, response['email'],
                         response['description'], response.get('domain_id'),
                         response.get('enabled'), {'user': response},
                         {'user': request})

    def _get_domain_data(self, domain_name=None, description=None,
                         enabled=None):
        domain_id = uuid.uuid4().hex
        domain_name = domain_name or self.getUniqueString('domainName')
        response = {'id': domain_id, 'name': domain_name}
        request = {'name': domain_name}
        if enabled is not None:
            request['enabled'] = bool(enabled)
            response['enabled'] = bool(enabled)
        if description:
            response['description'] = description
            request['description'] = description
        response.setdefault('enabled', True)
        return _DomainData(domain_id, domain_name, description,
                           {'domain': response}, {'domain': request})

    def _get_service_data(self, type=None, name=None, description=None,
                          enabled=True):
        service_id = uuid.uuid4().hex
        name = name or uuid.uuid4().hex
        type = type or uuid.uuid4().hex

        response = {'id': service_id, 'name': name, 'type': type,
                    'enabled': enabled}
        if description is not None:
            response['description'] = description
        request = response.copy()
        request.pop('id')
        return _ServiceData(service_id, name, type, description, enabled,
                            {'service': response},
                            {'OS-KSADM:service': response}, request)

    def _get_endpoint_v3_data(self, service_id=None, region=None,
                              url=None, interface=None, enabled=True):
        endpoint_id = uuid.uuid4().hex
        service_id = service_id or uuid.uuid4().hex
        region = region or uuid.uuid4().hex
        url = url or 'https://example.com/'
        interface = interface or uuid.uuid4().hex

        response = {'id': endpoint_id, 'service_id': service_id,
                    'region': region, 'interface': interface,
                    'url': url, 'enabled': enabled}
        request = response.copy()
        request.pop('id')
        response['region_id'] = response['region']
        return _EndpointDataV3(endpoint_id, service_id, interface, region,
                               url, enabled, {'endpoint': response},
                               {'endpoint': request})

    def _get_endpoint_v2_data(self, service_id=None, region=None,
                              public_url=None, admin_url=None,
                              internal_url=None):
        endpoint_id = uuid.uuid4().hex
        service_id = service_id or uuid.uuid4().hex
        region = region or uuid.uuid4().hex
        response = {'id': endpoint_id, 'service_id': service_id,
                    'region': region}
        v3_endpoints = {}
        if admin_url:
            response['adminURL'] = admin_url
            v3_endpoints['admin'] = self._get_endpoint_v3_data(
                service_id, region, public_url, interface='admin')
        if internal_url:
            response['internalURL'] = internal_url
            v3_endpoints['internal'] = self._get_endpoint_v3_data(
                service_id, region, internal_url, interface='internal')
        if public_url:
            response['publicURL'] = public_url
            v3_endpoints['public'] = self._get_endpoint_v3_data(
                service_id, region, public_url, interface='public')
        request = response.copy()
        request.pop('id')
        return _EndpointDataV2(endpoint_id, service_id, region, public_url,
                               internal_url, admin_url, v3_endpoints,
                               {'endpoint': response}, {'endpoint': request})

    def _get_role_data(self, role_name=None):
        role_id = uuid.uuid4().hex
        role_name = role_name or uuid.uuid4().hex
        request = {'name': role_name}
        response = request.copy()
        response['id'] = role_id
        return _RoleData(role_id, role_name, {'role': response},
                         {'role': request})

    def use_keystone_v3(self):
        self.adapter = self.useFixture(rm_fixture.Fixture())
        self.calls = []
        self._uri_registry.clear()
        self.__do_register_uris([
            dict(method='GET', uri='https://identity.example.com/',
                 text=open(self.discovery_json, 'r').read()),
            dict(method='POST',
                 uri='https://identity.example.com/v3/auth/tokens',
                 headers={
                     'X-Subject-Token': self.getUniqueString('KeystoneToken')},
                 text=open(os.path.join(
                     self.fixtures_directory, 'catalog-v3.json'), 'r').read()
                 )
        ])
        self._make_test_cloud(identity_api_version='3')

    def use_keystone_v2(self):
        self.adapter = self.useFixture(rm_fixture.Fixture())
        self.calls = []
        self._uri_registry.clear()

        # occ > 1.26.0 fixes keystoneclient construction. Unfortunately, it
        # breaks our mocking of what keystoneclient does here. Since we're
        # close to just getting rid of ksc anyway, just put in a version match
        occ_version = du_version.StrictVersion(occ.__version__)
        if occ_version > du_version.StrictVersion('1.26.0'):
            endpoint_uri = 'https://identity.example.com/v2.0'
        else:
            endpoint_uri = 'https://identity.example.com/'

        self.__do_register_uris([
            dict(method='GET', uri='https://identity.example.com/',
                 text=open(self.discovery_json, 'r').read()),
            dict(method='POST', uri='https://identity.example.com/v2.0/tokens',
                 text=open(os.path.join(
                     self.fixtures_directory, 'catalog-v2.json'), 'r').read()
                 ),
            dict(method='GET', uri=endpoint_uri,
                 text=open(self.discovery_json, 'r').read()),
            dict(method='GET', uri='https://identity.example.com/',
                 text=open(self.discovery_json, 'r').read())
        ])

        self._make_test_cloud(cloud_name='_test_cloud_v2_',
                              identity_api_version='2.0')

    def _add_discovery_uri_call(self):
        # NOTE(notmorgan): Temp workaround for transition to requests
        # mock for cases keystoneclient is still mocked directly. This allows
        # us to inject another call to discovery where needed in a test that
        # no longer mocks out kyestoneclient and performs the extra round
        # trips.
        self.__do_register_uris([
            dict(method='GET', uri='https://identity.example.com/',
                 text=open(self.discovery_json, 'r').read())])

    def _make_test_cloud(self, cloud_name='_test_cloud_', **kwargs):
        test_cloud = os.environ.get('SHADE_OS_CLOUD', cloud_name)
        self.cloud_config = self.config.get_one_cloud(
            cloud=test_cloud, validate=True, **kwargs)
        self.cloud = shade.OpenStackCloud(
            cloud_config=self.cloud_config,
            log_inner_exceptions=True)
        self.op_cloud = shade.OperatorCloud(
            cloud_config=self.cloud_config,
            log_inner_exceptions=True)

    def get_glance_discovery_mock_dict(
            self, image_version_json='image-version.json'):
        discovery_fixture = os.path.join(
            self.fixtures_directory, image_version_json)
        return dict(method='GET', uri='https://image.example.com/',
                    text=open(discovery_fixture, 'r').read())

    def use_glance(self, image_version_json='image-version.json'):
        # NOTE(notmorgan): This method is only meant to be used in "setUp"
        # where the ordering of the url being registered is tightly controlled
        # if the functionality of .use_glance is meant to be used during an
        # actual test case, use .get_glance_discovery_mock and apply to the
        # right location in the mock_uris when calling .register_uris
        self.__do_register_uris([
            self.get_glance_discovery_mock_dict(image_version_json)])

    def register_uris(self, uri_mock_list=None):
        """Mock a list of URIs and responses via requests mock.

        This method may be called only once per test-case to avoid odd
        and difficult to debug interactions. Discovery and Auth request mocking
        happens separately from this method.

        :param uri_mock_list: List of dictionaries that template out what is
                              passed to requests_mock fixture's `register_uri`.
                              Format is:
                                  {'method': <HTTP_METHOD>,
                                   'uri': <URI to be mocked>,
                                   ...
                                  }

                              Common keys to pass in the dictionary:
                                  * json: the json response (dict)
                                  * status_code: the HTTP status (int)
                                  * validate: The request body (dict) to
                                              validate with assert_calls
                              all key-word arguments that are valid to send to
                              requests_mock are supported.

                              This list should be in the order in which calls
                              are made. When `assert_calls` is executed, order
                              here will be validated. Duplicate URIs and
                              Methods are allowed and will be collapsed into a
                              single matcher. Each response will be returned
                              in order as the URI+Method is hit.
        :type uri_mock_list: list
        :return: None
        """
        assert not self.__register_uris_called
        self.__do_register_uris(uri_mock_list or [])
        self.__register_uris_called = True

    def __do_register_uris(self, uri_mock_list=None):
        for to_mock in uri_mock_list:
            kw_params = {k: to_mock.pop(k)
                         for k in ('request_headers', 'complete_qs',
                                   '_real_http')
                         if k in to_mock}

            method = to_mock.pop('method')
            uri = to_mock.pop('uri')
            # NOTE(notmorgan): make sure the delimiter is non-url-safe, in this
            # case "|" is used so that the split can be a bit easier on
            # maintainers of this code.
            key = '{method}|{uri}|{params}'.format(
                method=method, uri=uri, params=kw_params)
            validate = to_mock.pop('validate', {})
            headers = structures.CaseInsensitiveDict(to_mock.pop('headers',
                                                                 {}))
            if 'content-type' not in headers:
                headers[u'content-type'] = 'application/json'

            to_mock['headers'] = headers

            self.calls += [
                dict(
                    method=method,
                    url=uri, **validate)
            ]
            self._uri_registry.setdefault(
                key, {'response_list': [], 'kw_params': kw_params})
            if self._uri_registry[key]['kw_params'] != kw_params:
                raise AssertionError(
                    'PROGRAMMING ERROR: key-word-params '
                    'should be part of the uri_key and cannot change, '
                    'it will affect the matcher in requests_mock. '
                    '%(old)r != %(new)r' %
                    {'old': self._uri_registry[key]['kw_params'],
                     'new': kw_params})
            self._uri_registry[key]['response_list'].append(to_mock)

        for mocked, params in self._uri_registry.items():
            mock_method, mock_uri, _ignored = mocked.split('|', 2)
            self.adapter.register_uri(
                mock_method, mock_uri, params['response_list'],
                **params['kw_params'])

    def assert_calls(self, stop_after=None, do_count=True):
        for (x, (call, history)) in enumerate(
                zip(self.calls, self.adapter.request_history)):
            if stop_after and x > stop_after:
                break

            self.assertEqual(
                (call['method'], call['url']), (history.method, history.url),
                'REST mismatch on call {index}'.format(index=x))
            if 'json' in call:
                self.assertEqual(
                    call['json'], history.json(),
                    'json content mismatch in call {index}'.format(index=x))
            # headers in a call isn't exhaustive - it's checking to make sure
            # a specific header or headers are there, not that they are the
            # only headers
            if 'headers' in call:
                for key, value in call['headers'].items():
                    self.assertEqual(
                        value, history.headers[key],
                        'header mismatch in call {index}'.format(index=x))
        if do_count:
            self.assertEqual(
                len(self.calls), len(self.adapter.request_history))
