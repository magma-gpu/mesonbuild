#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 The Meson Development Team

from __future__ import annotations
import typing as T
from collections import defaultdict

if T.TYPE_CHECKING:
    from tools.hermetic.single_pass.single_pass_types import SinglePassStaticLibrary, SinglePassSharedLibrary
    from tools.hermetic.single_pass.single_pass_custom_target import SinglePassCustomTarget, SinglePassCmdPart, SinglePassPythonTarget
    from tools.hermetic.single_pass.single_pass_utils import SinglePassFlag, SinglePassIncludeDirectory, SinglePassFileGroup
    from tools.hermetic.virtual.virtual_project_info import VirtualProjectInfo
class HermeticBuckets:
    def __init__(self):
        self.values_by_label: T.Dict[T.Tuple[str, str], T.Set[str]] = defaultdict(set)
        self.valid_toolchains: T.Set[str] = set()
        self.valid_custom_variables: T.Set[str] = set()
        self.buckets: T.Dict[str, T.Dict[str, T.List[str]]] = defaultdict(lambda: defaultdict(list))

    def add(self, toolchain: str, custom_variable: T.Optional[str], values: T.Iterable[str]):
        custom_variable_label = custom_variable or ''
        self.valid_toolchains.add(toolchain)
        self.valid_custom_variables.add(custom_variable_label)
        self.values_by_label[(toolchain, custom_variable_label)].update(values)
        self._rebuild_buckets()

    def _rebuild_buckets(self):
        self.buckets.clear()
        if not self.values_by_label:
            return
        all_values = set.union(*self.values_by_label.values())
        for value in all_values:
            labels_for_value = {
                label for label, value_set in self.values_by_label.items() if value in value_set
            }
            if self._is_common_to_all(labels_for_value):
                self.buckets['common'][''].append(value)
                continue

            # Find all toolchains where this value is common
            common_tcs = []
            for tc in self.valid_toolchains:
                custom_vars_for_tc = {cv for t, cv in self.values_by_label.keys() if t == tc}
                if not custom_vars_for_tc:
                    continue
                if all((tc, cv) in labels_for_value for cv in custom_vars_for_tc):
                    common_tcs.append(tc)

            # Bucket under common toolchains
            for tc in common_tcs:
                self.buckets[tc][''].append(value)

            # Bucket remaining specific labels
            remaining_labels = labels_for_value.copy()
            for tc in common_tcs:
                custom_vars_for_tc = {cv for t, cv in self.values_by_label.keys() if t == tc}
                for cv in custom_vars_for_tc:
                    remaining_labels.discard((tc, cv))

            for toolchain, custom_var in remaining_labels:
                self.buckets[toolchain][custom_var].append(value)

    def _is_common_to_all(self, labels_for_value: T.Set[T.Tuple[str, str]]) -> bool:
        if not self.values_by_label:
            return False
        return set(self.values_by_label.keys()) == labels_for_value

    def __repr__(self) -> str:
        return f"HermeticBuckets({{\n{  '\n'.join([f'        {k}: {dict(v)}' for k, v in self.buckets.items()])}\n    }})"

class HermeticTarget:
    def __init__(self, name: str, subdir: str):
        self.name = name
        self.subdir = subdir
        self.host_supported = False

    def __repr__(self) -> str:
        return f"{{type(self).__name__}}(name='{self.name}', subdir='{self.subdir}')"


class HermeticFlag(HermeticTarget):
    def __init__(self, name: str, subdir: str):
        super().__init__(name, subdir)
        self.cflags = HermeticBuckets()
        self.cppflags = HermeticBuckets()
        self.all_flags = HermeticBuckets()

    def add_config(self, flag: SinglePassFlag, custom_variable: T.Optional[str]):
        toolchain = flag.toolchain_info.host_machine
        self.host_supported = flag.toolchain_info.host_supported()
        self.cflags.add(toolchain, custom_variable, flag.cflags)
        self.cppflags.add(toolchain, custom_variable, flag.cppflags)
        self.all_flags.add(toolchain, custom_variable, flag.cflags)
        self.all_flags.add(toolchain, custom_variable, flag.cppflags)

    def __repr__(self) -> str:
        return (f"{super().__repr__()}\n"
                f"    cflags: {self.cflags}\n"
                f"    cflags: {self.cflags}\n"
                f"    cppflags: {self.cppflags}\n")


