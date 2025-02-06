#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 The Meson Development Team

from __future__ import annotations
import typing as T
from pathlib import Path
import os
import sys
import enum
import hashlib

from jinja2 import Environment, FileSystemLoader

from mesonbuild import build, interpreter, programs, mparser
from mesonbuild.interpreter.interpreterobjects import RunProcess
from mesonbuild.dependencies import base as dependency_base, InternalDependency
from mesonbuild.interpreterbase import InterpreterObject, ObjectHolder, noArgsFlattening
from mesonbuild.interpreterbase.decorators import noKwargs
from enum import Enum

from tools.hermetic.single_pass.single_pass_custom_target import SinglePassCustomTarget, SinglePassPythonTarget
from tools.hermetic.single_pass.single_pass_utils import SinglePassFlag, SinglePassIncludeDirectory, SinglePassFileGroup
from tools.hermetic.virtual.virtual_interpreter import NameAssignment, get_stable_id

if T.TYPE_CHECKING:
    from mesonbuild.coredata import SharedCMDOptions
    from tools.hermetic.virtual.virtual_project_info import VirtualProjectInfo


class SinglePassState:

    def __init__(self):
        self.shared_libraries: T.List[SinglePassSharedLibrary] = []
        self.static_libraries: T.List[SinglePassStaticLibrary] = []
        self.rust_libraries: T.List[SinglePassRustLibrary] = []
        self.custom_targets: T.List[SinglePassCustomTarget] = []
        self.python_targets: T.List[SinglePassPythonTarget] = []
        self.filegroups: T.List[SinglePassFileGroup] = []
        self.include_directories: T.List[SinglePassIncludeDirectory] = []
        self.flags: T.Dict[str, SinglePassFlag] = {}

        self.c_std: str = ''
        self.cpp_std: str = ''
        self.root_subdir: str = ''

    def copts(self):
        pass

    def __str__(self):
        return f'SinglePassState:\n\tshared_libraries len: {len(self.shared_libraries)}' \
            f'\n\tstatic_libraries len: {len(self.static_libraries)}' \
            f'\n\tcustom_targets len: {len(self.custom_targets)}' \
            f'\n\tpython_targets len: {len(self.python_targets)}'


class RustABI(Enum):
    RUST = 'rust'
    C = 'c'
    NONE = None


