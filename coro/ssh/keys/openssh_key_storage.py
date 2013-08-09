# Copyright (c) 2002-2012 IronPort Systems and Cisco Systems
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

#
# ssh.keys.openssh_key_storage
#
# This module is capable of loading key files that are generated by OpenSSH.
#

# XXX: Make key parse error exception.  Replace asserts.

__version__ = '$Revision: #1 $'

import key_storage
import binascii
import hashlib
import os
import re
import rebuild
import ber
import dss
import rsa

from coro.ssh.keys import openssh_key_formats
from coro.ssh.util import str_xor
from coro.ssh.util.password import get_password

from Crypto.Cipher import DES
import openssh_known_hosts
import remote_host

class OpenSSH_Key_Storage(key_storage.SSH_Key_Storage):

    header = re.compile(
            rebuild.CONCAT(
                rebuild.NAME('name', '[^:]*'),
                ':',
                rebuild.SPLAT('[ \t]'),
                rebuild.NAME('value', '.*')
                          )
                       )

    key_types = ('dsa', 'rsa')

    def get_private_key_filenames(self, username, private_key_filename):
        """get_private_key_filenames(self, username, private_key_filename) -> [filename, ...]
        Gets the filenames of the private keys.

        <username> - Look into the home directory of this user for the key.
                     If None, uses the current user.
        <private_key_filename> - If this is set, then this is the value to
                     return.  Otherwise the filename is computed from the
                     username's home directory.  This is provided as a
                     convenience to handle the situation where the filename
                     is forced to a specific value.
        """
        if private_key_filename is None:
            if username is None:
                username = os.getlogin()
            home_dir = os.path.expanduser('~' + username)
            result = []
            for key_type in self.key_types:
                private_key_filename = os.path.join(home_dir, '.ssh', 'id_%s' % key_type)
                result.append(private_key_filename)
            return result
        else:
            return [private_key_filename]

    get_private_key_filenames = classmethod(get_private_key_filenames)

    def get_public_key_filenames(self, username, public_key_filename):
        """get_public_key_filenames(self, username, public_key_filename) -> [filename, ...]
        Gets the filenames of the public keys.

        <username> - Look into the home directory of this user for the key.
                     If None, uses the current user.
        <private_key_filename> - If this is set, then this is the value to
                     return.  Otherwise the filename is computed from the
                     username's home directory.  This is provided as a
                     convenience to handle the situation where the filename
                     is forced to a specific value.
        """
        if public_key_filename is None:
            result = self.get_private_key_filenames(username, None)
            result = map(lambda x: x+'.pub', result)
            return result
        else:
            return [public_key_filename]

    get_public_key_filenames = classmethod(get_public_key_filenames)

    def load_keys(self, username=None, private_key_filename=None, public_key_filename=None):
        """load_keys(self, username=None, private_key_filename=None, public_key_filename=None) -> [public_private_key_obj, ...]
        Loads both the private and public keys.  Returns a list of
        SSH_Public_Private_Key object.  Returns an empty list if both the
        public and private keys are not available.

        <private_key_filename> - defaults to $HOME/.ssh/id_dsa or id_rsa.
        <public_key_filename>  - If set to None, then it will assume the
                                 filename is the same as
                                 <private_key_filename> with a .pub extension.
        <username> - Look into the home directory of this user for the key.
                     If None, uses the current user.
        """
        private_key_filenames = self.get_private_key_filenames(username, private_key_filename)
        result = []
        for filename in private_key_filenames:
            private_keys = self.load_private_keys(private_key_filename=filename)
            if private_keys:
                assert (len(private_keys) == 1)
                if public_key_filename is None:
                    pub_filename = filename + '.pub'
                else:
                    pub_filename = public_key_filename
                public_keys = self.load_public_keys(public_key_filename=pub_filename)
                if public_keys:
                    assert (len(public_keys) == 1)
                    # Join the two keys into one.
                    key = private_keys[0]
                    key.public_key = public_keys[0].public_key
                    result.append(key)
        return result

    def load_private_keys(self, username=None, private_key_filename=None):
        """load_private_keys(self, username=None, private_key_filename=None) -> [key_obj, ...]
        Loads the private keys with the given filename.
        Defaults to $HOME/.ssh/id_dsa or id_rsa
        Returns a list SSH_Public_Private_Key object.
        Returns an empty list if the key is not available.
        """
        private_key_filenames = self.get_private_key_filenames(username, private_key_filename)
        result = []
        for filename in private_key_filenames:
            try:
                data = open(filename).read()
            except IOError:
                pass
            else:
                result.append(self.parse_private_key(data))
        return result

    load_private_keys = classmethod(load_private_keys)

    def load_public_keys(self, username=None, public_key_filename=None):
        """load_public_keys(self, username=None, public_key_filename=None) -> [key_obj, ...]
        Loads the public keys with the given filename.
        Defaults to $HOME/.ssh/id_dsa.pub
        Returns a list of SSH_Public_Private_Key object.
        Returns an empty list if the key is not available.
        """
        public_key_filenames = self.get_public_key_filenames(username, public_key_filename)
        result = []
        for filename in public_key_filenames:
            try:
                data = open(filename).read()
            except IOError:
                pass
            else:
                result.append(self.parse_public_key(data))
        return result

    load_public_keys = classmethod(load_public_keys)

    def parse_private_key(self, private_key):
        """parse_private_key(self, private_key) -> key_obj
        Parses the given string into an SSH_Public_Private_Key object.
        """
        # Format (PEM which is RFC 1421):
        # -----BEGIN DSA PRIVATE KEY-----
        # RFC 822 headers.
        # keydata_base64
        # -----END DSA PRIVATE KEY-----
        # keydata is BER-encoded
        data = private_key.split('\n')
        self._strip_empty_surrounding_lines(data)
        for key_type in self.key_types:
            if (data[0] == '-----BEGIN %s PRIVATE KEY-----' % (key_type.upper(),) and
                data[-1] == '-----END %s PRIVATE KEY-----' % (key_type.upper(),)):
                break
        else:
            raise ValueError, 'Corrupt key header/footer format: %s %s' % (data[0],data[-1])

        key_data = []
        # XXX: Does not support multiple headers with the same name.
        headers = {}
        current_line = 1
        if ':' in data[current_line]:
            # starts with RFC 822 headers
            continuation = 0    # Flag to follow continuation line
            current_value = []
            name = ''   # pychecker squelch
            while 1:
                line = data[current_line]
                if line.startswith(' ') or line.startswith('\t') or continuation:
                    if line.endswith('\\'):
                        # Strip trailing slash.
                        line = line[:-1]
                        continuation = 1
                    else:
                        continuation = 0
                    current_value.append(line.lstrip())
                    current_line += 1
                    continue
                else:
                    if current_value:
                        headers[name] = ''.join(current_value)
                        current_value = []

                if not line:
                    # end of headers
                    break

                match = self.header.match(line)
                assert (match != None), 'Invalid header value in private key: %r' % line
                d = match.groupdict()
                name = d['name']
                value = d['value']
                if line.endswith('\\'):
                    # Continuation (see ietf-secsh-publickeyfile)
                    continuation = 1
                current_value.append(value)
                current_line += 1

        # Parse the key
        while 1:
            if data[current_line].startswith('-----'):
                break
            if data[current_line]:
                key_data.append(data[current_line])
            current_line += 1
        key_data = ''.join(key_data)
        key_data = binascii.a2b_base64(key_data)
        if headers.has_key('Proc-Type'):
            proc_type = headers['Proc-Type'].split(',')
            proc_type = map(lambda x: x.strip(), proc_type)
            if len(proc_type)==2 and proc_type[0]=='4' and proc_type[1]=='ENCRYPTED':
                # Key is encrypted.
                assert headers.has_key('DEK-Info'), 'Private key missing DEK-Info field.'
                dek_info = headers['DEK-Info'].split(',')
                dek_info = map(lambda x: x.strip(), dek_info)
                assert (len(dek_info) == 2), 'Expected two values in DEK-Info field: %r' % dek_info
                # XXX: Do we need to support more encryption types?
                assert (dek_info[0] == 'DES-EDE3-CBC'), 'Can only handle DES-EDE3-CBC encryption: %r' % dek_info[0]
                iv = binascii.a2b_hex(dek_info[1])
                passphrase = self.ask_for_passphrase()
                # Convert passphrase to a key.
                a = hashlib.md5(passphrase + iv).digest()
                b = hashlib.md5(a + passphrase + iv).digest()
                passkey = (a+b)[:24]        # Only need first 24 characters.
                key_data = self.des_ede3_cbc_decrypt(key_data, iv, passkey)

        key_data = ber.decode(key_data)[0]
        # key_data[0] is always 0???
        if not keytype_map.has_key(key_type):
            return None
        key_obj = keytype_map[key_type]()

        # Just so happens both dsa and rsa keys have 5 numbers.
        key_obj.private_key = tuple(key_data[1:6])
        return key_obj

    parse_private_key = classmethod(parse_private_key)

    def ask_for_passphrase():
        return get_password('Enter passphrase> ')

    ask_for_passphrase = staticmethod(ask_for_passphrase)

    def des_ede3_cbc_decrypt(data, iv, key):
        assert (len(data) % 8 == 0), 'Data block must be a multiple of 8: %i' % len(data)
        key1 = DES.new(key[0:8], DES.MODE_ECB)
        key2 = DES.new(key[8:16], DES.MODE_ECB)
        key3 = DES.new(key[16:24], DES.MODE_ECB)
        # Outer-CBC Mode
        # 8-byte blocks
        result = []
        prev = iv
        for i in xrange(0, len(data), 8):
            block = data[i:i+8]
            value = key1.decrypt(
                    key2.encrypt(
                    key3.decrypt(block)))
            result.append(str_xor(prev, value))
            prev = block

        return ''.join(result)

    des_ede3_cbc_decrypt = staticmethod(des_ede3_cbc_decrypt)

    def parse_public_key(public_key):
        """parse_public_key(public_key) -> key_obj
        Parses the given string into an SSH_Public_Private_Key object.
        Returns None if parsing fails.

        <public_key>: The public key as a base64 string.
        """
        # Format:
        # keytype SPACE+ base64_string [ SPACE+ comment ]
        key_match = openssh_key_formats.ssh2_key.match(public_key)
        if not key_match:
            return None
        keytype = key_match.group('keytype')
        if not keytype_map.has_key(keytype):
            return None
        try:
            key = binascii.a2b_base64(key_match.group('base64_key'))
        except binascii.Error:
            return None
        key_obj = keytype_map[keytype]()
        key_obj.set_public_key(key)
        return key_obj

    parse_public_key = staticmethod(parse_public_key)

    def _strip_empty_surrounding_lines(data):
        while 1:
            if not data[0]:
                del data[0]
            else:
                break
        while 1:
            if not data[-1]:
                del data[-1]
            else:
                break

    _strip_empty_surrounding_lines = staticmethod(_strip_empty_surrounding_lines)

    def get_authorized_keys_filename(username, authorized_keys_filename=None):
        if authorized_keys_filename is None:
            if username is None:
                username = os.getlogin()
            home_dir = os.path.expanduser('~' + username)
            authorized_keys_filename = os.path.join(home_dir, '.ssh', 'authorized_keys')
        return authorized_keys_filename

    get_authorized_keys_filename = staticmethod(get_authorized_keys_filename)

    def verify(self, host_id, server_key_types, public_host_key, username=None):
        for key in server_key_types:
            if public_host_key.name == key.name:
                # This is a supported key type.
                if self._verify_contains(host_id, public_host_key, username):
                    return 1
        return 0

    verify.__doc__ = key_storage.SSH_Key_Storage.verify.__doc__

    verify = classmethod(verify)

    def _verify_contains(host_id, key, username):
        """_verify_contains(host_id, key, username) -> boolean
        Checks whether <key> is in the known_hosts file.
        """
        # Currently only supported IPv4
        if not isinstance(host_id, remote_host.IPv4_Remote_Host_ID):
            return 0
        hostfile = openssh_known_hosts.OpenSSH_Known_Hosts()
        return hostfile.check_for_host(host_id, key, username)

    _verify_contains = staticmethod(_verify_contains)

    def update_known_hosts(host, public_host_key, username=None):
        hostfile = openssh_known_hosts.OpenSSH_Known_Hosts()
        hostfile.update_known_hosts(host, public_host_key, username)

    update_known_hosts.__doc__ = key_storage.SSH_Key_Storage.update_known_hosts.__doc__

    update_known_hosts = staticmethod(update_known_hosts)


