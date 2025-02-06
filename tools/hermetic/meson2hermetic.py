#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 The Meson Authors

'''
Converts meson build files to a hermetic build system (Soong, Bazel)
Emits the hermetic build files directly in directory.

Intended Usage:
    Within the target directory run command:
    ```
    python ~/<path>/<to>/tools/hermetic/meson2hermetic.py --config=/path/to/aosp.toml
    ```

This scripts requires python 3.11 to run.

Dependencies Used:
 - jinja2
'''

import argparse
import typing as T
import os
from pathlib import Path
import tempfile

from mesonbuild import build, environment, coredata, mlog
from mesonbuild.options import OptionKey
from mesonbuild.utils.universal import set_meson_command, File
from mesonbuild.mesonlib import MachineChoice

from tools.hermetic.hermetic_types import (
    ToolchainFactory,
    HermeticInterpreter,
    HermeticConfig,
    HermeticState,
    HermeticStaticLibrary,
    HermeticSharedLibrary,
    HermeticCustomTarget,
    HermeticIncludeDirectory,
    HermeticFlag,
)
from tools.hermetic.soong_generator import SoongGenerator
from tools.hermetic.bazel_generator import BazelGenerator

if T.TYPE_CHECKING:
    from tools.hermetic.hermetic_types import CMDOptions

def create_hermetic_state(b: build.Build, intr: HermeticInterpreter) -> HermeticState:
    hermetic_state = HermeticState()
    hermetic_state.c_std = intr.environment.coredata.optstore.get_value_for(OptionKey('c_std'))
    hermetic_state.cpp_std = intr.environment.coredata.optstore.get_value_for(OptionKey('cpp_std'))
    hermetic_state.root_subdir = ''

    hermetic_state.include_directories.extend(intr.hermetic_includes)
    
    static_libs = []
    shared_libs = []
    targets = b.get_build_targets()
    for target in targets:
        library = targets[target]
        if isinstance(library, build.StaticLibrary):
            static_libs.append(library)
        elif isinstance(library, build.SharedLibrary):
            shared_libs.append(library)

    for lib in static_libs:
        hermetic_sl = HermeticStaticLibrary()
        c_flag, cpp_flag = hermetic_sl.convert_from_meson(lib, intr.assignment_tracker, b.projects)
        hermetic_state.static_libraries.append(hermetic_sl)
        if c_flag and c_flag.name not in hermetic_state.flags:
            hermetic_state.flags[c_flag.name] = c_flag
        if cpp_flag and cpp_flag.name not in hermetic_state.flags:
            hermetic_state.flags[cpp_flag.name] = cpp_flag

    for subproject, args in intr.hermetic_project_args.items():
        project_name = intr.build.projects[subproject]
        c_args = args.get('c', [])
        if c_args:
            flag = HermeticFlag(f'{project_name}_c_project_args', '')
            flag.cflags = c_args
            hermetic_state.flags[flag.name] = flag
        cpp_args = args.get('cpp', [])
        if cpp_args:
            flag = HermeticFlag(f'{project_name}_cpp_project_args', '')
            flag.cppflags = cpp_args
            hermetic_state.flags[flag.name] = flag

    for lib in shared_libs:
        hermetic_sl = HermeticSharedLibrary()
        c_flag, cpp_flag = hermetic_sl.convert_from_meson(lib, intr.assignment_tracker, b.projects)
        hermetic_state.shared_libraries.append(hermetic_sl)
        if c_flag and c_flag.name not in hermetic_state.flags:
            hermetic_state.flags[c_flag.name] = c_flag
        if cpp_flag and cpp_flag.name not in hermetic_state.flags:
            hermetic_state.flags[cpp_flag.name] = cpp_flag

    targets = b.get_custom_targets()
    host_tools_config = intr.config.host_tools
    processed_python_targets = set()
    for target in targets:
        custom_target = targets[target]
        hermetic_ct = HermeticCustomTarget()
        hermetic_ct.convert_from_meson(custom_target)

        new_srcs = []
        for i, src in enumerate(custom_target.sources):
            original_src = hermetic_ct.srcs[i] if i < len(hermetic_ct.srcs) else ''
            if isinstance(src, File):
                if src.subdir != custom_target.subdir:
                    fg_name = os.path.basename(src.fname).replace('.', '_')
                    if fg_name not in [fg['name'] for fg in hermetic_state.filegroups.get(src.subdir, [])]:
                        hermetic_state.filegroups.setdefault(src.subdir, []).append({
                            'name': fg_name,
                            'src': src.fname,
                        })
                    new_srcs.append(f':{fg_name}')
                    for j, part in enumerate(hermetic_ct.cmd_parts):
                        if part == ('location', src.fname, src.fname):
                            hermetic_ct.cmd_parts[j] = ('location', f':{fg_name}', None)
                elif original_src:
                    new_srcs.append(original_src)
        hermetic_ct.srcs = new_srcs

        hermetic_state.custom_targets.append(hermetic_ct)

        python_target = hermetic_ct.emit_python_target(host_tools_config)
        if python_target:
            if python_target.name not in processed_python_targets:
                hermetic_state.python_targets.append(python_target)
                processed_python_targets.add(python_target.name)

    return hermetic_state

