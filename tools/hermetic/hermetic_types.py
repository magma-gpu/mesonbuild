#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 The Meson Authors

from __future__ import annotations
import contextlib
import enum
import tomllib
import typing as T
from pathlib import Path
import os
import hashlib

from jinja2 import Environment, FileSystemLoader

from mesonbuild import build, environment, coredata, interpreter, mlog, programs, mparser
from mesonbuild.dependencies import base as dependency_base
from mesonbuild.dependencies import Dependency, InternalDependency
from mesonbuild.envconfig import MachineInfo
from mesonbuild.interpreterbase import InterpreterObject, ObjectHolder, noArgsFlattening
from mesonbuild.interpreterbase.decorators import noKwargs
from mesonbuild.mesonlib import MachineChoice, HoldableObject
from mesonbuild.compilers.c import ClangCCompiler
from mesonbuild.compilers.cpp import ClangCPPCompiler
from mesonbuild.compilers.rust import RustCompiler
from mesonbuild.linkers.linkers import GnuBFDDynamicLinker
from mesonbuild.compilers.compilers import CompileResult
from mesonbuild.utils import universal
from mesonbuild.utils.core import MesonException
from enum import Enum
from mesonbuild import options


if T.TYPE_CHECKING:
    from mesonbuild.coredata import SharedCMDOptions

    class CMDOptions(SharedCMDOptions):
        configdir: str

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
    return None

class HermeticFlag:
    def __init__(self, name: str, subdir: str):
        self.name = name
        self.subdir = subdir
        self.cflags: T.List[str] = []
        self.cppflags: T.List[str] = []

class HermeticIncludeDirectory:
    def __init__(self, inc: build.IncludeDirs, name: str):
        self.name = name
        self.subdir: str = inc.get_curdir()
        self.paths: T.Tuple[str, ...] = tuple(sorted(inc.get_incdirs()))

class HermeticState:

    def __init__(self):
        self.shared_libraries: T.List[HermeticSharedLibrary] = []
        self.static_libraries: T.List[HermeticStaticLibrary] = []
        self.rust_libraries: T.List[HermeticRustLibrary] = []
        self.custom_targets: T.List[HermeticCustomTarget] = []
        self.python_targets: T.List[HermeticPythonTarget] = []
        self.filegroups: T.Dict[str, T.List[str]] = {}
        self.dependencies: T.List[MesonDeclaredDependency] = []
        self.include_directories: T.List[HermeticIncludeDirectory] = []
        self.flags: T.Dict[str, HermeticFlag] = {}

        self.c_std: str = ''
        self.cpp_std: str = ''
        self.root_subdir: str = ''

    def copts(self):
        pass

    def __str__(self):
        return f'HermeticState:\n\tshared_libraries len: {len(self.shared_libraries)}' \
            f'\n\tstatic_libraries len: {len(self.static_libraries)}' \
            f'\n\tcustom_targets len: {len(self.custom_targets)}' \
            f'\n\tpython_targets len: {len(self.python_targets)}'


class RustABI(Enum):
    RUST = 'rust'
    C = 'c'
    NONE = None

class HermeticAssignment:
    def __init__(self, name: str, subdir: str):
        self.name: str = name
        self.subdir: str = subdir