class SinglePassStaticLibrary:

    def __init__(self):
        self.name: str = ''
        self.toolchain: str = ''
        self.custom_variable: T.Optional[str] = None
        self.subdir: str = ''  # Location of this StaticLibrary's definition
        self.host_supported: str = 'false'
        self.dirs: T.List[str] = []
        self.visibility: T.List[str] = []
        self.srcs: T.List[str] = []
        self.rust_abi: RustABI = RustABI.NONE
        self.crate_root: str = ''
        self.src_subdirs: T.Set[str] = set()
        self.c_args: T.List[str] = []
        self.cpp_args: T.List[str] = []

        self.generated_headers: T.List[str] = []
        self.generated_sources: T.List[str] = []

        self.host_supported = False;
        self.deps: T.List[str] = []
        self.target_compatible_with: T.List[str] = []

        self.generated_filegroups: T.Dict[str, SinglePassFileGroup] = {}
        self.generated_include_dirs: T.Dict[str, SinglePassIncludeDirectory] = {}
        self.static_libs: T.List[str] = []
        self.whole_static_libs: T.List[str] = []
        self.shared_libs: T.List[str] = []
        self.flags: T.List[str] = []
        self.header_libs: T.List[str] = []

    def convert_from_meson(self, meson_sl: build.StaticLibrary, project_info: VirtualProjectInfo) -> T.Tuple[T.Optional[SinglePassFlag], T.Optional[SinglePassFlag]]:
        self.name = meson_sl.get_basename()
        self.toolchain_info = project_info.toolchain_info
        self.custom_variable = project_info.custom_variable
        self.subdir = meson_sl.subdir

        for file in meson_sl.sources:
            stable_id = get_stable_id(file)
            if stable_id in project_info.assignment_tracker and file.subdir != self.subdir:
                fg_name = project_info.assignment_tracker[stable_id].name
                if file.fname.endswith('.h'):
                    fg_name = fg_name + "_headers"
                    if fg_name in self.generated_include_dirs:
                        self.generated_include_dirs[fg_name].add_header_file(file, project_info)
                    else:
                        directory = SinglePassIncludeDirectory(project_info.toolchain_info, name = fg_name)
                        directory.add_header_file(file, project_info)
                        self.generated_include_dirs[directory.name] = directory
                        self.header_libs.append(directory.name)
                else:
                    fg_name = fg_name + "_impl"
                    if fg_name in self.generated_filegroups:
                        self.generated_filegroups[fg_name].add_source_file(file, project_info)
                    else:
                        filegroup = SinglePassFileGroup(project_info.toolchain_info, name = fg_name)
                        filegroup.add_source_file(file, project_info)
                        self.generated_filegroups[fg_name] = filegroup
                        self.srcs.append(":" + filegroup.name)
            else:
                if not file.fname.endswith('.h'):
                    self.srcs.append(file.fname)

        project_name = project_info.project_name
        self.flags.append(f'{project_name}_c_project_args')
        self.flags.append(f'{project_name}_cpp_project_args')
        rust_abi = meson_sl.original_kwargs.get('rust_abi')
        if rust_abi:
            self.rust_abi = RustABI(rust_abi)
            for s in self.srcs:
                if os.path.basename(s) == 'lib.rs':
                    self.crate_root = s
                    break
            if not self.crate_root and self.srcs:
                self.crate_root = self.srcs[0]

            for s in self.srcs:
                self.src_subdirs.add(os.path.dirname(s))

        for d in meson_sl.get_dependencies():
            if isinstance(d, InternalDependency):
                for lib in d.libraries:
                    if isinstance(lib, build.StaticLibrary):
                        self.static_libs.append(lib.name)
                    elif isinstance(lib, build.SharedLibrary):
                        self.shared_libs.append(lib.name)
            else:
                self.deps.append(d.name)

        for d in meson_sl.external_deps:
            if d.found() and isinstance(d, dependency_base.ExternalDependency):
                if d.static:
                    self.static_libs.append(d.name)
                else:
                    if project_info.is_dependency_necessary(d.name):
                        self.shared_libs.append(d.name)

        c_args = meson_sl.get_extra_args('c')
        cpp_args = meson_sl.get_extra_args('cpp')

        c_flag_ret = None
        cpp_flag_ret = None

        if c_args:
            stable_id = get_stable_id(c_args)
            name = ''
            subdir = ''
            if stable_id in project_info.assignment_tracker:
                name = project_info.assignment_tracker[stable_id].name
                subdir = project_info.assignment_tracker[stable_id].subdir
            else:
                name = f'{self.name}_c_flags'
                subdir = self.subdir
            c_flag_ret = SinglePassFlag(name, subdir, project_info.toolchain_info, c_flags=c_args)
            self.flags.append(name)

        if cpp_args:
            stable_id = get_stable_id(cpp_args)
            name = ''
            subdir = ''
            if stable_id in project_info.assignment_tracker:
                name = project_info.assignment_tracker[stable_id].name
                subdir = project_info.assignment_tracker[stable_id].subdir
            else:
                name = f'{self.name}_cpp_flags'
                subdir = self.subdir
            cpp_flag_ret = SinglePassFlag(name, subdir, project_info.toolchain_info, cpp_flags=cpp_args)
            self.flags.append(name)

        for include_dir in meson_sl.include_dirs:
            stable_id = get_stable_id(include_dir)
            if stable_id in project_info.assignment_tracker:
                directory = SinglePassIncludeDirectory(project_info.toolchain_info)
                directory.add_include_dir(include_dir, project_info)
                if directory.name not in self.generated_include_dirs:
                    self.generated_include_dirs[directory.name] = directory
                    self.header_libs.append(directory.name)

        processed_targets = set()
        for target in meson_sl.get_generated_sources():
            if not isinstance(target, build.CustomTarget):
                continue
            if target.name in processed_targets:
                continue

            processed_targets.add(target.name)

            has_headers = any(o.endswith('.h') for o in target.outputs)
            has_sources = any(not o.endswith('.h') for o in target.outputs)

            sanitized_name = project_info.sanitize_target_name(target.name)

            if has_headers and has_sources:
                if f'{sanitized_name}_header' not in self.generated_headers:
                    self.generated_headers.append(f'{sanitized_name}_header')
                if f'{sanitized_name}_impl' not in self.generated_sources:
                    self.generated_sources.append(f'{sanitized_name}_impl')
            elif has_headers:
                if target.name not in self.generated_headers:
                    self.generated_headers.append(sanitized_name)
            else:
                if target.name not in self.generated_sources:
                    self.generated_sources.append(sanitized_name)

        for target in meson_sl.link_targets:
            if isinstance(target, build.StaticLibrary):
                self.static_libs.append(target.name)
            elif isinstance(target, build.SharedLibrary):
                self.shared_libs.append(target.name)

        for target in meson_sl.link_whole_targets:
            if isinstance(target, build.StaticLibrary):
                self.whole_static_libs.append(target.name)

        return c_flag_ret, cpp_flag_ret

    def __str__(self):
        return f'@StaticLibrary({self.name})'


class SinglePassSharedLibrary(SinglePassStaticLibrary):
    '''
    Exactly same metadata as StaticLibrary besides how it's generated in Soong and Bazel files
    '''

    def convert_from_meson(self, meson_sl: build.SharedLibrary, project_info: VirtualProjectInfo) -> T.Tuple[T.Optional[SinglePassFlag], T.Optional[SinglePassFlag]]:
        return super().convert_from_meson(meson_sl, project_info)

    def __str__(self):
        return f'@SharedLibrary({self.name})'



