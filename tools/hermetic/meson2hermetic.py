#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 The Meson Development Team

'''
Converts meson build files to a single_pass build system (Soong, Bazel)
Emits the single_pass build files directly in directory.

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
import tomllib
import typing as T
import os
from pathlib import Path
import tempfile
import sys

from mesonbuild import build, environment, coredata, mlog
from mesonbuild.options import OptionKey
from mesonbuild.utils.universal import set_meson_command, File

from tools.hermetic.hermetic_state import HermeticState
from mesonbuild.mesonlib import MachineChoice

from tools.hermetic.hermetic_state import HermeticState
from tools.hermetic.virtual.virtual_toolchain import VirtualToolchain
from tools.hermetic.virtual.virtual_project_info import VirtualProjectInfo
from tools.hermetic.virtual.virtual_interpreter import VirtualInterpreter
from tools.hermetic.single_pass.single_pass_types import (
    SinglePassState,
    SinglePassStaticLibrary,
    SinglePassSharedLibrary,
    SinglePassCustomTarget,
    SinglePassFlag,
    SinglePassFileGroup,
)
from tools.hermetic.generators.soong_hermetic_generator import SoongHermeticGenerator
from tools.hermetic.generators.bazel_hermetic_generator import BazelHermeticGenerator

if T.TYPE_CHECKING:
    from tools.hermetic.single_pass.single_pass_types import CMDOptions


def create_single_pass_state(b: build.Build, intr: VirtualInterpreter) -> SinglePassState:
    project_info = intr.project_info
    single_pass_state = SinglePassState()
    single_pass_state.c_std = intr.environment.coredata.optstore.get_value_for(OptionKey('c_std'))
    single_pass_state.cpp_std = intr.environment.coredata.optstore.get_value_for(OptionKey('cpp_std'))
    single_pass_state.root_subdir = ''

    processed_python_targets = set()
    processed_filegroups = set()
    processed_include_dirs = set()

    for subproject, args in intr.single_pass_project_args.items():
        project_name = project_info.project_name
        c_args = args.get('c', [])
        if c_args:
            flag = SinglePassFlag(f'{project_name}_c_project_args', '', project_info.toolchain_info, c_flags=c_args)
            single_pass_state.flags[flag.name] = flag
        cpp_args = args.get('cpp', [])
        if cpp_args:
            flag = SinglePassFlag(f'{project_name}_cpp_project_args', '', project_info.toolchain_info, cpp_flags=cpp_args)
            single_pass_state.flags[flag.name] = flag

    targets = b.get_custom_targets()
    for target in targets:
        custom_target = targets[target]
        single_pass_ct = SinglePassCustomTarget(custom_target, project_info)
        
        if single_pass_ct.skip_custom_target:
            continue

        single_pass_state.custom_targets.append(single_pass_ct)

        python_target = single_pass_ct.get_python_target()
        if python_target:
            if python_target.name not in processed_python_targets:
                single_pass_state.python_targets.append(python_target)
                processed_python_targets.add(python_target.name)

        filegroups = single_pass_ct.get_generated_filegroups()
        for filegroup in filegroups:
            if filegroup.name not in processed_filegroups:
                single_pass_state.filegroups.append(filegroup)
                processed_filegroups.add(filegroup.name)

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
        single_pass_sl = SinglePassStaticLibrary()
        c_flag, cpp_flag = single_pass_sl.convert_from_meson(lib, project_info)
        single_pass_state.static_libraries.append(single_pass_sl)
        if c_flag and c_flag.name not in single_pass_state.flags:
            single_pass_state.flags[c_flag.name] = c_flag
        if cpp_flag and cpp_flag.name not in single_pass_state.flags:
            single_pass_state.flags[cpp_flag.name] = cpp_flag

        for include_dir in single_pass_sl.generated_include_dirs.values():
            if include_dir.name not in processed_include_dirs:
                processed_include_dirs.add(include_dir.name)
                single_pass_state.include_directories.append(include_dir)
        
        for filegroup in single_pass_sl.generated_filegroups.values():
            if filegroup.name not in processed_filegroups:
                single_pass_state.filegroups.append(filegroup)
                processed_filegroups.add(filegroup.name)

    for lib in shared_libs:
        single_pass_sl = SinglePassSharedLibrary()
        c_flag, cpp_flag = single_pass_sl.convert_from_meson(lib, project_info)
        single_pass_state.shared_libraries.append(single_pass_sl)
        if c_flag and c_flag.name not in single_pass_state.flags:
            single_pass_state.flags[c_flag.name] = c_flag
        if cpp_flag and cpp_flag.name not in single_pass_state.flags:
            single_pass_state.flags[cpp_flag.name] = cpp_flag

        for include_dir in single_pass_sl.generated_include_dirs.values():
            if include_dir.name not in processed_include_dirs:
                processed_include_dirs.add(include_dir.name)
                single_pass_state.include_directories.append(include_dir)

        for filegroup in single_pass_sl.generated_filegroups.values():
            if filegroup.name not in processed_filegroups:
                single_pass_state.filegroups.append(filegroup)
                processed_filegroups.add(filegroup.name)

    return single_pass_state


def generate(project_info: VirtualProjectInfo, cmd_opts: argparse.Namespace, toolchain: VirtualToolchain):
    env = environment.Environment(cmd_opts.sourcedir, cmd_opts.builddir, cmd_opts)

    if not toolchain:
        sys.exit(f'Invalid toolchain_config passed to generate.')

    env.machines.host = project_info.toolchain_info.machine_info[MachineChoice.HOST]
    env.machines.build = project_info.toolchain_info.machine_info[MachineChoice.BUILD]

    env.coredata.compilers[MachineChoice.HOST]['c'] = toolchain.create_c_compiler(MachineChoice.HOST)
    env.coredata.compilers[MachineChoice.HOST]['cpp'] = toolchain.create_cpp_compiler(MachineChoice.HOST)
    env.coredata.compilers[MachineChoice.HOST]['rust'] = toolchain.create_rust_compiler(MachineChoice.HOST)

    env.coredata.compilers[MachineChoice.BUILD]['c'] = toolchain.create_c_compiler(MachineChoice.BUILD)
    env.coredata.compilers[MachineChoice.BUILD]['cpp'] = toolchain.create_cpp_compiler(MachineChoice.BUILD)
    env.coredata.compilers[MachineChoice.BUILD]['rust'] = toolchain.create_rust_compiler(MachineChoice.BUILD)
    b = build.Build(env)

    user_defined_options = T.cast('CMDOptions', argparse.Namespace(**vars(cmd_opts)))
    d = {OptionKey.from_string(k): v for k, v in project_info.meson_options.items()}
    d.update(user_defined_options.cmd_line_options)
    user_defined_options.cmd_line_options = d

    intr = VirtualInterpreter(b, project_info, user_defined_options=user_defined_options)

    try:
        intr.run()
    except Exception as e:
        raise e

    single_pass_state = create_single_pass_state(b, intr)
    return single_pass_state


def create_default_options(args: argparse.Namespace) -> argparse.Namespace:
    options = T.cast('CMDOptions', args)
    options.sourcedir = args.project_dir
    options.builddir = os.path.join(args.project_dir, 'single_pass-build')
    options.cross_file = []
    options.backend = 'hermetic'
    options.projectoptions = []
    options.native_file = []
    options.cmd_line_options = {}
    return options


def main():
    parser = argparse.ArgumentParser(description='Generates single_pass build files from meson')
    parser.add_argument('--config', required=True, help='The path to a valid config file (toml).')
    parser.add_argument('--toolchain', required=True, help='The path to a valid toolchain config file (toml).')
    parser.add_argument('--dependencies', required=False, help='The path to a valid dependencies file (toml).')
    parser.add_argument('--project-dir', default=os.getcwd(), help='The path to the project directory.')
    parser.add_argument(
        '--output-dir', help='The path to the output directory for generated files. Defaults to the project directory.')

    args = parser.parse_args()

    # Load all toml files
    try:
        with open(Path(args.config), "rb") as f:
            config_toml = tomllib.load(f)
        with open(Path(args.toolchain), "rb") as f:
            toolchain_toml = tomllib.load(f)
        dependencies_toml = {}
        if args.dependencies:
            with open(Path(args.dependencies), "rb") as f:
                dependencies_toml = tomllib.load(f)
    except Exception as e:
        sys.exit(f'Error trying to open config file: {e}')

    # Separate project-level info from configs
    project_data = {k: v for k, v in config_toml.items() if k != 'config'}
    configs = config_toml.get('config', [])
    if not isinstance(configs, list):
        configs = [configs]

    hermetic_state = HermeticState()

    toolchain_configs = {tc.get('name'): tc for tc in toolchain_toml.get('toolchain', [])}

    for config_item in configs:
        config_name = config_item.get('config_name')
        if config_name:
            print(f"Processing config: {config_name}")
        else:
            print("Processing unnamed config")

        current_config_data = project_data.copy()
        current_config_data['config'] = config_item

        project_info = VirtualProjectInfo(current_config_data, dependencies_toml)

        mlog.set_quiet()
        set_meson_command('NULL')

        options = create_default_options(args)
        options.output_dir = args.output_dir if args.output_dir else args.project_dir

        options.projectoptions = project_info.project_options()

        if not project_info.toolchain_labels:
            print(f"Warning: No host_toolchains specified for config '{config_name}'. Skipping.")
            continue

        if not project_info.build_toolchain_labels:
            print(f"Warning: No build_toolchains specified for config '{config_name}'. Skipping.")
            continue

        for toolchain_label in project_info.toolchain_labels:
            print(f"  Processing toolchain: {toolchain_label}")

            if toolchain_label not in toolchain_configs:
                print(f"Warning: Toolchain '{toolchain_label}' not found in toolchain file. Skipping.")
                continue

            # For now, we assume the first build toolchain is the one to use.
            build_toolchain_label = project_info.build_toolchain_labels[0]
            if build_toolchain_label not in toolchain_configs:
                print(f"Warning: Toolchain '{build_toolchain_label}' not found in toolchain file. Skipping.")
                continue

            with tempfile.TemporaryDirectory() as temp_build_dir:
                options.builddir = temp_build_dir
                toolchain = VirtualToolchain(toolchain_label, build_toolchain_label, toolchain_configs)
                project_info.set_toolchain_info(toolchain.toolchain_info)
                single_pass_state = generate(project_info, options, toolchain)
                for lib in single_pass_state.static_libraries:
                    hermetic_state.add_static_library(lib)
                for lib in single_pass_state.shared_libraries:
                    hermetic_state.add_shared_library(lib)
                for target in single_pass_state.custom_targets:
                    hermetic_state.add_custom_target(target)
                for flag in single_pass_state.flags.values():
                    hermetic_state.add_flag(flag, project_info.custom_variable)
                for inc in single_pass_state.include_directories:
                    hermetic_state.add_include_directory(inc, project_info.custom_variable)
                for grp in single_pass_state.filegroups:
                    hermetic_state.add_file_group(grp, project_info.custom_variable)
                for target in single_pass_state.python_targets:
                    hermetic_state.add_python_target(target)

    hermetic_generator = None
    build_system = project_info.build_system
    if build_system == 'soong':
        hermetic_generator = SoongHermeticGenerator(options.output_dir, hermetic_state, project_info)
    elif build_system == 'bazel':
        hermetic_generator = BazelHermeticGenerator(options.output_dir, hermetic_state, project_info)
    else:
        sys.exit(f'Build system {build_system} not supported.')
    hermetic_generator.generate()
    #print(hermetic_state)


if __name__ == '__main__':
    main()