class HermeticStaticLibrary:

    def __init__(self):
        self.name: str = ''
        self.subdir: str = '' # Location of this StaticLibrary's definition
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

        self.deps: T.List[str] = []
        self.target_compatible_with: T.List[str] = []

        self.include_dirs: T.List[str] = []
        self.local_include_dirs: T.List[str] = []
        self.static_libs: T.List[str] = []
        self.whole_static_libs: T.List[str] = []
        self.shared_libs: T.List[str] = []
        self.flags: T.List[str] = []
        self.header_libs: T.List[str] = []

    def convert_from_meson(self, meson_sl: build.StaticLibrary, assignment_tracker: T.Dict[str, HermeticAssignment], projects: T.Dict[str, str]) -> (T.Optional[HermeticFlag], T.Optional[HermeticFlag]):
        self.name = meson_sl.get_basename()
        self.srcs = [s.fname for s in meson_sl.sources]
        self.subdir = meson_sl.subdir
        project_name = projects[meson_sl.subproject]
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
                    self.shared_libs.append(d.name)

        c_args = meson_sl.get_extra_args('c')
        cpp_args = meson_sl.get_extra_args('cpp')

        c_flag_ret = None
        cpp_flag_ret = None

        if c_args:
            stable_id = get_stable_id(c_args)
            name = ''
            subdir = ''
            if stable_id in assignment_tracker:
                name = assignment_tracker[stable_id].name
                subdir = assignment_tracker[stable_id].subdir
            else:
                name = f'{self.name}_c_flags'
                subdir = self.subdir
            c_flag_ret = HermeticFlag(name, subdir)
            c_flag_ret.cflags = c_args
            self.flags.append(name)

        if cpp_args:
            stable_id = get_stable_id(cpp_args)
            name = ''
            subdir = ''
            if stable_id in assignment_tracker:
                name = assignment_tracker[stable_id].name
                subdir = assignment_tracker[stable_id].subdir
            else:
                name = f'{self.name}_cpp_flags'
                subdir = self.subdir
            cpp_flag_ret = HermeticFlag(name, subdir)
            cpp_flag_ret.cppflags = cpp_args
            self.flags.append(name)

        for include_dir in meson_sl.include_dirs:
            stable_id = get_stable_id(include_dir)
            if stable_id in assignment_tracker:
                name = assignment_tracker[stable_id].name
                # why does meson have duplicate IncludeDirectories?
                if name not in self.header_libs:
                    self.header_libs.append(name)

        processed_targets = set()
        for target in meson_sl.get_generated_sources():
            if not isinstance(target, build.CustomTarget):
                continue
            if target.name in processed_targets:
                continue
            processed_targets.add(target.name)

            has_headers = any(o.endswith('.h') for o in target.outputs)
            has_sources = any(not o.endswith('.h') for o in target.outputs)

            if has_headers and has_sources:
                if f'{target.name}_header' not in self.generated_headers:
                    self.generated_headers.append(f'{target.name}_header')
                if f'{target.name}_impl' not in self.generated_sources:
                    self.generated_sources.append(f'{target.name}_impl')
            elif has_headers:
                if target.name not in self.generated_headers:
                    self.generated_headers.append(target.name)
            else:
                if target.name not in self.generated_sources:
                    self.generated_sources.append(target.name)

        for target in meson_sl.link_targets:
            if isinstance(target, build.StaticLibrary):
                self.static_libs.append(target.name)
            elif isinstance(target, build.SharedLibrary):
                self.shared_libs(target.name)

        for target in meson_sl.link_whole_targets:
            if isinstance(target, build.StaticLibrary):
                self.whole_static_libs.append(target.name)

        return c_flag_ret, cpp_flag_ret


    def __str__(self):
        return f'@StaticLibrary({self.name})'

class HermeticSharedLibrary(HermeticStaticLibrary):
    '''
    Exactly same metadata as StaticLibrary besides how it's generated in Soong and Bazel files
    '''
    def convert_from_meson(self, meson_sl: build.SharedLibrary, assignment_tracker: T.Dict[str, HermeticAssignment], projects: T.Dict[str, str]) -> (T.Optional[HermeticFlag], T.Optional[HermeticFlag]):
        return super().convert_from_meson(meson_sl, assignment_tracker, projects)

    def __str__(self):
        return f'@SharedLibrary({self.name})'

