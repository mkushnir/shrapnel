# $Header: //prod/main/ap/shrapnel/setup.py#17 $
#!/usr/bin/env python

import sys
import glob
import os
import subprocess

from distribute_setup import use_setuptools
use_setuptools()
from setuptools import setup, find_packages

try:
    from Cython.Distutils import build_ext
    from Cython.Distutils.extension import Extension
except ImportError:
    sys.stderr.write (
        '\nThe Cython compiler is required to build Shrapnel.\n'
        '  Try "pip install cython"\n'
        '  *or* "easy_install cython"\n'
        )
    sys.exit (-1)

include_dir = os.getcwd()

def newer(x, y):
    x_mtime = os.path.getmtime(x)
    try:
        y_mtime = os.path.getmtime(y)
    except OSError:
        return True
    return x_mtime > y_mtime

def exit_ok(status):
    return os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0

def check_lio():
    if os.getenv('BUILDING') is not None:
        # Doing this in a build environment.
        # This is a terrible hack.  However, there is no easy way to add
        # arbitrary options to distutils.
        return True
    if newer('test/build/test_lio.c', 'test/build/test_lio'):
        status = os.system('gcc -o test/build/test_lio test/build/test_lio.c')
        if not exit_ok(status):
            return False
    status = os.system('test/build/test_lio')
    return exit_ok(status)

_glibc = -1
_glibc_minor = -1
def check_libc():
    global _glibc, _glibc_minor
    if sys.platform.startswith('linux'):
        f = open('main.c', 'w')
        f.write('''/**/
#include <stdio.h>
#include <features.h>
int main(void) { printf("%d %d", __GLIBC__, __GLIBC_MINOR__); return 0; }
''')
        f.close()
        subprocess.call([os.getenv('CC', 'cc'), 'main.c'])
        if os.path.isfile('a.out'):
            p = subprocess.Popen(['./a.out'], stdout=subprocess.PIPE)
            stdoutdata, stderrdata = p.communicate()
            if stdoutdata:
                pieces = stdoutdata.split()
                _glibc = int(pieces[0])
                _glibc_minor = int(pieces[1])
        for i in ('main.c', 'a.out'):
            try:
                os.unlink(i)
            except:
                pass

check_libc()
compile_time_env = {
    'COMPILE_LIO': check_lio(),
    'COMPILE_NETDEV' : False,
    'COMPILE_LZO' : False,
    'COMPILE_LZ4' : False,
    'CORO_DEBUG': False,
    '__GLIBC__': _glibc,
    '__GLIBC_MINOR__': _glibc_minor,
    }

#--------------------------------------------------------------------------------
# OpenSSL support 
#--------------------------------------------------------------------------------
import os
import sys

# If you need NPN support (for SPDY), you most likely will have to link against
#   newer openssl than the one that came with your OS.  (this is circa 2012).
# 1) change the value of ossl_base below.
# 2) change the value of either 'libraries' or 'extra_link_args' depending on
#    your platform.
#    For OS X: use 'manual static link'

# statically link is a bit tricky
# Note: be sure to remove coro/ssl/openssl.c if you change this, see NPN probe below.
#ossl_base = '/Users/rushing/src/openssl-1.0.1c'
ossl_base = '/usr'

def O (path):
    return os.path.join (ossl_base, path)

# cheap probe for npn support
USE_NPN = (open (O('include/openssl/ssl.h')).read().find ('next_protos') != -1)

if USE_NPN:
    sys.stderr.write ('detected NPN-capable OpenSSL\n')
else:
    sys.stderr.write ('NPN support disabled.  Needs OpenSSL-1.0.1+\n')

OpenSSL_Extension = Extension (
    'coro.ssl.openssl',
    ['coro/ssl/openssl.pyx'],
    depends=['coro/ssl/openssl.pxi'],
    # manual static link
    #extra_link_args = [ O('libcrypto.a'), O('libssl.a') ],
    # link to an absolute location
    #extra_link_args = [ '-L %s -lcrypto -lssl' % (ossl_base,) ]
    # 'normal' link
    libraries = ['crypto', 'ssl'],
    include_dirs = [ O('include') ],
    cython_compile_time_env = {'NPN' : USE_NPN},
    )
