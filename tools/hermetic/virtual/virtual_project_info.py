#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 The Meson Development Team

from __future__ import annotations
import tomllib
import typing as T
from pathlib import Path
from dataclasses import dataclass
import glob
import os
import sys
from enum import Enum

from mesonbuild.envconfig import MachineInfo
from mesonbuild.mesonlib import MachineChoice

if T.TYPE_CHECKING:
    from .virtual_interpreter import NameAssignment

# meson adds these automatically, not necessary for soong
UNNECESSARY_SOONG_DEPENDENCIES = ['m', 'dl', 'c', 'rt']

# Soong is opinionated on naming: 'my_custom.[ch]'] is not a valid name
TRANSLATION_TABLE = str.maketrans('', '', '[]')

class VirtualToolchainInfo:
    def __init__(self, build_machine: str, host_machine: str, toolchain_config: T.Dict[str, T.Any]):
        self.machine_info = {}
        self.build_machine = build_machine
        self.host_machine = host_machine
        self.toolchain_config = toolchain_config
        self.machine_info[MachineChoice.HOST] = MachineInfo.from_literal(self.toolchain_config.get(host_machine, {}).get('host_machine', {}))
        self.machine_info[MachineChoice.BUILD] = MachineInfo.from_literal(self.toolchain_config.get(build_machine, {}).get('host_machine', {}))

    def host_supported(self) -> bool:
        return (self.build_machine == self.host_machine)

class VirtualProjectInfo:
    def __init__(self, config_data: T.Dict[str, T.Any], dependencies_data: T.Dict[str, T.Any]):
        self._toml_data: dict[str, T.Any]
        try:
            # Start with config_data and merge others into it.
            self._toml_data = config_data.copy()
            if dependencies_data:
                self._toml_data = self._merge_dicts(self._toml_data, dependencies_data)
        except Exception as e:
            sys.exit(f'Error trying to merge config data: {e}')

        self.project_dir: str = ''
        self.build_dir: str = ''
        self.assignment_tracker: T.Dict[str, NameAssignment] = {}
        self.install_dir: str = ''
        self.toolchain_info: T.Optional[VirtualToolchainInfo] = None

    def set_toolchain_info(self, toolchain_info: VirtualToolchainInfo):
        self.toolchain_info = toolchain_info

    def set_directories(self, project_dir: str, build_dir: str, install_dir: str):
        self.project_dir = project_dir
        self.build_dir = build_dir
        self.install_dir = install_dir

    def is_dependency_necessary(self, dep_name: str) -> bool:
        if self.build_system == 'soong':
            return not dep_name in UNNECESSARY_SOONG_DEPENDENCIES
        return False

    def _merge_dicts(self, a: T.Dict, b: T.Dict) -> T.Dict:
        for key, value in b.items():
            if key in a and isinstance(a[key], dict) and isinstance(value, dict):
                a[key] = self._merge_dicts(a[key], value)
            else:
                a[key] = value
        return a

    def normalize_path(self, path: str, current_subdir: str) -> str:
        prospective_path = Path(self.project_dir) / current_subdir / path
        abs_path = os.path.normpath(str(prospective_path))

        if os.path.exists(abs_path):
            relative_path = os.path.relpath(abs_path, self.project_dir)
            return relative_path
        else:
            sys.exit(f'Unknown path: {abs_path}')
            return ''

    def normalize_file_path(self, file_path: str, current_subdir: str) -> str:
        path = self.normalize_path(file_path, current_subdir)
        return os.path.dirname(path)

    def normalize_string(self, input_string: str, current_subdir: str) -> T.Optional[str]:
        gen_dir = self.build_dir + '/' + current_subdir
        if gen_dir in input_string:
            sanitized = input_string.replace(gen_dir, "@@GEN_DIR@@")
            return sanitized

        if self.install_dir in input_string:
            sanitized = input_string.replace(self.install_dir, "@@INSTALL_DIR@@")
            return sanitized

        if self.build_dir in input_string:
            sanitized = input_string.replace(self.build_dir, "@@BUILD_DIR@@")
            return sanitized

        if self.project_dir in input_string:
            sanitized = input_string.replace(self.project_dir, "@@PROJECT_DIR@@")
            return sanitized

        return input_string

    def glob_headers(self, subdir: str) -> T.List[str]:
        target_dir = self.project_dir + subdir
        pattern_h = os.path.join(target_dir, '**', '*.h')
        pattern_hpp = os.path.join(target_dir, '**', '*.hpp')

        headers_h = glob.glob(pattern_h, recursive=True)
        headers_hpp = glob.glob(pattern_hpp, recursive=True)

        return headers_h + headers_hpp

    def sanitize_target_name(self, target_name: str) -> str:
        return target_name.translate(TRANSLATION_TABLE)

    @property
    def build_system(self):
        return self._toml_data.get('project', {}).get('build_system')

    @property
    def project_name(self):
        return self._toml_data.get('project', {}).get('project_name')

    @property
    def config(self):
        return self._toml_data.get('config', {})

    @property
    def toolchain_labels(self) -> T.List[str]:
        return self.config.get('properties', {}).get('host_toolchains', [])

    @property
    def build_toolchain_labels(self) -> T.List[str]:
        return self.config.get('properties', {}).get('build_toolchains', [])

    @property
    def custom_variable(self) -> T.Optional[str]:
        custom_vars = self.config.get('properties', {}).get('custom_variable', [])
        if custom_vars:
            first_var = custom_vars[0]
            local = first_var.get('local')
            glob = first_var.get('global')
            if local is not None and glob is not None:
                return f'{local} = {glob}'
        return None

    @property
    def meson_options(self):
        return self._toml_data.get('config', {}).get('meson_options', {})

    @property
    def shared_libraries(self):
        return self._toml_data.get('shared_libraries', {})

    @property
    def static_libraries(self):
        return self._toml_data.get('static_libraries', {})

    @property
    def header_libraries(self):
        return self._toml_data.get('header_libraries', {})

    @property
    def programs(self):
        return self._toml_data.get('programs', {})


    @property
    def copyright(self):
        return self._toml_data.get('copyright', {})

    def project_options(self) -> list[str]:
        return [f'{k}={str(v).lower()}' for k, v in self.meson_options.items()]