class HermeticCustomTarget:

    def __init__(self):
        self.name: str = ''
        self.subdir: str = ''
        self.srcs: T.List[str] = []
        self.out: T.List[str] = []  # 'outs' in bazel
        self.generated_headers: T.List[str] = []
        self.generated_sources: T.List[str] = []
        self.tools: T.List[str] = []
        self.export_include_dirs: T.List[str] = []
        self.cmd: T.List[str] = []
        self.cmd_parts: T.List[T.Any] = []

        self.python_script = ''
        self.python_script_target_name = ''

    def convert_from_meson(self, custom_target: build.CustomTarget) -> None:
        self.name = custom_target.name
        self.subdir = custom_target.subdir

        for out in custom_target.outputs:
            self.out.append(out)
            if out.endswith('.h'):
                self.generated_headers.append(out)
            else:
                self.generated_sources.append(out)

        self.depend_files = [f.fname for f in custom_target.depend_files if isinstance(f, universal.File)]

        if custom_target.command and isinstance(custom_target.command[0], programs.ExternalProgram) and custom_target.command[0].get_name() == 'python3':
            script = custom_target.command[1]
            if isinstance(script, universal.File):
                self.python_script = script
                self.python_script_target_name = os.path.splitext(os.path.basename(script.fname))[0] + '_gen'
            elif isinstance(script, str) and script.startswith('@INPUT'):
                index_str = script.replace('@INPUT', '').rstrip('@')
                index = int(index_str) if index_str else 0
                self.python_script = custom_target.sources[index]
                self.python_script_target_name = os.path.splitext(os.path.basename(self.python_script.fname))[0] + '_gen'

        for src in custom_target.sources:
            if self.python_script and src == self.python_script:
                continue
            if isinstance(src, (universal.File)):
                self.srcs.append(src.fname)
            elif isinstance(src, (build.CustomTarget, build.CustomTargetIndex)):
                self.srcs.extend(str(s) for s in src.get_outputs())
            elif isinstance(src, (build.StaticLibrary)):
                self.srcs.append(src.filename)
            else:
                # TODO: handle all other possible types
                raise MesonException(f'Type: {type(src)} not handled, exiting...')

        if custom_target.command:
            cmd_parts = []
            i = 0
            while i < len(custom_target.command):
                part = custom_target.command[i]
                part_str = str(part)
                if isinstance(part, build.BuildTarget):
                    if not self.tools or self.tools[-1] != part.name:
                        self.tools.append(part.name)
                    cmd_parts.append(('location', part.name, None))
                elif isinstance(part, universal.File):
                    if part == self.python_script:
                        self.tools.append(self.python_script_target_name)
                        cmd_parts.append(('location', self.python_script_target_name, None))
                    else:
                        cmd_parts.append(('location', part.fname, part.fname))
                elif isinstance(part, programs.ExternalProgram):
                    cmd_parts.append(part.name)
                elif part_str == '@INPUT@':
                    for j, src in enumerate(custom_target.sources):
                        if self.python_script and src == self.python_script:
                            if self.python_script_target_name not in self.tools:
                                self.tools.append(self.python_script_target_name)
                            cmd_parts.append(('location', self.python_script_target_name, None))
                        else:
                            if isinstance(src, (build.CustomTarget, build.CustomTargetIndex)):
                                cmd_parts.append(('input', j, src.get_outputs()[0]))
                            elif isinstance(src, (build.StaticLibrary)):
                                cmd_parts.append(('input', j, src.filename))
                            else:
                                cmd_parts.append(('input', j, src.fname))
                elif part_str.startswith('@INPUT'):
                    index_str = part_str.replace('@INPUT', '').rstrip('@')
                    index = int(index_str) if index_str else 0
                    src = custom_target.sources[index]

                    if self.python_script and src == self.python_script:
                        self.tools.append(self.python_script_target_name)
                        cmd_parts.append(('location', self.python_script_target_name, None))
                    else:
                        if isinstance(src, (build.CustomTarget, build.CustomTargetIndex)):
                            cmd_parts.append(('input', index, src.get_outputs()[0]))
                        elif isinstance(src, (build.StaticLibrary)):
                            cmd_parts.append(('input', index, src.filename))
                        else:
                            cmd_parts.append(('input', index, src.fname))
                elif part_str.startswith('@OUTPUT'):
                    index_str = part_str.replace('@OUTPUT', '').rstrip('@')
                    if not index_str:
                        cmd_parts.append(('output', 0, self.out[0]))
                    else:
                        index = int(index_str)
                        cmd_parts.append(('output', index, self.out[index]))
                elif part_str == os.path.join(custom_target.environment.get_build_dir(), custom_target.subdir):
                    cmd_parts.append('gen_dir')
                else:
                    cmd_parts.append(part_str)
                i += 1
            self.cmd_parts = cmd_parts

        if custom_target.capture:
            if len(self.out) >= 1:
                self.cmd_parts.extend(['&>', ('output', 0, self.out[0])])

        self.export_include_dirs.append(custom_target.subdir)

    def emit_python_target(self, host_tools_config: T.Dict[str, T.Any]) -> T.Union[HermeticPythonTarget, None]:
        '''
        Not all custom targets have valid python targets.
        Function may return None for those cases.
        '''
        if not self.python_script:
            return None
        
        python_custom_target = HermeticPythonTarget(self, host_tools_config)
        return python_custom_target
    
    def __str__(self):
        return f'HermeticCustomTarget({self.name})'

