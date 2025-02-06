#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 The Meson Development Team

import os
import typing as T
from enum import Enum

from mesonbuild import build, programs
from mesonbuild.utils.universal import File
from mesonbuild.utils.core import MesonException

from tools.hermetic.single_pass.single_pass_utils import SinglePassFileGroup
from tools.hermetic.virtual.virtual_project_info import VirtualProjectInfo


class SinglePassCmdPartType(Enum):
    TOOL = 1
    PYTHON_BINARY = 2
    INPUT = 3
    OUTPUT = 4
    STRING = 5

class SinglePassCmdPart:
    def __init__(self, cmd: str, cmd_type: SinglePassCmdPartType):
        self.cmd: str = cmd
        self.cmd_type: SinglePassCmdPartType = cmd_type

    def __str__(self) -> str:
        return self.cmd

    def __eq__(self, other):
        if not isinstance(other, SinglePassCmdPart):
            return NotImplemented
        return self.cmd == other.cmd and self.cmd_type == other.cmd_type

    def __hash__(self):
        return hash((self.cmd, self.cmd_type))


def index_from_string(input_str: str) -> T.Optional[int]:
    valid_prefixes = ('@INPUT', '@OUTPUT')
    if input_str.startswith(valid_prefixes):
        index_as_str = input_str.replace('@INPUT', '').replace('@OUTPUT', '').rstrip('@')
        if index_as_str:
            return int(index_as_str)

    return 0


def is_python_script(input_str: str) -> bool:
    if input_str.endswith('.py') or input_str.endswith('_py'):
        return True

    return False

def python_script_to_binary(input_str: str) -> str:
    name = os.path.basename(input_str)
    if name.endswith('gen.py'):
        return name[:-6] + 'gen'
    if name.endswith('gen_py'):
        return name[:-6] + 'gen'
    if name.endswith('.py'):
        return name[:-3] + '_gen'
    if name.endswith('_py'):
        return name[:-3] + '_gen'
    return name + '_gen'

class SinglePassPythonTarget():
    def __init__(self):
        self.main: str = ''
        self.subdir: str = ''
        self.name: str = ''
        self.srcs: T.List[str] = []
        self.libs: T.List[str] = []