keytype_map = {'ssh-dss': dss.SSH_DSS,
               'dss': dss.SSH_DSS,
               'dsa': dss.SSH_DSS,
               'ssh-rsa': rsa.SSH_RSA,
               'rsa': rsa.SSH_RSA,
#               'rsa1': None
              }

import unittest

class ssh_key_storage_test_case(unittest.TestCase):
    pass

class load_dsa_test_case(ssh_key_storage_test_case):

    def runTest(self):
        public_key = 'ssh-dss AAAAB3NzaC1kc3MAAACBAM46u7kMaoOESTiF3fwqvKry2YSYwlgcl2fRtw5IBgLyeS5SLy/M18ZeGLBokFSAFN110B4X6mUK05VMn3KGo0xKnu35+s4g20vOn9ubjXzUkt4EORJZ+MPPaQOllW22m5fjutND3SzahUOx9Z/PaTSbRLGovpTA7NjlliUVt32rAAAAFQCgqkv3v9z16r0z36InixKZTeWcIQAAAIB7qsZKumVthTLCzj/nAgOvdehLm8PbpWAYe8g1QyAGhbyB0MTwak0TvtBrxCq1nbCkYuFdPVtAWw7Q6fk4nf+3vNiKIl55lVMmUpJ2KkGBJDuEuMUWPRiiJZwW+KxKUyB7pKY5gwJt4DLGlfVjQW4q+b0qm83k/XUoW3VW/L4TIAAAAIAQbMUcClGzedoL7bIf4vh7DiQedlMaTM66EL8awJAQBNfAc9au84J0yMz84/6Dub2h+XwP6Ip5E+QjD32grBgj2MV3orjeXa3GKEbmLV9+3asZKma+gzfQurz0rfR767vp5p4ZScODAp/u64FrMQeiMLD0TePAOhDX7Y6ON5AOlw== admin@test04.god\n'
        public_key_value = (144819228510396375480510966045726324197234443151241728654670685625305230385467763734653299992854300412367868856607501321634131298084648429649714452472261648519166487595581105734370788168033696455943547609540069712392591019911289209306656760054646817215504894551439102079913490941604156000063251698742214491563L,
                            917236267741783881593757959752012731596818193441L,
                            86841982599782731711680786695115998714268381589063264016290973272276727186587704621026934440034006184167355751218909451695519359588918346763818149790632054843515968619897385312275286742098283669267848018249714288204339152924266843441212538369167733167339150986744361013446636969482251895247384926570829189920L,
                            11533944838210201987952882615702205024058326377484185868096298195008186074503068022497845512065175251915059614398323642650025427428897101740618300183253025704503580499048317459491367465314627384976328186431247669440664650252937901083193085980371291330875135658464646502031914168659603641006946305696513134231L)
        private_key = """-----BEGIN DSA PRIVATE KEY-----
MIIBuwIBAAKBgQDOOru5DGqDhEk4hd38Kryq8tmEmMJYHJdn0bcOSAYC8nkuUi8v
zNfGXhiwaJBUgBTdddAeF+plCtOVTJ9yhqNMSp7t+frOINtLzp/bm4181JLeBDkS
WfjDz2kDpZVttpuX47rTQ90s2oVDsfWfz2k0m0SxqL6UwOzY5ZYlFbd9qwIVAKCq
S/e/3PXqvTPfoieLEplN5ZwhAoGAe6rGSrplbYUyws4/5wIDr3XoS5vD26VgGHvI
NUMgBoW8gdDE8GpNE77Qa8QqtZ2wpGLhXT1bQFsO0On5OJ3/t7zYiiJeeZVTJlKS
dipBgSQ7hLjFFj0YoiWcFvisSlMge6SmOYMCbeAyxpX1Y0FuKvm9KpvN5P11KFt1
Vvy+EyACgYAQbMUcClGzedoL7bIf4vh7DiQedlMaTM66EL8awJAQBNfAc9au84J0
yMz84/6Dub2h+XwP6Ip5E+QjD32grBgj2MV3orjeXa3GKEbmLV9+3asZKma+gzfQ
urz0rfR767vp5p4ZScODAp/u64FrMQeiMLD0TePAOhDX7Y6ON5AOlwIVAIJE+2W3
jbdJzPIVCZV/ns8QD/HE
-----END DSA PRIVATE KEY-----"""
        private_key_value = (144819228510396375480510966045726324197234443151241728654670685625305230385467763734653299992854300412367868856607501321634131298084648429649714452472261648519166487595581105734370788168033696455943547609540069712392591019911289209306656760054646817215504894551439102079913490941604156000063251698742214491563L,
                             917236267741783881593757959752012731596818193441L,
                             86841982599782731711680786695115998714268381589063264016290973272276727186587704621026934440034006184167355751218909451695519359588918346763818149790632054843515968619897385312275286742098283669267848018249714288204339152924266843441212538369167733167339150986744361013446636969482251895247384926570829189920L,
                             11533944838210201987952882615702205024058326377484185868096298195008186074503068022497845512065175251915059614398323642650025427428897101740618300183253025704503580499048317459491367465314627384976328186431247669440664650252937901083193085980371291330875135658464646502031914168659603641006946305696513134231L,
                             743707150676871705974360193988282239149564490180L)

        encrypted_private_key = """-----BEGIN DSA PRIVATE KEY-----
Proc-Type: 4,ENCRYPTED
DEK-Info: DES-EDE3-CBC,4D88293673F5EB5A

z9/jHRxmgWErlTqA4+BsGWLnFFYJAUSDKLv2mbB7vsTkdHggJGUFm370hXR494R2
kV8zTcdl8xNaD5O9wv2TxAa+1XAK9PMvoZ22EicJkJfdVZB1WxOEo6gYafcbUwn5
jCw2WtOdmFL1LfBTKJUQsN3+s/Z/8xIFjHyZ1AqBEsbtvyT5x6gTCb5gqd9aLmAy
U1MSAS69G83XS0vwGjUBu6hIY+NqH97MYaYuUxYRHZ4kwx6a+AZA05VAWuqCNG/i
REnwjW62umnal6bAb/P0ShV/5Q1N+jOgfbAVeSbwOBNi6l0a3R2vsACowBsQGIOs
AI0LkOljn0SQ9EbiVFt0X3EmDDqXJ4pyUQiQWWVqk/NdlcOXlVHxW1LAH178prSh
9lynvklH9ddxf1ogZzoklnwbHFOQL82VP3OgHzLe4zHEZb7/7n04Hsn65tE1IDPN
BZCMWNmWn5b4XlpvM9qmrqw7OSHXJmo3pUuobgMDJY8ivajqqgLnKobNUyRqIIXe
K+HsnOddon8EQ7paJXiQtIoduGkNprkteopuVCTPVnJ7iPH7nlZlklRzMd0Nf6HX
uUG2MBh5S6IgJq3XFEqkLfnz1kLZTEqa
-----END DSA PRIVATE KEY-----"""

        encrypted_private_key_value = (164075852029082894163234846758911180897102424744594692831708895466836370115659341783053385481002813494629146363751701674649183075680471787603685864085093058760568072639047095709834084191969571447062510635065846311615327171352818023633056996941311207910795837452601272338087606069040250703385973352638645677247L,
                                       1382042759715880151069055791721895992148320772021L,
                                       153926732596083235894968118340186482799172595631686494839668449588513699006316353000942531708584009247814149841973530532258546565044160391136120217307693710508485285457287378935130848066293114662648349996271228116599598513247673155335538020265820918414007609749609438690078201904948848717674434559570846935983L,
                                       72384903337992313747768521223976137917296105324177373979721513786321354786021736609201334497124282719580897296769614546413169191983285786187571827959205578548773194544237603360013637593100703429306046520179789796054440223880857876564434231726404752178503857092191315752338544897832163988229715776540342317876L,
                                       863501795884281323360678431361598105234793720895L)

        a = OpenSSH_Key_Storage()
        dss_obj = a.parse_public_key(public_key)
        self.assertEqual(dss_obj.public_key, public_key_value)

        dss_obj = a.parse_private_key(private_key)
        self.assertEqual(dss_obj.private_key, private_key_value)

        # Try an encrypted private key.
        class fixed_passphrase_OpenSSH_Key_Storage(OpenSSH_Key_Storage):
            def ask_for_passphrase():
                return 'foobar'
            ask_for_passphrase = staticmethod(ask_for_passphrase)

        a = fixed_passphrase_OpenSSH_Key_Storage()
        dss_obj = a.parse_private_key(encrypted_private_key)
        self.assertEqual(dss_obj.private_key, encrypted_private_key_value)