class HermeticPythonTarget(HermeticCustomTarget):

    def __init__(self, custom_target: T.Optional[HermeticCustomTarget] = None, host_tools_config: T.Dict[str, T.Any] = None):
        super().__init__()
        self.main: str = ''
        self.imports: T.List[str] = []
        self.version: T.Dict[T.Any, T.Any] = {}
        self.libs: T.List[str] = []
        if custom_target is None:
            return

        self.main = os.path.basename(str(custom_target.python_script))
        self.name = custom_target.python_script_target_name
        self.subdir = custom_target.subdir

        self.srcs = [self.main] + [s for s in custom_target.depend_files if s.endswith('.py')]
        self.srcs = sorted(list(set(self.srcs)))

        if host_tools_config:
            tool_config = host_tools_config.get('python3', {}) or host_tools_config.get('python', {})
            self.libs.extend(tool_config.get('dependencies', []))

        self.out = custom_target.out.copy()
        self.export_include_dirs = custom_target.export_include_dirs.copy()

class ToolchainFactory:
    def __init__(self, toolchain_config: T.Dict[str, T.Any], for_machine: MachineChoice, info: MachineInfo):
        self.config = toolchain_config
        self.for_machine = for_machine
        self.info = info
        self.linkers = self._create_linkers()

    def _create_linkers(self) -> T.Dict[str, GnuBFDDynamicLinker]:
        linkers = {}
        for name, conf in self.config.get('linkers', {}).items():
            if conf['type'] == 'gnu-like':
                linker = GnuBFDDynamicLinker(['/dev/null/ld'], self.for_machine, '-Wl,', [])
                linker.id = name
                linkers[name] = linker
        return linkers

    def get_linker(self, name: str) -> GnuBFDDynamicLinker:
        return self.linkers.get(name)

    def create_compiler(self, lang: str) -> T.Any:
        conf = self.config.get(lang)
        if not conf:
            return None

        linker = self.get_linker(conf['linker'])
        compiler_type = conf['type']
        version = conf['version']
        exelist = [f'/dev/null/{compiler_type}']

        compiler = None
        if compiler_type == 'clang':
            compiler = ClangCCompiler([], exelist, version, self.for_machine, True, self.info, linker=linker, full_version=version)
        elif compiler_type == 'clang++':
            compiler = ClangCPPCompiler([], exelist, version, self.for_machine, True, self.info, linker=linker, full_version=version)
        elif compiler_type == 'rustc':
            compiler = RustCompiler(exelist, version, self.for_machine, True, self.info, linker=linker, full_version=version)

        if compiler:
            def find_library(libname, env, *args, **kwargs):
                return dependency_base.NotFoundDependency(libname, env)
            compiler.find_library = find_library

            original_get_options = compiler.get_options
            def new_get_options(self) -> 'options.MutableKeyedOptionDictType':
                opts = original_get_options()
                key = self.form_compileropt_key('args')
                opts[key] = options.UserStringArrayOption(
                    self.make_option_name(key),
                    'Extra arguments passed to the compiler',
                    [],
                )
                key = self.form_compileropt_key('link_args')
                opts[key] = options.UserStringArrayOption(
                    self.make_option_name(key),
                    'Extra arguments passed to the linker',
                    [],
                )
                return opts
            compiler.get_options = new_get_options.__get__(compiler)
            compiler.sanity_check = lambda work_dir, env: None
            compiler.get_default_include_dirs = lambda: []
            compiler.get_define = lambda *args, **kwargs: ('', False)
            compiler.cross_compute_int = lambda *args, **kwargs: 0

            compiles_results = conf.get('compiles', {})
            compiler.compiles = lambda snippet, name, **kwargs: (compiles_results.get(name, True), True)

            links_results = conf.get('links', {})
            compiler.links = lambda snippet, name, **kwargs: (links_results.get(name, True), True)

            check_header_results = conf.get('check_header', {})
            compiler.check_header = lambda header, *args, **kwargs: (check_header_results.get(header, True), True)

            has_header_symbol_results = conf.get('has_header_symbol', {})
            def has_header_symbol(header, symbol, *args, **kwargs):
                header_symbols = has_header_symbol_results.get(header, {})
                return (header_symbols.get(symbol, True), True)
            compiler.has_header_symbol = has_header_symbol

            has_function_results = conf.get('has_function', {})
            compiler.has_function = lambda func, *args, **kwargs: (has_function_results.get(func, True), True)

            has_member_results = conf.get('has_member', {})
            def has_member(typename, member, prefix):
                type_members = has_member_results.get(typename, {})
                return type_members.get(member, True)
            compiler.has_member = has_member

            supported_args = conf.get('supported_arguments', [])
            compiler.get_supported_arguments = lambda args: [a for a in args if a in supported_args]

            supported_link_args = conf.get('supported_link_arguments', [])
            compiler.get_supported_link_arguments = lambda args: [a for a in args if a in supported_link_args]

            has_function_attribute_results = conf.get('has_function_attribute', {})
            compiler.has_function_attribute = lambda attribute: has_function_attribute_results.get(attribute, True)

            get_supported_function_attributes_results = conf.get('has_function_attribute', {})
            compiler.get_supported_function_attributes = lambda attributes: [a for a in attributes if a in get_supported_function_attributes_results]

            @contextlib.contextmanager
            def mock_compile(*args, **kwargs):
                yield CompileResult('', '', [], 0, '')

            compiler.compile = mock_compile
            if isinstance(compiler, RustCompiler):
                compiler.native_static_libs = []

        return compiler

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

