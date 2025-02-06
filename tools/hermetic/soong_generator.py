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
    'soong': 'Android.bp',
}

class SoongCustomTarget(HermeticCustomTarget):
    def __init__(self, target: HermeticCustomTarget):
        self.__dict__.update(target.__dict__)
        self.cmd = self._get_cmd()

    def _get_cmd(self) -> str:
        final_cmd = []
        for p in self.cmd_parts:
            if p == 'gen_dir':
                final_cmd.append('$(genDir)')
            elif isinstance(p, str):
                final_cmd.append(p)
            elif isinstance(p, tuple):
                if p[0] == 'location':
                    final_cmd.append(f'$(location {p[1]})')
                elif p[0] == 'input':
                    final_cmd.append(f'$(location {p[2]})')
                elif p[0] == 'output':
                    final_cmd.append(f'$(location {p[2]})')
                elif p[0] == 'placeholder':
                    if p[1].endswith('.h'):
                        final_cmd.append(f'"$(genDir)/{p[1]}"')
                    else:
                        final_cmd.append('"$(genDir)/placeholder.c"')
        return ' '.join(final_cmd)

class SoongGenerator(Generator):
    def __init__(self, output_dir: str, hermetic_state: HermeticState, config: 'HermeticConfig'):
        super().__init__('soong', output_dir, hermetic_state, config)

    def generate(self):
        copyright_template = self.jinja_env.get_template('copyright.tmpl')
        copyright_string = copyright_template.render(**self.config.copyright)

        hermetic_targets = {}
        for t in self.hermetic_state.static_libraries:
            hermetic_targets.setdefault(t.subdir, []).append(t)
        for t in self.hermetic_state.shared_libraries:
            hermetic_targets.setdefault(t.subdir, []).append(t)
        for t in self.hermetic_state.custom_targets:
            if t.generated_headers and t.generated_sources:
                # Split into two targets
                header_target = SoongCustomTarget(t)
                header_target.name = f'{t.name}_header'
                header_target.out = t.generated_headers
                
                # Modify cmd_parts for header_target
                new_cmd_parts = []
                for part in t.cmd_parts:
                    if isinstance(part, tuple) and part[0] == 'output':
                        # part is ('output', index, filename)
                        if part[2] in t.generated_sources:
                            # This is a source output, redirect it
                            new_cmd_parts.append(('placeholder', part[2]))
                        else:
                            new_cmd_parts.append(part)
                    else:
                        new_cmd_parts.append(part)
                header_target.cmd_parts = new_cmd_parts
                header_target.cmd = header_target._get_cmd()

                source_target = SoongCustomTarget(t)
                source_target.name = f'{t.name}_impl'
                source_target.out = t.generated_sources

                # Modify cmd_parts for source_target
                new_cmd_parts = []
                for part in t.cmd_parts:
                    if isinstance(part, tuple) and part[0] == 'output':
                        if part[2] in t.generated_headers:
                            # This is a header output, redirect it
                            new_cmd_parts.append(('placeholder', part[2]))
                        else:
                            new_cmd_parts.append(part)
                    else:
                        new_cmd_parts.append(part)
                source_target.cmd_parts = new_cmd_parts
                source_target.cmd = source_target._get_cmd()

                hermetic_targets.setdefault(t.subdir, []).append(header_target)
                hermetic_targets.setdefault(t.subdir, []).append(source_target)
            else:
                hermetic_targets.setdefault(t.subdir, []).append(SoongCustomTarget(t))
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
            if subdir in self.hermetic_state.filegroups:
                template = self.jinja_env.get_template('filegroup.txt')
                for fg in self.hermetic_state.filegroups[subdir]:
                    content += template.render(fg=fg)

            for i, target in enumerate(targets):
                if isinstance(target, HermeticIncludeDirectory):
                    template_name = 'include.txt'
                elif isinstance(target, HermeticFlag):
                    template_name = 'flag.txt'
                elif isinstance(target, HermeticStaticLibrary) and target.rust_abi != RustABI.NONE:
                    if target.rust_abi == RustABI.RUST:
                        template_name = 'rustlibrary.txt'
                    else:
                        template_name = 'rustffi.txt'
                else:
                    template_name = f'{type(target).__name__.replace("Hermetic", "").replace("Soong", "").lower()}.txt'
                try:
                    template = self.jinja_env.get_template(template_name)
                    content += template.render(target=target, state=self.hermetic_state)
                except Exception:
                    print(f'Could not find template for {template_name}')

            self.write_build_file(subdir, build_file_name, content)
