#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 The Meson Development Team

from __future__ import annotations
import typing as T
from mesonbuild.mesonlib import File


import os

from tools.hermetic.virtual.virtual_project_info import VirtualProjectInfo, VirtualProjectInfo
from tools.hermetic.virtual.virtual_interpreter import NameAssignment, get_stable_id

def _add_file_to_group(
    new_file: File,
    project_info: VirtualProjectInfo,
    current_subdir: T.Optional[str],
    current_paths: T.List[str]
) -> T.Tuple[str, T.List[str]]:
    normalized_path = project_info.normalize_file_path(new_file.fname, new_file.subdir)
    old_subdir = current_subdir
    if current_subdir is not None:
        new_subdir = os.path.commonpath([current_subdir, normalized_path])
    else:
        new_subdir = normalized_path

    new_paths = list(current_paths)
    if old_subdir != new_subdir:
        rebased_paths = []
        if old_subdir is not None:
            for path in new_paths:
                abs_path = os.path.join(old_subdir, path)
                rebased_paths.append(os.path.relpath(abs_path, new_subdir))
        new_paths = rebased_paths

    full_path = os.path.join(normalized_path, os.path.basename(new_file.fname))
    new_paths.append(os.path.relpath(full_path, new_subdir))
    return new_subdir, new_paths

class SinglePassFlag:
    def __init__(self, name: str, subdir: str, toolchain_info: VirtualToolchainInfo, c_flags: T.Optional[T.List[str]] = None, cpp_flags: T.Optional[T.List[str]] = None):
        self.name = name
        self.subdir = subdir
        self.cflags: T.List[str] = []
        self.cppflags: T.List[str] = []
        self.toolchain_info = toolchain_info
        if c_flags:
            self.cflags = [f.replace('"', '\\"') for f in c_flags]
        if cpp_flags:
            self.cppflags = [f.replace('"', '\\"') for f in cpp_flags]


class SinglePassIncludeDirectory:
    def __init__(self, toolchain_info: VirtualToolchainInfo, name: T.Optional[str] = None):
        self.subdir: T.Optional[str] = None
        self.paths: T.Set[str] = set()
        self.name = name
        self.toolchain_info = toolchain_info

    def add_include_dir(self, include_dir: build.IncludeDirs, project_info: VirtualProjectInfo):
        stable_id = get_stable_id(include_dir)
        self.name = project_info.assignment_tracker[stable_id].name
        subdir = project_info.assignment_tracker[stable_id].subdir

        normalized_paths: T.List[str] = []
        for directory in include_dir.get_incdirs():
            normalized_paths.append(project_info.normalize_path(directory, subdir))

        self.subdir = os.path.commonpath(normalized_paths)
        for normalized_path in normalized_paths:
            self.paths.add(os.path.relpath(normalized_path, self.subdir))

    def add_header_file(self, file: File, project_info: VirtualProjectInfo):
        self.subdir, paths = _add_file_to_group(file, project_info, self.subdir, list(self.paths))
        self.paths = set()
        for path in paths:
            if path.endswith('.h'):
                newpath = os.path.dirname(path)
                if not newpath:
                    newpath = '.'

                self.paths.add(newpath)
            else:
                self.paths.add(path)

class SinglePassFileGroup:
    def __init__(self, toolchain_info: VirtualToolchainInfo, name: T.Optional[str] = None):
        self.name = name
        self.subdir: T.Optional[str] = None
        self.srcs: T.List[str] = []
        self.toolchain_info = toolchain_info

    def add_source_file(self, file: File, project_info: VirtualProjectInfo):
        self.subdir, self.srcs = _add_file_to_group(file, project_info, self.subdir, self.srcs)
        
        if self.name is None:
            root, ext = os.path.splitext(self.srcs[0])
            self.name = root + '_' + ext[1:]

    def add_header_glob(self, subdir: str, headers: T.List[str]):
        self.subdir = subdir
        self.srcs = headers
        if self.name is not None:
            self.name = self.subdir.replace("/", "_") + "_glob"