class HermeticInterpreter(interpreter.Interpreter):
    def __init__(self, build, config, **kwargs):
        super().__init__(build, **kwargs)
        self.config = config
        self.assignment_tracker: T.Dict[str, HermeticAssignment] = {}
        self.hermetic_includes: T.List[HermeticIncludeDirectory] = []
        self.hermetic_project_args: T.Dict[str, T.Dict[str, T.List[str]]] = {}
        self.variables['host_machine'] = MachineHolder(self.build.environment.machines.host)
        self.variables['build_machine'] = MachineHolder(self.build.environment.machines.build)
        self.variables['target_machine'] = MachineHolder(self.build.environment.machines.target)
        self.funcs['add_project_arguments'] = self.func_add_project_arguments

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
            self.hermetic_project_args.setdefault(self.subproject, {}).setdefault(lang, []).extend(args_str)

    def _redetect_machines(self):
        pass

    def assignment(self, node: mparser.AssignmentNode):
        var_name = node.var_name.value
        super().assignment(node)
        value_holder = self.variables[var_name]

        raw_obj = value_holder
        if isinstance(value_holder, ObjectHolder):
            raw_obj = value_holder.held_object

        if isinstance(raw_obj, build.IncludeDirs):
            hermetic_inc = HermeticIncludeDirectory(raw_obj, var_name)
            self.hermetic_includes.append(hermetic_inc)

        stable_id = get_stable_id(raw_obj)
        if stable_id:
            self.assignment_tracker[stable_id] = HermeticAssignment(var_name, self.subdir)

    def func_dependency(self, node, args, kwargs):
        dep_name = args[0] if args else kwargs.get('name', 'unnamed')
        if dep_name in self.config.dependencies:
            dep_info = self.config.dependencies[dep_name]
            
            dep = dependency_base.ExternalDependency('system', self.environment, kwargs)
            dep.is_found = True
            dep.version = dep_info[0].get('version', 'hermetic') if dep_info else 'hermetic'

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

            for d in dep_info:
                dep.name = d.get('target_name')
                target_type = d.get('target_type')
                
                if target_type == 'STATIC_LIBRARY':
                    dep.static = True

            return dep

        return dependency_base.NotFoundDependency(dep_name, self.environment)