class HermeticIncludeDirectory(HermeticTarget):
    def __init__(self, name: str, subdir: str):
        super().__init__(name, subdir)
        self.paths = HermeticBuckets()

    def add_config(self, inc: SinglePassIncludeDirectory, custom_variable: T.Optional[str]):
        self.host_supported = inc.toolchain_info.host_supported()
        toolchain = inc.toolchain_info.host_machine
        self.paths.add(toolchain, custom_variable, inc.paths)

    def __repr__(self) -> str:
        return (f"{super().__repr__()}\n"
                f"    paths: {self.paths}\n")


class HermeticStaticLibrary(HermeticTarget):
    def __init__(self, name: str, subdir: str):
        super().__init__(name, subdir)
        self.srcs = HermeticBuckets()
        self.flags = HermeticBuckets()
        self.include_dirs = HermeticBuckets()
        self.static_libs = HermeticBuckets()
        self.shared_libs = HermeticBuckets()
        self.whole_static_libs = HermeticBuckets()
        self.header_libs = HermeticBuckets()
        self.deps = HermeticBuckets()
        self.generated_headers = HermeticBuckets()
        self.generated_sources = HermeticBuckets()
        self.rust_abi = None
        self.crate_root: str = ''
        self.src_subdirs: T.Set[str] = set()

    def add_config(self, lib: SinglePassStaticLibrary):
        self.host_supported = lib.toolchain_info.host_supported()

        toolchain = lib.toolchain_info.host_machine
        self.srcs.add(toolchain, lib.custom_variable, lib.srcs)
        self.flags.add(toolchain, lib.custom_variable, lib.flags)
        self.include_dirs.add(toolchain, lib.custom_variable, lib.header_libs)
        self.static_libs.add(toolchain, lib.custom_variable, lib.static_libs)
        self.shared_libs.add(toolchain, lib.custom_variable, lib.shared_libs)
        self.whole_static_libs.add(toolchain, lib.custom_variable, lib.whole_static_libs)
        self.deps.add(toolchain, lib.custom_variable, lib.deps)
        self.generated_headers.add(toolchain, lib.custom_variable, lib.generated_headers)
        self.generated_sources.add(toolchain, lib.custom_variable, lib.generated_sources)
        if lib.rust_abi.value:
            self.rust_abi = lib.rust_abi
            self.crate_root = lib.crate_root
            self.src_subdirs.update(lib.src_subdirs)

    def __repr__(self) -> str:
        return (f"{super().__repr__()}\n"
                f"    srcs: {self.srcs}\n"
                f"    flags: {self.flags}\n"
                f"    include_dirs: {self.include_dirs}\n"
                f"    static_libs: {self.static_libs}\n"
                f"    shared_libs: {self.shared_libs}\n"
                f"    whole_static_libs: {self.whole_static_libs}\n"
                f"    header_libs: {self.header_libs}\n"
                f"    deps: {self.deps}\n"
                f"    generated_headers: {self.generated_headers}\n"
                f"    generated_sources: {self.generated_sources}")

class HermeticSharedLibrary(HermeticStaticLibrary):
    def __repr__(self) -> str:
        return f"{{super().__repr__()}}"


class HermeticFileGroup(HermeticTarget):
    def __init__(self, name: str, subdir: str):
        super().__init__(name, subdir)
        self.srcs = HermeticBuckets()

    def add_config(self, grp: SinglePassFileGroup, custom_variable: T.Optional[str]):
        toolchain = grp.toolchain_info.host_machine
        self.srcs.add(toolchain, custom_variable, grp.srcs)

    def __repr__(self) -> str:
        return (f"{super().__repr__()}\n"
                f"    srcs: {self.srcs}\n")


class HermeticPythonTarget(HermeticTarget):
    def __init__(self, name: str, subdir: str):
        super().__init__(name, subdir)
        self.main: str = ''
        self.srcs: T.List[str] = []
        self.libs: T.List[str] = []

    def add_config(self, target: SinglePassPythonTarget):
        self.main = target.main
        self.srcs = target.srcs
        self.libs = target.libs

    def __repr__(self) -> str:
        return (f"{super().__repr__()}\n"
                f"    main: {self.main}\n"
                f"    srcs: {self.srcs}\n"
                f"    libs: {self.libs}\n")