#--------------------------------------------------------------------------------

setup (
    name='coro',
    version='1.0.3-000-p05',
    description='IronPort Coroutine/Threading Library',
    author='Sam Rushing, Eric Huss, IronPort Engineering',
    author_email='sam-coro@rushing.nightmare.com',
    license = "MIT",
    url = "http://github.com/ironport/shrapnel",
    ext_modules = [
        Extension(
            'coro.event_queue',
            ['coro/event_queue.pyx'],
            language='c++',
            depends=[os.path.join(include_dir, 'pyrex', 'python.pxi'),],
            pyrex_include_dirs=[
                os.path.join(include_dir, '.'),
                os.path.join(include_dir, 'pyrex'),
                ],),
        Extension (
            'coro._coro',
            ['coro/_coro.pyx', 'coro/swap.c'],
            extra_compile_args = ['-Wno-unused-function'],
            depends = (
                glob.glob('coro/*.pyx') +
                glob.glob('coro/*.pxi') +
                glob.glob('coro/*.pxd') + [
                    os.path.join(include_dir, 'pyrex', 'python.pxi'),
                    os.path.join(include_dir, 'pyrex', 'pyrex_helpers.pyx'),
                    os.path.join(include_dir, 'include', 'pyrex_helpers.h'),
                    os.path.join(include_dir, 'pyrex', 'tsc_time_include.pyx'),
                    os.path.join(include_dir, 'include', 'tsc_time.h'),
                    os.path.join(include_dir, 'pyrex', 'libc.pxd'),
                    ]
                 ),
            pyrex_include_dirs=[
                os.path.join(include_dir, '.'),
                os.path.join(include_dir, 'pyrex'),
                ],
            include_dirs=[
                os.path.join(include_dir, '.'),
                os.path.join(include_dir, 'include'),
                ],
            pyrex_compile_time_env = compile_time_env,
            # to enable LZO|LZ4 for stack compression, set COMPILE_LZO|COMPILE_LZ4 above
            #   and uncomment one of the following:
            #libraries=['lzo2', 'z']
            #libraries=['lz4', 'z'],
            libraries=['z']
            ),
        Extension ('coro.oserrors', ['coro/oserrors.pyx', ],),
        Extension ('coro.dns.packet', ['coro/dns/packet.pyx', ],),
        Extension ('coro.dns.surf', ['coro/dns/surf.pyx',],),
        Extension ('coro.lru', ['coro/lru.pyx'], ),
        Extension ('coro.asn1.ber', ['coro/asn1/ber.pyx'],),
        Extension ('coro.db.postgres.proto', ['coro/db/postgres/proto.pyx'],),
        Extension ('coro.ldap.query', ['coro/ldap/query.pyx'],),
        Extension ('coro.http.zspdy', ['coro/http/zspdy.pyx'], include_dirs=['coro'], libraries=['z'], depends=['coro/zlib.pxd']),
        Extension (
            'coro.clocks.tsc_time',
            ['coro/clocks/tsc_time.pyx', ],
            pyrex_include_dirs=[os.path.join(include_dir, 'pyrex')],
            pyrex_compile_time_env = compile_time_env,
            include_dirs=[
                os.path.join(include_dir, '.'),
                os.path.join(include_dir, 'include'),
                ],
            ),
        # the pre-computed openssl extension from above
        OpenSSL_Extension,
        ],
    packages= find_packages(),
    py_modules = ['backdoor', 'coro.read_stream', 'coro_process', 'coro_unittest',],
    download_url = 'http://github.com/ironport/shrapnel/tarball/master#egg=coro-1.0.2',
    install_requires = ['Cython>=0.12.1', 'distribute>=0.6.16'],
    cmdclass={'build_ext': build_ext},
)
