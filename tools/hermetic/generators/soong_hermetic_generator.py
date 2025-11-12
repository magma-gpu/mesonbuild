#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 The Meson Development Team

import sys
import datetime
from pathlib import Path
from tools.hermetic.hermetic_state import HermeticState
from tools.hermetic.virtual.virtual_project_info import VirtualProjectInfo
from tools.hermetic.generators.generator import Generator
from tools.hermetic.generators.jinja_hermetic_helpers import hermetic_textwrap
from tools.hermetic.single_pass.single_pass_custom_target import SinglePassCustomTarget, SinglePassCmdPart, SinglePassCmdPartType
from tools.hermetic.hermetic_state import (
    HermeticStaticLibrary,
    HermeticSharedLibrary,
    HermeticFlag,
    HermeticIncludeDirectory,
    HermeticFileGroup,
    HermeticPythonTarget,
)

BUILD_FILES = {
    'soong': 'Android.bp',
}

class SoongCustomTarget(SinglePassCustomTarget):
    def __init__(self, target: SinglePassCustomTarget):
        self.__dict__.update(target.__dict__)
        self.out = self.generated_headers + self.generated_sources
        self.tools = []
        for p in self.single_pass_cmds:
            if p.cmd_type == SinglePassCmdPartType.TOOL:
                self.tools.append(p.cmd)
            elif p.cmd_type == SinglePassCmdPartType.PYTHON_BINARY:
                self.tools.append(p.cmd)
        self.cmd = self._get_cmd()

    def _get_cmd(self) -> str:
        final_cmd = []
        for p in self.single_pass_cmds:
            if isinstance(p, SinglePassCmdPart):
                if p.cmd_type == SinglePassCmdPartType.TOOL:
                    final_cmd.append(f'$(location {p.cmd})')
                elif p.cmd_type == SinglePassCmdPartType.PYTHON_BINARY:
                    final_cmd.append(f'$(location {p.cmd})')
                elif p.cmd_type == SinglePassCmdPartType.INPUT:
                    final_cmd.append(f'$(location {p.cmd})')
                elif p.cmd_type == SinglePassCmdPartType.OUTPUT:
                    final_cmd.append(f'$(location {p.cmd})')
                elif p.cmd_type == SinglePassCmdPartType.STRING:
                    processed_cmd = p.cmd.replace('@@GEN_DIR@@', '$(genDir)')
                    final_cmd.append(processed_cmd)
            elif isinstance(p, tuple):
                if p[0] == 'placeholder':
                    if p[1].endswith('.h'):
                        final_cmd.append(f'$(genDir)/{p[1]}')
                    else:
                        final_cmd.append('$(genDir)/placeholder.c')
        return ' '.join(final_cmd)

class SoongHermeticGenerator(Generator):
    def __init__(self, output_dir: str, hermetic_state: HermeticState, project_info: VirtualProjectInfo):
        super().__init__('soong_hermetic', output_dir, hermetic_state, project_info)
        self.hermetic_state = hermetic_state
        self.jinja_env.filters['hermetic_textwrap'] = hermetic_textwrap

    def generate(self):
        self.project_info.copyright.update({'year': datetime.date.today().year})
        copyright_template = self.jinja_env.get_template('copyright.tmpl')
        copyright_string = copyright_template.render(**self.project_info.copyright)

        license_template = self.jinja_env.get_template('license.tmpl')
        license_string = license_template.render(**self.project_info.copyright)

        hermetic_targets = {}
        for t in self.hermetic_state.file_groups.values():
            hermetic_targets.setdefault(t.subdir, []).append(t)
        for t in self.hermetic_state.static_libraries.values():
            hermetic_targets.setdefault(t.subdir, []).append(t)
        for t in self.hermetic_state.shared_libraries.values():
            hermetic_targets.setdefault(t.subdir, []).append(t)

        custom_targets = []
        for name, targets in self.hermetic_state.custom_targets.items():
            if not targets:
                continue
            t = targets[0]
            if t.generated_headers and t.generated_sources:
                # Split into two targets
                header_target = SoongCustomTarget(t)
                header_target.name = f'{t.name}_header'
                header_target.out = t.generated_headers

                # Modify single_pass_cmds for header_target
                new_single_pass_cmds = []
                for part in t.single_pass_cmds:
                    if part.cmd_type == SinglePassCmdPartType.OUTPUT:
                        if part.cmd in t.generated_sources:
                            # This is a source output, redirect it
                            new_single_pass_cmds.append(('placeholder', part.cmd))
                        else:
                            new_single_pass_cmds.append(part)
                    else:
                        new_single_pass_cmds.append(part)
                header_target.single_pass_cmds = new_single_pass_cmds
                header_target.cmd = header_target._get_cmd()

                source_target = SoongCustomTarget(t)
                source_target.name = f'{t.name}_impl'
                source_target.out = t.generated_sources

                # Modify single_pass_cmds for source_target
                new_single_pass_cmds = []
                for part in t.single_pass_cmds:
                    if part.cmd_type == SinglePassCmdPartType.OUTPUT:
                        if part.cmd in t.generated_headers:
                            # This is a header output, redirect it
                            new_single_pass_cmds.append(('placeholder', part.cmd))
                        else:
                            new_single_pass_cmds.append(part)
                    else:
                        new_single_pass_cmds.append(part)
                source_target.single_pass_cmds = new_single_pass_cmds
                source_target.cmd = source_target._get_cmd()

                custom_targets.append(header_target)
                custom_targets.append(source_target)
            else:
                soong_target = SoongCustomTarget(t)
                custom_targets.append(soong_target)

        for t in custom_targets:
            hermetic_targets.setdefault(t.subdir, []).append(t)
        for t in self.hermetic_state.python_targets.values():
            hermetic_targets.setdefault(t.subdir, []).append(t)
        for t in self.hermetic_state.include_directories.values():
            hermetic_targets.setdefault(t.subdir, []).append(t)
        for t in self.hermetic_state.flags.values():
            hermetic_targets.setdefault(t.subdir, []).append(t)

        for subdir, targets in hermetic_targets.items():
            build_file_name = BUILD_FILES.get('soong')
            if not build_file_name:
                sys.exit(f'Build system soong not supported.')

            targets.sort(key=lambda t: (
                0 if isinstance(t, HermeticFileGroup) else
                1 if isinstance(t, HermeticPythonTarget) else
                2 if isinstance(t, SoongCustomTarget) else
                3 if isinstance(t, HermeticIncludeDirectory) else
                4 if isinstance(t, HermeticFlag) else
                5, t.name
            ))

            content = copyright_string
            if not subdir:
                content += '\n' + license_string
            for i, target in enumerate(targets):
                template_name = f'{type(target).__name__.replace('Hermetic', '').replace('Soong', '').lower()}.txt'
                if isinstance(target, SoongCustomTarget):
                    template_name = "customtarget.txt"
                try:
                    template = self.jinja_env.get_template(template_name)
                    content += template.render(target=target, state=self.hermetic_state)
                except Exception as e:
                    print(f'Could not find template for {template_name}: {e}')

            self.write_build_file(subdir, build_file_name, content)