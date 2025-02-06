#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 The Meson Development Team

from __future__ import annotations
import contextlib
import typing as T

from mesonbuild.dependencies import base as dependency_base
from mesonbuild.envconfig import MachineInfo
from mesonbuild.mesonlib import MachineChoice
from mesonbuild.compilers.c import ClangCCompiler
from mesonbuild.compilers.cpp import ClangCPPCompiler
from mesonbuild.compilers.rust import RustCompiler
from mesonbuild.linkers.linkers import GnuBFDDynamicLinker
from mesonbuild.compilers.compilers import CompileResult, Compiler
from mesonbuild import options

from .virtual_project_info import VirtualToolchainInfo


def _find_library(self, libname: str, env, *args, **kwargs) -> T.Optional[T.List[str]]:
    if libname == "rt":
        return ['']
    return None


@contextlib.contextmanager
def _emulated_compile(*args, **kwargs):
    yield CompileResult('', '', [], 0, '')


def _get_new_get_options_func(original_get_options):
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
    return new_get_options


def _get_has_header_symbol_func(fails_results: T.Dict[str, T.Dict[str, T.Any]]):
    def has_header_symbol(header, symbol, *args, **kwargs):
        header_symbols_fails = fails_results.get(header, {})
        return (symbol not in header_symbols_fails, True)
    return has_header_symbol


def _get_has_member_func(fails_results: T.Dict[str, T.Dict[str, T.Any]]):
    def has_member(typename, member, prefix):
        type_members_fails = fails_results.get(typename, {})
        return member not in type_members_fails
    return has_member



class VirtualToolchain:
    def __init__(self, host_machine_toolchain: str, build_machine_toolchain: str, toolchain_config: T.Dict[str, T.Any]):
        self.toolchains = {}
        self.toolchain_info = VirtualToolchainInfo(build_machine_toolchain, host_machine_toolchain, toolchain_config)
        self.toolchains[MachineChoice.HOST] = toolchain_config.get(host_machine_toolchain)
        self.toolchains[MachineChoice.BUILD] = toolchain_config.get(build_machine_toolchain)

    def create_c_compiler(self, choice: MachineChoice) -> T.Optional[ClangCCompiler]:
        machine_info = self.toolchain_info.machine_info[choice]
        c_info = self.toolchains[choice].get('c')
        if not c_info:
            return None
        version = c_info.get('version')
        compiler_id = c_info.get('compiler_id')
        linker_id = c_info.get('linker_id')
        exelist = [f'/dev/null/{compiler_id}']
        linker = None
        if linker_id:
            linker = GnuBFDDynamicLinker([f'/dev/null/{linker_id}'], choice, '', [])

        compiler = ClangCCompiler([], exelist, version, choice, True,
                                  machine_info, linker=linker, full_version=version)
        self._configure_compiler(compiler, c_info)
        return compiler

    def create_cpp_compiler(self, choice: MachineChoice) -> T.Optional[ClangCPPCompiler]:
        machine_info = self.toolchain_info.machine_info[choice]
        cpp_info = self.toolchains[choice].get('cpp')
        if not cpp_info:
            return None
        version = cpp_info.get('version')
        compiler_id = cpp_info.get('compiler_id')
        exelist = [f'/dev/null/{compiler_id}']

        compiler = ClangCPPCompiler([], exelist, version, choice, True,
                                    machine_info, None, full_version=version)
        self._configure_compiler(compiler, cpp_info)
        return compiler

    def create_rust_compiler(self, choice: MachineChoice) -> T.Optional[RustCompiler]:
        machine_info = self.toolchain_info.machine_info[choice]
        rs_info = self.toolchains[choice].get('rust')
        if not rs_info:
            return None
        version = rs_info.get('version')
        compiler_id = rs_info.get('compiler_id')
        exelist = [f'/dev/null/{compiler_id}']

        compiler = RustCompiler(exelist, version, choice, True,
                                machine_info, full_version=version)
        self._configure_compiler(compiler, rs_info)
        return compiler

    def _configure_compiler(self, compiler: Compiler, conf: T.Dict[str, T.Any]) -> Compiler:
        compiler.find_library = _find_library.__get__(compiler)
        original_get_options = compiler.get_options
        new_get_options_func = _get_new_get_options_func(original_get_options)
        compiler.get_options = new_get_options_func.__get__(compiler)

        compiler.sanity_check = lambda work_dir, env: None
        compiler.get_default_include_dirs = lambda: []
        compiler.get_define = lambda *args, **kwargs: ('', False)
        compiler.cross_compute_int = lambda *args, **kwargs: 0

        compiles_fails_results = conf.get('compiles', {}).get('fails', {})
        compiler.compiles = lambda snippet, name, **kwargs: (name not in compiles_fails_results, True)

        links_fails_results = conf.get('links', {}).get('fails', {})
        compiler.links = lambda snippet, name, **kwargs: (name not in links_fails_results, True)

        check_header_fails_results = conf.get('check_header', {}).get('fails', {})
        compiler.check_header = lambda header, *args, **kwargs: (header not in check_header_fails_results, True)

        has_header_symbol_fails_results = conf.get('has_header_symbol', {}).get('fails', {})
        compiler.has_header_symbol = _get_has_header_symbol_func(has_header_symbol_fails_results)

        has_function_fails_results = conf.get('has_function', {}).get('fails', {})
        compiler.has_function = lambda func, *args, **kwargs: (func not in has_function_fails_results, True)

        has_member_fails_results = conf.get('has_member', {}).get('fails', {})
        compiler.has_member = _get_has_member_func(has_member_fails_results)

        supported_args_fails = conf.get('supported_arguments', {}).get('args', [])
        compiler.get_supported_arguments = lambda args: [a for a in args if a not in supported_args_fails]

        supported_link_args_fails = conf.get('supported_link_arguments', {}).get('args', [])
        compiler.get_supported_link_arguments = lambda args: [a for a in args if a not in supported_link_args_fails]

        has_function_attribute_fails_results = conf.get('has_function_attribute', {}).get('fails', {})
        compiler.has_function_attribute = lambda attribute: attribute not in has_function_attribute_fails_results

        get_supported_function_attributes_fails_results = conf.get('has_function_attribute', {}).get('fails', {})
        compiler.get_supported_function_attributes = lambda attributes: [
            a for a in attributes if a not in get_supported_function_attributes_fails_results]

        compiler.compile = _emulated_compile
        if isinstance(compiler, RustCompiler):
            compiler.native_static_libs = []

        return compiler