def generate(config: HermeticConfig, cmd_opts: argparse.Namespace):
    env = environment.Environment(cmd_opts.sourcedir, cmd_opts.builddir, cmd_opts)

    if config.host_machine:
        env.machines.host = env.machines.host.from_literal(config.host_machine)
    if config.build_machine:
        env.machines.build = env.machines.build.from_literal(config.build_machine)
    if config.target_machine:
        env.machines.target = env.machines.target.from_literal(config.target_machine)

    factory = ToolchainFactory(config.toolchain, MachineChoice.HOST, env.machines.host)
    env.coredata.compilers[MachineChoice.HOST]['c'] = factory.create_compiler('c')
    env.coredata.compilers[MachineChoice.HOST]['cpp'] = factory.create_compiler('cpp')
    env.coredata.compilers[MachineChoice.HOST]['rust'] = factory.create_compiler('rust')
    
    b = build.Build(env)
    
    if env.is_cross_build():
        pass

    user_defined_options = T.cast('CMDOptions', argparse.Namespace(**vars(cmd_opts)))
    d = {OptionKey.from_string(k): v for k, v in config.meson_options.items()}
    d.update(user_defined_options.cmd_line_options)
    user_defined_options.cmd_line_options = d

    intr = HermeticInterpreter(b, config, user_defined_options=user_defined_options)

    try:
        print(f'Interpreting {cmd_opts.sourcedir}/meson.build ...')
        intr.run()
    except Exception as e:
        raise e

    hermetic_state = create_hermetic_state(b, intr)
    
    build_system = config.build
    if not build_system:
        exit('Build system not specified in config file.')

    output_dir = cmd_opts.output_dir if cmd_opts.output_dir else cmd_opts.project_dir
    
    generator = None
    if build_system == 'soong':
        generator = SoongGenerator(output_dir, hermetic_state, config)
    elif build_system == 'bazel':
        generator = BazelGenerator(output_dir, hermetic_state, config)
    else:
        exit(f'Build system {build_system} not supported.')
        
    generator.generate()

def create_default_options(args: argparse.Namespace) -> argparse.Namespace:
    options = T.cast('CMDOptions', args)
    options.sourcedir = args.project_dir
    options.builddir = os.path.join(args.project_dir, 'hermetic-build')
    options.cross_file = []
    options.backend = 'hermetic'
    options.projectoptions = []
    options.native_file = []
    options.cmd_line_options = {}
    return options

def main():
    parser = argparse.ArgumentParser(description='Generates hermetic build files from meson')
    parser.add_argument('--config', required=True, help='The path to a valid config file (toml).')
    parser.add_argument('--toolchain', required=True, help='The path to a valid toolchain config file (toml).')
    parser.add_argument('--project-dir', default=os.getcwd(), help='The path to the project directory.')
    parser.add_argument('--output-dir', help='The path to the output directory for generated files. Defaults to the project directory.')

    args = parser.parse_args()
    config = HermeticConfig(Path(args.config), Path(args.toolchain))

    mlog.set_quiet()
    set_meson_command('NULL')

    options = create_default_options(args)
    options.projectoptions = config.project_options()
    coredata.parse_cmd_line_options(options)
    with tempfile.TemporaryDirectory() as temp_build_dir:
        options.builddir = temp_build_dir
        generate(config, options)

if __name__ == '__main__':
    main()
