#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 The Meson Development Team

from __future__ import annotations
import typing as T
from pathlib import Path
import os
import sys
import hashlib

from mesonbuild import build, interpreter, programs, mparser
from mesonbuild.interpreter.interpreterobjects import RunProcess
from mesonbuild.dependencies import base as dependency_base
from mesonbuild.interpreterbase import InterpreterObject, ObjectHolder, noArgsFlattening
from mesonbuild.interpreterbase.decorators import noKwargs

if T.TYPE_CHECKING:
    from tools.hermetic.virtual.virtual_project_info import VirtualProjectInfo


class NameAssignment:
    def __init__(self, name: str, subdir: str):
        self.name: str = name
        self.subdir: str = subdir


class MachineHolder(InterpreterObject):
    def __init__(self, machine_info):
        super().__init__()
        self.holder = machine_info

    @InterpreterObject.method('system')
    @noKwargs
    def system_method(self, args, kwargs):
        return self.holder.system

    @InterpreterObject.method('cpu_family')
    @noKwargs
    def cpu_family_method(self, args, kwargs):
        return self.holder.cpu_family

    @InterpreterObject.method('cpu')
    @noKwargs
    def cpu_method(self, args, kwargs):
        return self.holder.cpu


class VirtualInterpreter(interpreter.Interpreter):
    def __init__(self, build, info: VirtualProjectInfo, **kwargs):
        super().__init__(build, **kwargs)
        self.config = info
        self.project_info = info

        prefix = self.environment.get_prefix()
        libdir = self.environment.get_libdir()
        install_dir = libdir
        if not os.path.isabs(libdir):
            install_dir = os.path.join(prefix, libdir)

        self.project_info.set_directories(
            self.environment.get_source_dir(),
            self.environment.get_build_dir(),
            install_dir
        )
        self.single_pass_project_args: T.Dict[str, T.Dict[str, T.List[str]]] = {}
        self.variables['host_machine'] = MachineHolder(self.build.environment.machines.host)
        self.variables['build_machine'] = MachineHolder(self.build.environment.machines.build)
        self.variables['target_machine'] = MachineHolder(self.build.environment.machines.target)
        self.funcs['add_project_arguments'] = self.func_add_project_arguments
        self.funcs['find_program'] = self.func_find_program

    @noArgsFlattening
    def func_find_program(self, node: mparser.BaseNode, args: T.List[T.Any], kwargs: T.Dict[str, T.Any]):
        prog_names = args[0]
        if not isinstance(prog_names, list):
            prog_names = [prog_names]

        for prog_name in prog_names:
            # Check if the program is a script in the source tree
            script_path = os.path.join(self.environment.source_dir, self.subdir, prog_name)
            is_file = os.path.isfile(script_path)
            if is_file:
                # If it's a python script, prepend the python executable
                if prog_name.endswith('.py'):
                    return programs.ExternalProgram(prog_name, command=[sys.executable, script_path])
                return programs.ExternalProgram(prog_name, command=[script_path])

            prog_info = self.config.programs.get(prog_name)
            if prog_info:
                prog = programs.ExternalProgram(prog_name, command=[prog_name])
                prog.found = lambda: True
                version = prog_info.get('version')
                if version:
                    prog.version = version
                return prog

        return programs.NonExistingExternalProgram(prog_names[0])

    def run_command_impl(self, args, kwargs, in_builddir=False) -> RunProcess:
        emulated_process = RunProcess.__new__(RunProcess)
        emulated_process.returncode = 0

        cmd = args[0]
        raw_args = args[1:]

        cmd_args = []
        for arg in raw_args:
            if isinstance(arg, list):
                cmd_args.extend(arg)
            else:
                cmd_args.append(arg)

        cmd_name = ''
        if isinstance(cmd, programs.ExternalProgram):
            cmd_name = cmd.get_name()
        elif isinstance(cmd, str):
            cmd_name = cmd
        else:
            cmd_name = cmd[0]
            cmd_args = cmd[1:] + cmd_args

        prog_info = self.config.programs.get(cmd_name)
        if '--version' in cmd_args:
            if prog_info and 'version' in prog_info:
                emulated_process.stdout = str(prog_info['version'])
                emulated_process.stderr = ''

        emulated_process.subproject = self.subproject
        return emulated_process

    @noArgsFlattening
    def func_add_project_arguments(self, node: mparser.BaseNode, args: T.List[T.Any], kwargs: T.Dict[str, T.Any]):
        args_flat = []
        for arg in args:
            if isinstance(arg, list):
                args_flat.extend(arg)
            else:
                args_flat.append(arg)

        args_str = [str(x) for x in args_flat]

        # This is not entirely correct, as it doesn't respect `native:` kwarg.
        # But for non-cross builds it should be fine.
        langs = kwargs.get('language', [])
        if not isinstance(langs, list):
            langs = [langs]

        for lang in langs:
            self.single_pass_project_args.setdefault(self.subproject, {}).setdefault(lang, []).extend(args_str)

    def _redetect_machines(self):
        pass

    def track_assignment(self, var_name: str):
        value_holder = self.variables.get(var_name)
        if not value_holder:
            return

        raw_obj = value_holder
        if isinstance(value_holder, ObjectHolder):
            raw_obj = value_holder.held_object

        if isinstance(raw_obj, list) and all(isinstance(f, build.File) for f in raw_obj):
            for f in raw_obj:
                stable_id = get_stable_id(f)
                self.project_info.assignment_tracker[stable_id] = NameAssignment(var_name, self.subdir)
        else:
            stable_id = get_stable_id(raw_obj)
            if stable_id:
                self.project_info.assignment_tracker[stable_id] = NameAssignment(var_name, self.subdir)

    def assignment(self, node: mparser.AssignmentNode):
        var_name = node.var_name.value
        super().assignment(node)
        self.track_assignment(var_name)

    def evaluate_plusassign(self, node: mparser.PlusAssignmentNode):
        # does nothing interesting now, but maybe we'll have to update assigments..
        super().evaluate_plusassign(node)

    def func_dependency(self, node, args, kwargs):
        desired_dep_name = args[0] if args else kwargs.get('name', 'unnamed')

        dep_info = self.config.shared_libraries.get(desired_dep_name) or \
            self.config.static_libraries.get(desired_dep_name) or \
            self.config.header_libraries.get(desired_dep_name)

        if dep_info:
            dep = dependency_base.ExternalDependency('system', self.environment, kwargs)
            dep.is_found = True
            dep.version = dep_info[0].get('version', 'single_pass') if dep_info else 'single_pass'

            configtool_checks = dep_info[0].get('configtool', {}) if dep_info else {}
            pkgconfig_checks = dep_info[0].get('pkgconfig', {}) if dep_info else {}

            def get_variable(*args, **kwargs):
                ct_var = kwargs.get('configtool')
                if ct_var and ct_var in configtool_checks:
                    return configtool_checks[ct_var]
                pc_var = kwargs.get('pkgconfig')
                if pc_var and pc_var in pkgconfig_checks:
                    return pkgconfig_checks[pc_var]
                return None
            dep.get_variable = get_variable

            dep.name = dep_info[0].get('target_name')

            if self.config.static_libraries.get(desired_dep_name):
                dep.static = True

            return dep

        return dependency_base.NotFoundDependency(desired_dep_name, self.environment)

def get_stable_id(obj: T.Any) -> T.Optional[str]:
    if isinstance(obj, build.IncludeDirs):
        canonical_string = '|'.join([
            obj.curdir,
            ','.join(sorted(obj.incdirs)),
            str(obj.is_system),
            ','.join(sorted(obj.extra_build_dirs)),
        ])
        return hashlib.sha1(canonical_string.encode()).hexdigest()
    elif isinstance(obj, list):
        if all(isinstance(i, str) for i in obj):
            canonical_string = ','.join(sorted(obj))
            return hashlib.sha1(canonical_string.encode()).hexdigest()
    elif isinstance(obj, build.File):
        canonical_string = obj.fname
        return hashlib.sha1(canonical_string.encode()).hexdigest()
    return None
