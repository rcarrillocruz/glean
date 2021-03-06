# Copyright (c) 2015 Hewlett-Packard Development Company, L.P.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import os

import fixtures
import mock
from oslotest import base
from testscenarios import load_tests_apply_scenarios as load_tests  # noqa

from glean import cmd


sample_data_path = os.path.join(
    os.path.dirname(os.path.realpath(__file__)), 'fixtures')

distros = ['Ubuntu', 'Debian', 'Fedora', 'RedHat', 'CentOS']
styles = ['hp', 'rax', 'liberty', 'nokey']

built_scenarios = []
for distro in distros:
    for style in styles:
        built_scenarios.append(
            ('%s-%s' % (distro, style),
             dict(distro=distro, style=style)))


class TestGlean(base.BaseTestCase):

    scenarios = built_scenarios

    def setUp(self):
        super(TestGlean, self).setUp()

    def _patch_argv(self, args):
        self.useFixture(fixtures.MonkeyPatch('sys.argv', ['.'] + args))

    def _patch_files(self, sample_prefix):
        std_open = open
        self.file_handle_mocks = {}

        def open_side_effect(*args, **kwargs):
            if (args[0].startswith('/etc/network') or
                    args[0].startswith('/etc/sysconfig/network-scripts') or
                    args[0].startswith('/etc/resolv.conf') or
                    args[0] in ('/etc/hostname', '/etc/hosts')):
                try:
                    mock_handle = self.file_handle_mocks[args[0]]
                except KeyError:
                    mock_handle = mock.Mock()
                    mock_handle.__enter__ = mock.Mock(return_value=mock_handle)
                    mock_handle.__exit__ = mock.Mock()
                    mock_handle.read.return_value = ''
                    self.file_handle_mocks[args[0]] = mock_handle
                return mock_handle
            elif args[0].startswith('/sys/class/net'):
                mock_args = [os.path.join(
                    sample_data_path, sample_prefix, args[0][1:])]
                if len(args) > 1:
                    mock_args += args[1:]
                return std_open(*mock_args, **kwargs)
            elif args[0].startswith('/mnt/config'):
                mock_args = [os.path.join(
                    sample_data_path, sample_prefix,
                    args[0][1:])]
                if len(args) > 1:
                    mock_args += args[1:]
                return std_open(*mock_args, **kwargs)
            else:
                return std_open(*args, **kwargs)

        mock_open = mock.Mock(side_effect=open_side_effect)
        self.useFixture(fixtures.MonkeyPatch('six.moves.builtins.open',
                                             mock_open))

        real_listdir = os.listdir

        def fake_listdir(path):
            if path.startswith('/'):
                path = path[1:]
            return real_listdir(os.path.join(
                sample_data_path, sample_prefix, path))

        self.useFixture(fixtures.MonkeyPatch('os.listdir', fake_listdir))
        self.useFixture(fixtures.MonkeyPatch(
            'subprocess.check_output', mock.Mock()))

        real_path_exists = os.path.exists

        def fake_path_exists(path):
            if path.startswith('/mnt/config'):
                path = os.path.join(
                    sample_data_path, sample_prefix,
                    path[1:])
            if path in ['/etc/sysconfig/network-scripts/ifcfg-eth2',
                        '/etc/network/interfaces.d/eth2']:
                # Pretend this file exists, we need to test skipping
                # pre-existing config files
                return True
            return real_path_exists(path)

        self.useFixture(fixtures.MonkeyPatch('os.path.exists',
                                             fake_path_exists))

    def _patch_distro(self, distro_name):
        def fake_distro():
            return (distro_name, '', '')

        self.useFixture(fixtures.MonkeyPatch('platform.dist', fake_distro))

    def _assert_distro_provider(self, distro, provider):
        self._patch_argv(['--hostname'])
        self._patch_files(provider)
        self._patch_distro(distro)

        mock_call = mock.Mock()
        mock_call.return_value = 0
        self.useFixture(fixtures.MonkeyPatch('subprocess.call', mock_call))

        cmd.main()

        output_filename = '%s.%s.network.out' % (provider, distro.lower())
        output_path = os.path.join(sample_data_path, 'test', output_filename)

        # Generate a list of (dest, content) into write_blocks to assert
        write_blocks = []
        lines = open(output_path).readlines()
        write_dest = None
        write_content = None
        for line in lines:
            if line.startswith('### Write '):
                if write_dest is not None:
                    write_blocks.append((write_dest, write_content))
                write_dest = line[len('### Write '):-1]
                write_content = ''
            else:
                write_content += line
        if write_dest is not None:
            write_blocks.append((write_dest, write_content))

        for dest, content in write_blocks:
            self.assertNotIn("eth2", dest)
            self.assertIn(dest, self.file_handle_mocks)
            write_handle = self.file_handle_mocks[dest].write
            write_handle.assert_called_once_with(content)

        # Check hostname
        meta_data_path = 'mnt/config/openstack/latest/meta_data.json'
        hostname = None
        with open(os.path.join(sample_data_path, provider,
                               meta_data_path)) as fh:
            meta_data = json.load(fh)
            hostname = meta_data['name'].split('.')[0]

        mock_call.assert_called_once_with(['hostname', hostname])
        self.file_handle_mocks['/etc/hostname'].write.assert_has_calls(
            [mock.call(hostname), mock.call('\n')])

        # Check hosts entry
        self.file_handle_mocks['/etc/hosts'].write.assert_called_once_with(
            '127.0.1.1 %s\n' % hostname)

    def test_glean(self):
        self._assert_distro_provider(self.distro, self.style)