class HermeticState:
    def __init__(self):
        self.static_libraries: T.Dict[str, HermeticStaticLibrary] = {}
        self.shared_libraries: T.Dict[str, HermeticSharedLibrary] = {}
        self.custom_targets: T.Dict[str, T.List[SinglePassCustomTarget]] = {}
        self.flags: T.Dict[str, HermeticFlag] = {}
        self.include_directories: T.Dict[str, HermeticIncludeDirectory] = {}
        self.file_groups: T.Dict[str, HermeticFileGroup] = {}
        self.python_targets: T.Dict[str, HermeticPythonTarget] = {}

    def add_static_library(self, lib: SinglePassStaticLibrary):
        if lib.name not in self.static_libraries:
            self.static_libraries[lib.name] = HermeticStaticLibrary(lib.name, lib.subdir)
        self.static_libraries[lib.name].add_config(lib)

    def add_shared_library(self, lib: SinglePassSharedLibrary):
        if lib.name not in self.shared_libraries:
            self.shared_libraries[lib.name] = HermeticSharedLibrary(lib.name, lib.subdir)
        self.shared_libraries[lib.name].add_config(lib)

    def add_custom_target(self, target: SinglePassCustomTarget):
        if target.name not in self.custom_targets:
            self.custom_targets[target.name] = [target]
            return

        existing_targets = self.custom_targets[target.name]
        if not existing_targets:
            return # Target was invalidated

        if target != existing_targets[0]:
            print(f'WARNING: Custom target {target.name} has conflicting definitions across toolchains, ignoring.')
            self.custom_targets[target.name] = []
        else:
            existing_targets.append(target)

    def add_flag(self, flag: SinglePassFlag, custom_variable: T.Optional[str]):
        if flag.name not in self.flags:
            self.flags[flag.name] = HermeticFlag(flag.name, flag.subdir)
        self.flags[flag.name].add_config(flag, custom_variable)

    def add_include_directory(self, inc: SinglePassIncludeDirectory, custom_variable: T.Optional[str]):
        if inc.name not in self.include_directories:
            self.include_directories[inc.name] = HermeticIncludeDirectory(inc.name, inc.subdir)
        self.include_directories[inc.name].add_config(inc, custom_variable)

    def add_file_group(self, grp: SinglePassFileGroup, custom_variable: T.Optional[str]):
        if grp.name not in self.file_groups:
            self.file_groups[grp.name] = HermeticFileGroup(grp.name, grp.subdir)
        self.file_groups[grp.name].add_config(grp, custom_variable)

    def add_python_target(self, target: SinglePassPythonTarget):
        if target.name not in self.python_targets:
            self.python_targets[target.name] = HermeticPythonTarget(target.name, target.subdir)
        self.python_targets[target.name].add_config(target)

    def __repr__(self) -> str:
        static_libs_str = '\n'.join([repr(lib) for lib in self.static_libraries.values()])
        shared_libs_str = '\n'.join([repr(lib) for lib in self.shared_libraries.values()])
        custom_targets_str = '\n'.join([', '.join(repr(t) for t in targets) for targets in self.custom_targets.values()])
        flags_str = '\n'.join([repr(flag) for flag in self.flags.values()])
        include_directories_str = '\n'.join([repr(inc) for inc in self.include_directories.values()])
        file_groups_str = '\n'.join([repr(grp) for grp in self.file_groups.values()])
        python_targets_str = '\n'.join([repr(target) for target in self.python_targets.values()])
        return (f"HermeticState:\n"
                f"  Static Libraries:\n{static_libs_str}\n"
                f"  Shared Libraries:\n{shared_libs_str}\n"
                f"  Custom Targets:\n{custom_targets_str}\n"
                f"  Flags:\n{flags_str}\n"
                f"  Include Directories:\n{include_directories_str}\n"
                f"  File Groups:\n{file_groups_str}\n"
                f"  Python Targets:\n{python_targets_str}\n")