class HermeticConfig:
    def __init__(self, config_file: Path, toolchain_file: T.Optional[Path] = None):
        self._path: Path = config_file
        self._toolchain_path: T.Optional[Path] = toolchain_file
        self._toml_data: dict[str, T.Any] = {}
        try:
            with open(self._path, "rb") as f:
                self._toml_data = tomllib.load(f)
            if self._toolchain_path:
                with open(self._toolchain_path, "rb") as f:
                    toolchain_data = tomllib.load(f)
                    self._toml_data.update(toolchain_data)
        except Exception as e:
            exit(f'Error trying to open config file: {e}')

    @property
    def build(self):
        return self._toml_data.get('build')

    @property
    def project_config(self):
        return self._toml_data.get('project_config', {})

    @property
    def host_machine(self) -> dict[str, str]:
        return self.project_config.get('host_machine', {})

    @property
    def build_machine(self) -> dict[str, str]:
        return self.project_config.get('build_machine', {})

    @property
    def target_machine(self) -> dict[str, str]:
        return self.project_config.get('target_machine', {})

    @property
    def meson_options(self):
        return self.project_config.get('meson_options', {})

    @property
    def dependencies(self):
        return self.project_config.get('dependencies', {})
        
    @property
    def host_tools(self):
        return self._toml_data.get('host_tools', {})

    @property
    def toolchain(self):
        return self._toml_data.get('toolchain', {})

    @property
    def copyright(self):
        return self._toml_data.get('copyright', {})

    def project_options(self) -> list[str]:
        return [f'{k}={str(v).lower()}' for k, v in self.meson_options.items()]

class DefaultCMDOptions(enum.Enum):
    SOURCE_DIR = os.getcwd()
    BUILD_DIR = os.getcwd() + '/hermetic-build'
    CROSS_FILE = []
    NATIVE_FILE = []
    BACKEND = 'hermetic'

class Generator:
    def __init__(self, build_system: str, output_dir: str, hermetic_state: HermeticState, config: 'HermeticConfig'):
        self.build_system = build_system
        self.output_dir = output_dir
        self.hermetic_state = hermetic_state
        self.config = config
        self.jinja_env = Environment(keep_trailing_newline=True)
        self.jinja_env.loader = FileSystemLoader(Path(__file__).parent.resolve() / f'hermetic_templates/{build_system}')

    def generate(self):
        raise NotImplementedError

    def write_build_file(self, subdir: str, build_file_name: str, content: str):
        output_path = Path(self.output_dir) / subdir
        output_path.mkdir(parents=True, exist_ok=True)
        (output_path / build_file_name).write_text(content, encoding='utf-8')
