#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 The Meson Authors

import argparse
import os
import sys
import tempfile

from mesonbuild import environment, mesonlib, mlog, compilers
from mesonbuild.utils.universal import set_meson_command, File
from mesonbuild.coredata import CoreData

def run_compiler_checks(cross_file, name):
    with tempfile.TemporaryDirectory() as temp_dir:
        options = argparse.Namespace(cross_file=[cross_file] if cross_file else [], native_file=[], cmd_line_options={})
        env = environment.Environment(os.getcwd(), temp_dir, options)
        cc = compilers.detect_c_compiler(env, mesonlib.MachineChoice.HOST)
        cpp = compilers.detect_cpp_compiler(env, mesonlib.MachineChoice.HOST)

        c_compiles_fails = []
        c_links_fails = []
        c_headers_fails = []
        c_header_symbols_fails = {}
        c_functions_fails = []
        c_function_attributes_fails = []
        c_members_fails = {}
        c_supported_arguments_fails = []
        c_supported_link_arguments_fails = []
        cpp_supported_arguments_fails = []

        print('[[toolchain]]')
        print(f'name = "{name}"')
        print('')
        print('[toolchain.host_machine]')
        print(f'cpu_family = "{env.machines.host.cpu_family}"')
        print(f'cpu = "{env.machines.host.cpu}"')
        print(f'system = "{env.machines.host.system}"')
        print(f'endian = "{env.machines.host.endian}"')
        print('')
        print('[toolchain.c]')
        print(f'compiler_id = "{cc.get_id()}"')
        print(f'linker_id = "{cc.get_linker_id()}"')
        print(f'version = "{cc.version}"')
        print('')
        print('[toolchain.cpp]')
        print(f'compiler_id = "{cpp.get_id()}"')
        print(f'linker_id = "{cpp.get_linker_id()}"')
        print(f'version = "{cpp.version}"')

        try:
            rustc = compilers.detect_rust_compiler(env, mesonlib.MachineChoice.HOST)
            print('')
            print('[toolchain.rust]')
            print(f'compiler_id = "{rustc.get_id()}"')
            print(f'linker_id = "{rustc.get_linker_id()}"')
            print(f'version = "{rustc.version}"')
        except mesonlib.MesonException:
            pass

        if cc.get_id() == 'gcc' and mesonlib.version_compare(cc.version, '< 4.4.6'):
            raise mesonlib.MesonException('When using GCC, version 4.4.6 or later is required.')

        if not cc.has_multi_link_arguments(['-Wl,--gdb-index'], env)[0]:
            c_supported_link_arguments_fails.append('-Wl,--gdb-index')

        for b in ['bswap32', 'bswap64', 'clz', 'clzll', 'ctz', 'expect', 'ffs',
                  'ffsll', 'popcount', 'popcountll', 'unreachable', 'types_compatible_p']:
            if not cc.has_function(b, '', env)[0]:
                c_functions_fails.append(b)

        _attributes = [
            'const', 'flatten', 'malloc', 'pure', 'unused', 'warn_unused_result',
            'weak', 'format', 'packed', 'returns_nonnull', 'alias', 'noreturn',
        ]
        for attr in _attributes:
            if not cc.has_func_attribute(attr, env)[0]:
                c_function_attributes_fails.append(attr)

        if not cc.has_func_attribute('visibility:hidden', env)[0]:
            c_function_attributes_fails.append('visibility:hidden')

        if not cc.compiles('__uint128_t foo(void) { return 0; }', env, extra_args=[])[0]:
            c_compiles_fails.append('__uint128_t')

        if not cc.has_function('reallocarray', '', env)[0]:
            c_functions_fails.append('reallocarray')
        if not cc.has_function('fmemopen', '', env)[0]:
            c_functions_fails.append('fmemopen')

        if not cc.links('static char unused() { return 5; } int main() { return 0; }',
                        env, extra_args=['-Wl,--gc-sections'])[0]:
            c_links_fails.append('gc-sections')

        if not cc.compiles('''#include <stdint.h>
                          int main() {
                            struct {
                              uint64_t *v;
                            } x;
                            return (int)__atomic_load_n(x.v, __ATOMIC_ACQUIRE) &
                                   (int)__atomic_add_fetch(x.v, (uint64_t)1, __ATOMIC_ACQ_REL);
                          }''', env)[0]:
            c_compiles_fails.append('GCC atomic builtins')
        if not cc.links('''#include <stdint.h>
                           uint64_t v;
                           int main() {
                             return __sync_add_and_fetch(&v, (uint64_t)1);
                           }''', env)[0]:
            c_links_fails.append('GCC 64bit atomics')

        if not (cc.has_header_symbol('sys/sysmacros.h', 'major', '', env)[0] and
                cc.has_header_symbol('sys/sysmacros.h', 'minor', '', env)[0] and
                cc.has_header_symbol('sys/sysmacros.h', 'makedev', '', env)[0]):
            c_header_symbols_fails['sys/sysmacros.h'] = ['major', 'minor', 'makedev']

        if not cc.check_header('sched.h', '', env)[0]:
            c_headers_fails.append('sched.h')
        elif not cc.has_function('sched_getaffinity', '', env)[0]:
            c_functions_fails.append('sched_getaffinity')

        for h in ['xlocale.h', 'linux/futex.h', 'endian.h', 'dlfcn.h', 'sys/shm.h',
                  'cet.h', 'sys/inotify.h', 'linux/udmabuf.h']:
            if not cc.check_header(h, '', env)[0]:
                c_headers_fails.append(h)

        if not cc.has_header_symbol('time.h', 'struct timespec', '', env)[0]:
            c_header_symbols_fails['time.h'] = ['struct timespec']

        if not cc.has_function('posix_memalign', '', env)[0]:
            c_functions_fails.append('posix_memalign')

        if not cc.has_members('struct dirent', ['d_type'], prefix='''#include <sys/types.h>
           #include <dirent.h>''', env=env)[0]:
            c_members_fails['struct dirent'] = ['d_type']

        if not cc.links('int main() { return 0; }', env, extra_args=['-Wl,-Bsymbolic'])[0]:
            c_links_fails.append('Bsymbolic')

        if not cc.has_function('dladdr', '', env)[0]:
            c_functions_fails.append('dladdr')

        if not cc.has_function('dl_iterate_phdr', '', env)[0]:
            c_functions_fails.append('dl_iterate_phdr')

        if not cc.has_function('clock_gettime', '', env)[0]:
            c_functions_fails.append('clock_gettime')

        if not cc.links('''#define _GNU_SOURCE
#include <stdlib.h>
#include <locale.h>
#ifdef HAVE_XLOCALE_H
#include <xlocale.h>
#endif
int main() {
  locale_t loc = newlocale(LC_CTYPE_MASK, "C", NULL);
  const char *s = "1.0";
  char *end;
  double d = strtod_l(s, &end, loc);
  float f = strtof_l(s, &end, loc);
  freelocale(loc);
  return 0;
}''', env)[0]:
            c_links_fails.append('xlocale')
        else:
            functions_to_detect = {
                'strtof': '',
                'mkostemp': '',
                'memfd_create': '',
                'flock': '',
                'strtok_r': '',
                'getrandom': '',
                'qsort_s': '',
                'posix_fallocate': '',
                'sysconf': '#include <unistd.h>',
            }
            for f, prefix in functions_to_detect.items():
                if not cc.has_function(f, prefix, env)[0]:
                    c_functions_fails.append(f)

        if not cc.has_multi_link_arguments(['-Wl,--build-id=sha1'], env)[0]:
            c_supported_link_arguments_fails.append('-Wl,--build-id=sha1')

        if cc.get_argument_syntax() != 'msvc':
            _trial_c = [
                '-Werror=implicit-function-declaration',
                '-Werror=missing-prototypes',
                '-Werror=return-type',
                '-Werror=empty-body',
                '-Werror=gnu-empty-initializer',
                '-Werror=incompatible-pointer-types',
                '-Werror=int-conversion',
                '-Werror=pointer-arith',
                '-Werror=vla',
                '-Wimplicit-fallthrough',
                '-Wmisleading-indentation',
                '-Wno-missing-field-initializers',
                '-Wno-format-truncation',
                '-fno-math-errno',
                '-fno-trapping-math',
                '-Qunused-arguments',
                '-fno-common',
                '-Wno-initializer-overrides',
                '-Wno-override-init',
                '-Wno-unknown-pragmas',
                '-Wno-microsoft-enum-value',
                '-Wno-unused-function',
                '-Werror=format',
                '-Wformat-security',
                '-Werror=thread-safety',
                '-ffunction-sections',
                '-fdata-sections',
            ]
            _trial_cpp = [
                '-Werror=return-type',
                '-Werror=empty-body',
                '-Wmisleading-indentation',
                '-Wno-non-virtual-dtor',
                '-Wno-missing-field-initializers',
                '-Wno-format-truncation',
                '-fno-math-errno',
                '-fno-trapping-math',
                '-Qunused-arguments',
                '-Wno-unknown-pragmas',
                '-Wno-microsoft-enum-value',
                '-Werror=format',
                '-Wformat-security',
                '-ffunction-sections',
                '-fdata-sections',
                '-Werror=pointer-arith',
                '-Werror=vla',
                '-Werror=gnu-empty-initializer',
            ]
            for arg in _trial_c:
                args = [arg] if arg.startswith('-Werror=') else ['-Werror', arg]
                if not cc.has_multi_arguments(args, env)[0]:
                    c_supported_arguments_fails.append(arg)
            for arg in _trial_cpp:
                args = [arg] if arg.startswith('-Werror=') else ['-Werror', arg]
                if not cpp.has_multi_arguments(args, env)[0]:
                    cpp_supported_arguments_fails.append(arg)

        output = '\n'

        if c_compiles_fails:
            output += '[toolchain.c.compiles.fails]\n'
            for i in c_compiles_fails:
                output += f'"{i}" = true\n'
            output += '\n'

        if c_links_fails:
            output += '[toolchain.c.links.fails]\n'
            for i in c_links_fails:
                output += f'"{i}" = true\n'
            output += '\n'

        if c_headers_fails:
            output += '[toolchain.c.check_header.fails]\n'
            for i in c_headers_fails:
                output += f'"{i}" = true\n'
            output += '\n'

        if c_header_symbols_fails:
            output += '[toolchain.c.has_header_symbol.fails]\n'
            for h, s_list in c_header_symbols_fails.items():
                symbols_str_list = []
                for s in s_list:
                    symbols_str_list.append(f'{s} = true')
                output += f'"{h}" = {{ {", ".join(symbols_str_list)} }}\n'
            output += '\n'

        if c_functions_fails:
            output += '[toolchain.c.has_function.fails]\n'
            for i in c_functions_fails:
                output += f'{i} = true\n'
            output += '\n'

        if c_function_attributes_fails:
            output += '[toolchain.c.has_function_attribute.fails]\n'
            for i in c_function_attributes_fails:
                output += f'"{i}" = true\n'
            output += '\n'

        if c_members_fails:
            output += '[toolchain.c.has_member.fails]\n'
            for s, m_list in c_members_fails.items():
                members_str_list = []
                for m in m_list:
                    members_str_list.append(f'{m} = true')
                output += f'"{s}" = {{ {", ".join(members_str_list)} }}\n'
            output += '\n'

        if c_supported_arguments_fails:
            output += '[toolchain.c.supported_arguments.fails]\n'
            output += 'args = [\n'
            for i in c_supported_arguments_fails:
                output += f'    "{i}",\n'
            output += ']\n'

        if c_supported_link_arguments_fails:
            output += '[toolchain.c.supported_link_arguments.fails]\n'
            output += 'args = [\n'
            for i in c_supported_link_arguments_fails:
                output += f'    "{i}",\n'
            output += ']\n'

        if cpp_supported_arguments_fails:
            output += '[toolchain.cpp.supported_arguments.fails]\n'
            output += 'args = [\n'
            for i in cpp_supported_arguments_fails:
                output += f'    "{i}",\n'
            output += ']\n'

        output = output.rstrip('\n')
        print(output)

def main():
    mlog.set_quiet()
    set_meson_command('NULL')
    parser = argparse.ArgumentParser()
    parser.add_argument('--cross-file', default=None)
    parser.add_argument('--name', required=True)
    args = parser.parse_args()
    run_compiler_checks(args.cross_file, args.name)

if __name__ == '__main__':
    sys.exit(main())