class load_rsa_test_case(ssh_key_storage_test_case):

    def runTest(self):
        public_key = 'ssh-rsa AAAAB3NzaC1yc2EAAAABIwAAAIEAqmjoccK1YhAhSC4TzycQ/61EtlbbeoeJDdaK03523oNbLdxw5snrsu1cpiR6xlPo5hBsaVBOvBeoQt+SrGwTy12P76i0LCyj6G+ylyHelWG5AsH4PyOETZonEaQiGozCHFXjZ4s/fQ0JjJ55zmhGgqpQNnz2SmqpjZjCipJX70c= admin@test04.god\n'
        public_key_value = (35L,
                            119665828850037267028328680471142741797655500899213447140209093165296153783302091227178836947120698323025616662599207205000714031772597163041046686901539652734222225828489682382176199509894978250917122539502090949162883932641842735933478535393127460177054018760505497941735313342238826050943434409531493838663L)
        private_key = """-----BEGIN RSA PRIVATE KEY-----
MIICWgIBAAKBgQCqaOhxwrViECFILhPPJxD/rUS2Vtt6h4kN1orTfnbeg1st3HDm
yeuy7VymJHrGU+jmEGxpUE68F6hC35KsbBPLXY/vqLQsLKPob7KXId6VYbkCwfg/
I4RNmicRpCIajMIcVeNniz99DQmMnnnOaEaCqlA2fPZKaqmNmMKKklfvRwIBIwKB
gD9LiYlW8unou+ecFfx8OYOJf+v0YCYyV3orHZ8DFjVj/UuMZHL6ir7NMQqClACF
kQT+yS5uSSFKnZUueE6rzNXmW+TSXbkYx+Ews60gC1gkbKpV45oKZhg1yfRErwOy
JF8VPQzstIZdf0iWU3uQ+6T64CrPLz0c40b5I47ux5mbAkEA3r2aeIQsftWedjgx
+mXwtCJb1+3oOcbz3tPoZGTqvLWReuQ5ZmZIj06Lg3cy3sNH5+pjDCmiQlH8Eo7E
fFM8jwJBAMPa7SEzquE9/2KqV/iVLYaZedtqfRyDGIz4XIbSTT80dyuplTFpVJWz
Fks1ie97Q4FswqV03D0HXEnnu2vD7ckCQHjqla8jLhj3nyo7w1wLdAoEBfjgPDyf
M+3+AdAZhr42rg+DNRpUyE3LjZCCiVRbYYyGjYpCfKeo2UvnGjTcunkCQBDJn0v9
HUaBqC0HSV5zL8m1Yjdg5icD7ClXHd+roDieGNfbVe5K211J3VbnVPdFFGot5Mxa
eUcPQmy8F2ECKlMCQQDX7sV4IqQrPXGC3TpHXNMJfRy+RuGNFH3mf5j3iAhgnSc5
Sk5Qdbor2yjwE/GHz2ycGtkjRulOUTv4TYX5+O6u
-----END RSA PRIVATE KEY-----"""
        private_key_value = (119665828850037267028328680471142741797655500899213447140209093165296153783302091227178836947120698323025616662599207205000714031772597163041046686901539652734222225828489682382176199509894978250917122539502090949162883932641842735933478535393127460177054018760505497941735313342238826050943434409531493838663L,
                             35L,
                             44447307858585270610522081317853018381986328905422137509220520318538571405226491027237853723216259377123800474679705533285979497515536089129531626563429005729644097293794274690875731684502848992888319583322382841862406829655518405505486920254346175585047037778915301016757980191172721050366334657821864860059L,
                             11665873813839313890672267141763021801562202172556393999332152934535676204166852333822550370230102963227543021845798110514178180132425544663462167708908687L,
                             10257768150044345052157551318596288583246323320335701676273534238701739242861950987619850286757345117997197327072483122544803209060028258639176304917999049L)

        encrypted_private_key = """-----BEGIN RSA PRIVATE KEY-----
Proc-Type: 4,ENCRYPTED
DEK-Info: DES-EDE3-CBC,09F6C5E0A7DE5062

12K8ifZGdXBydKq73mrFhrZ3MQvesgCHnSF5DAFt6xka89S7LC8fGxyxKtUNxewF
SZ+9m6A+/ZvD5vsI8xSicdEToFKDIzjBdPg6/2c2PZ5qQn46fQUjoRh3HVxMwe3j
y2c8s9z62aprf6P+QrllcI2h1dSoGpiS2v/CaeZSAo+cDnULrX7ICraVLx7TMdsz
vXuaf4Qa+CcunYOl9fSMAATUZD3LiRAICLZxcsT7MaqVLerWquZ54kOnOLAXMMDb
bth0+DlErt7Er2zjCWz+GSZY5QG663FTBVVVhgQGrj3D9T9VgvMVOneqJHGgkHB8
OYP5Lzw3ukNxlxP8L5Is22S7dGqHNYAoqecS8l6kPkrChRosVHcyl0WViKKuUacm
Oeeh7bYiQNtWy7yXWfwbA/qV46rQqb9jvoK0X0poL1QxLnayZtE2Py5AwGT17MLE
Fgcf5aXRk8BlEhS7Cxx7bTFfgC4reV/SL6D+bWYU91YuWx+Ivr1W/WF7JGBhY+PC
PuYX4L0U1btWcj5Y35ZZQX3iLM5Qo+39gL8YJ8Ee+F51MEu89yuSJrxpbanec55r
iawSKr0WOyY44GA3sfRGKbr6EN56QoR938S0nVAwYCPqwJz00+7ElpLNgH4Utjwj
68pP4jQTVGgI7K4gxN2jDvlol/dTprmjXmyHykW7s7s5Ew4wrN+qMFpgeyIz9/qc
5L0OtjBAbjyLFdE0Xngg0Lmn2bIlvL8jrMPGwaxqu2T0ulrLN8Z2G+1iAafj8Kqh
VaPIN0x5mSV39WpxGz4SmIOVZdIlUL9dOJEv4K4qOHQ=
-----END RSA PRIVATE KEY-----"""

        encrypted_private_key_value = (132188032201840059483513934225114077156533953079152741406965569217951447670952039954408688762434667064008713729945007603706264832329500562212083089065564763658016112303811971922789074398850943636984349987490511238526103841681580491336332634066855256218075929727422563972932130562723997143531042508709389080193L,
                                       35L,
                                       86866421161209181946309156776503536417150883452014658638863088343225237040911340541468566901028495499205726165392433568149831175530814655167940315671656829536913792735395949882296110745152030053580749430717046323948708357601049256155974132066189771161275225056736507873625291084649019736200813189569105066383L,
                                       12385928121051751494808912011530512750393016832123879182480209182965942204322814452624601155140075558662348021403304814310741397421628588564328911043663179L,
                                       10672436567524284031640817763908324234466779420903824519134916712505476909909154093286756280899157968011858280387816948898605556328090289066804367098576867L)

        a = OpenSSH_Key_Storage()
        rsa_obj = a.parse_public_key(public_key)
        self.assertEqual(rsa_obj.public_key, public_key_value)

        rsa_obj = a.parse_private_key(private_key)
        self.assertEqual(rsa_obj.private_key, private_key_value)

        # Try an encrypted private key.
        class fixed_passphrase_OpenSSH_Key_Storage(OpenSSH_Key_Storage):
            def ask_for_passphrase():
                return 'foobar'
            ask_for_passphrase = staticmethod(ask_for_passphrase)

        a = fixed_passphrase_OpenSSH_Key_Storage()
        rsa_obj = a.parse_private_key(encrypted_private_key)
        self.assertEqual(rsa_obj.private_key, encrypted_private_key_value)

def suite():
    suite = unittest.TestSuite()
    suite.addTest(load_dsa_test_case())
    suite.addTest(load_rsa_test_case())
    return suite

if __name__ == '__main__':
    unittest.main(defaultTest='suite')
