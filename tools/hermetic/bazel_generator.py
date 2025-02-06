#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 The Meson Authors

import typing as T
from tools.hermetic.hermetic_types import (
    Generator,
    HermeticConfig,
    HermeticCustomTarget,
    HermeticFlag,
    HermeticIncludeDirectory,
    HermeticPythonTarget,
    HermeticStaticLibrary,
    HermeticState,
    RustABI,
)

BUILD_FILES = {
    'bazel': 'BUILD.bazel',
}

class BazelCustomTarget(HermeticCustomTarget):
    def __init__(self, target: HermeticCustomTarget):
        self.__dict__.update(target.__dict__)
        self.cmd = self._get_cmd()

    def _get_cmd(self) -> str:
        final_cmd = []
        for p in self.cmd_parts:
            if isinstance(p, str):
                final_cmd.append(p)
            elif isinstance(p, tuple):
                if p[0] == 'location':
                    if p[2]:
                        final_cmd.append(f'$(location {p[1]})')
                    else:
                        final_cmd.append(f'$(location :{p[1]})')
                elif p[0] == 'input':
                    final_cmd.append(f'$(location {p[2]})')
                elif p[0] == 'output':
                    final_cmd.append(f'$(location {p[2]})')
            elif p == 'gen_dir':
                final_cmd.append('$(GENDIR)')
        return ' '.join(final_cmd)

class BazelGenerator(Generator):
    def __init__(self, output_dir: str, hermetic_state: HermeticState, config: 'HermeticConfig'):
        super().__init__('bazel', output_dir, hermetic_state, config)

    def generate(self):
        copyright_template = self.jinja_env.get_template('copyright.tmpl')
        copyright_string = copyright_template.render(**self.config.copyright)

        hermetic_targets = {}
        for t in self.hermetic_state.static_libraries:
            hermetic_targets.setdefault(t.subdir, []).append(t)
        for t in self.hermetic_state.shared_libraries:
            hermetic_targets.setdefault(t.subdir, []).append(t)
        for t in self.hermetic_state.custom_targets:
            hermetic_targets.setdefault(t.subdir, []).append(BazelCustomTarget(t))
        for t in self.hermetic_state.python_targets:
            hermetic_targets.setdefault(t.subdir, []).append(t)
        for t in self.hermetic_state.include_directories:
            hermetic_targets.setdefault(t.subdir, []).append(t)
        for t in self.hermetic_state.flags.values():
            hermetic_targets.setdefault(t.subdir, []).append(t)

        for subdir, targets in hermetic_targets.items():
            build_file_name = BUILD_FILES.get(self.build_system)
            if not build_file_name:
                exit(f'Build system {self.build_system} not supported.')

            targets.sort(key=lambda t: (
                0 if isinstance(t, HermeticPythonTarget) else
                1 if isinstance(t, HermeticCustomTarget) else
                2 if isinstance(t, HermeticIncludeDirectory) else
                3 if isinstance(t, HermeticFlag) else
                4, t.name
            ))

            content = copyright_string
            for i, target in enumerate(targets):
                if isinstance(target, HermeticIncludeDirectory):
                    template_name = 'include.txt'
                elif isinstance(target, HermeticFlag):
                    template_name = 'flag.txt'
                elif isinstance(target, HermeticStaticLibrary) and target.rust_abi != RustABI.NONE:
                    if target.rust_abi == RustABI.RUST:
                        template_name = 'rustlibrary.txt'
                    else:
                        # TODO: Add rust_ffi support to Bazel
                        template_name = 'rustlibrary.txt'
                else:
                    template_name = f'{type(target).__name__.replace("Hermetic", "").replace("Bazel", "").lower()}.txt'
                try:
                    template = self.jinja_env.get_template(template_name)
                    content += template.render(target=target, state=self.hermetic_state)
                except Exception:
                    print(f'Could not find template for {template_name}')
 
            self.write_build_file(subdir, build_file_name, content)