class SinglePassCustomTarget:
    def __init__(self, custom_target: build.CustomTarget, project_info: VirtualProjectInfo):
        self.is_python = False
        self.depend_files: T.List[str] = []

        self.tools: T.List[str] = []
        self.srcs: T.List[str] = []
        self.python_script: T.Optional[str] = None
        self.generated_headers: T.List[str] = []
        self.generated_sources: T.List[str] = []
        self.single_pass_cmds: T.List[SinglePassCmdPart] = []
        self.generated_filegroups: T.List[SinglePassFileGroup] = []
        self.skip_custom_target: bool = False

        self.name = project_info.sanitize_target_name(custom_target.name)
        self.subdir = custom_target.subdir
        self.project_info = project_info
        self._parse_custom_target(custom_target)

    def __eq__(self, other):
        if not isinstance(other, SinglePassCustomTarget):
            return NotImplemented
        return (self.srcs == other.srcs and
                self.tools == other.tools and
                self.generated_headers == other.generated_headers and
                self.generated_sources == other.generated_sources and
                set(self.single_pass_cmds) == set(other.single_pass_cmds))

    def get_python_target(self) -> T.Optional[SinglePassPythonTarget]:
        if self.python_script is None:
            return None

        python_target = SinglePassPythonTarget()
        python_target.main = self.python_script
        python_target.name = self.tools[0]
        python_target.subdir = self.subdir

        srcs = [python_target.main]
        for s in self.srcs:
           if is_python_script(s) and s != python_target.main:
                srcs += [s]

        python_target.srcs = srcs
        if self.project_info.programs:
            tool_config = self.project_info.programs.get('python3') or \
                          self.project_info.programs.get('python') or \
                          {}
            python_target.libs.extend(tool_config.get('dependencies', []))
        return python_target

    def get_generated_filegroups(self) -> T.List[SinglePassFileGroup]:
        return self.generated_filegroups

    def _handle_environment(self, custom_target: build.CustomTarget):
        if custom_target.env:
            for key, val in custom_target.env.get_env({}).items():
                sanitized_val = self.project_info.normalize_string(val, custom_target.subdir)
                env_cmd = f'{key}={sanitized_val}'
                self.single_pass_cmds.append(SinglePassCmdPart(env_cmd, SinglePassCmdPartType.STRING))

    def _parse_custom_target(self, custom_target: build.CustomTarget):

        self._handle_environment(custom_target)

        for command in custom_target.command:
            if isinstance(command, File):
                self._handle_file(command, custom_target)
            elif isinstance(command, programs.ExternalProgram):
                self._handle_program(command)
            elif isinstance(command, str):
                self._handle_string(command, custom_target)

        for output in custom_target.outputs:
            if output.endswith('.h'):
                self.generated_headers.append(output)
            else:
                self.generated_sources.append(output)

        for file in custom_target.depend_files:
            if isinstance(file, File):
                self._handle_file(file, custom_target)


    def _handle_input(self, src, custom_target: build.CustomTarget):
        if isinstance(src, File):
            self._handle_file(src, custom_target)
        elif isinstance(src, (build.CustomTarget, build.CustomTargetIndex)):
            output = src.get_outputs()[0]
            self.single_pass_cmds.append(SinglePassCmdPart(output, SinglePassCmdPartType.INPUT))
            self.srcs.append(output)
        elif isinstance(src, str):
            raise MesonException(f'Type: {type(src)} not handled, exiting...')

    def _handle_file(self, file: File, custom_target: build.CustomTarget):
        name = file.fname
        
        filegroup = SinglePassFileGroup(self.project_info.toolchain_info)
        filegroup.add_source_file(file, self.project_info)
        needs_filegroup = filegroup.subdir != custom_target.subdir
        if needs_filegroup:
            self.generated_filegroups.append(filegroup)
            name = ":" + filegroup.name

        if is_python_script(file.fname) and self.python_script is None:
            self.python_script = name
            python_binary = python_script_to_binary(file.fname)
            if python_binary in self.tools:
                return

            self.tools.append(python_binary)
            self.single_pass_cmds.append(SinglePassCmdPart(python_binary, SinglePassCmdPartType.PYTHON_BINARY))
        elif file not in custom_target.depend_files:
            if name in self.srcs:
                return
            
            self.single_pass_cmds.append(SinglePassCmdPart(name, SinglePassCmdPartType.INPUT))
            self.srcs.append(name)

    def _handle_program(self, program: programs.ExternalProgram):
        prog_name = program.get_name()
        if prog_name in {'python', 'python3'}:
            return

        if is_python_script(prog_name):
            self.python_script = prog_name
            python_binary = python_script_to_binary(prog_name)
            self.tools.append(python_binary)
            self.single_pass_cmds.append(SinglePassCmdPart(python_binary, SinglePassCmdPartType.PYTHON_BINARY))
        else:
            if self.project_info.programs:
                prog_config = self.project_info.programs.get(prog_name)
                if prog_config is None:
                    raise MesonException(f'Type: {type(prog_name)} not present, exiting...')

                tool_name = prog_name
                if prog_config and 'path' in prog_config:
                    tool_name = prog_config['path']

                self.single_pass_cmds.append(SinglePassCmdPart(tool_name, SinglePassCmdPartType.TOOL))

    def _handle_string(self, command_string: str, custom_target: build.CustomTarget):
        if command_string == '@INPUT@':
            for j, src in enumerate(custom_target.sources):
                self._handle_input(src, custom_target)
        elif command_string.startswith('@INPUT'):
            idx = index_from_string(command_string)
            src = custom_target.sources[idx]
            self._handle_input(src, custom_target)
        elif command_string.startswith('@OUTPUT'):
            if custom_target.capture:
                self.single_pass_cmds.extend('>', SinglePassCmdPartType.STRING)

            value = index_from_string(command_string)
            output = custom_target.outputs[value]

            self.single_pass_cmds.append(SinglePassCmdPart(output, SinglePassCmdPartType.OUTPUT))
        else:
            normalized_string = self.project_info.normalize_string(command_string, custom_target.subdir)
            processed_string = self._handle_normalized_string(normalized_string, custom_target)
            self.single_pass_cmds.append(SinglePassCmdPart(processed_string, SinglePassCmdPartType.STRING))

    def _handle_normalized_string(self, normalized_str: str, custom_target: build.CustomTarget) -> str:
        sanitized_parts = []
        string_parts = normalized_str.split(' ')

        for part in string_parts:
            if part.startswith('-I'):
                path = part[2:]
                if path.startswith('@@PROJECT_DIR@@'):
                    subdir = path.replace('@@PROJECT_DIR@@', '')
                    if subdir.startswith('/'):
                        subdir = subdir[1:]

                    headers = self.project_info.glob_headers(subdir)
                    filegroup = SinglePassFileGroup(self.project_info.toolchain_info)
                    filegroup.add_header_glob(subdir, headers)
                    self.generated_filegroups.append(filegroup)
                    sanitized_parts.append('-I' + subdir)
                else:
                    sanitized_parts.append(part)
            elif part.startswith('@@PROJECT_DIR@@'):
                sanitized = part.replace('@@PROJECT_DIR@@', '')
                sanitized_parts.append(sanitized)
            elif part.startswith('@@INSTALL_DIR@@'):
                # Install dir undefined for hermetic builds for now
                self.skip_custom_target = True
            elif '@DEPFILE@' in part:
                depfile = custom_target.get_dep_outname(self.srcs)
                sanitized_parts.append(part.replace('@DEPFILE@', depfile))
            else:
                sanitized_parts.append(part)

        return ' '.join(sanitized_parts)
