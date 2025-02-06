#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 The Meson Development Team

import sys
from pathlib import Path
from tools.hermetic.hermetic_state import HermeticState
from tools.hermetic.virtual.virtual_project_info import VirtualProjectInfo
from tools.hermetic.generators.generator import Generator
from tools.hermetic.single_pass.single_pass_custom_target import SinglePassCustomTarget, SinglePassCmdPartType
from tools.hermetic.hermetic_state import (
    HermeticStaticLibrary,
    HermeticFlag,
    HermeticIncludeDirectory,
    HermeticFileGroup,
    HermeticPythonTarget,
)
from tools.hermetic.single_pass.single_pass_types import RustABI

BUILD_FILES = {
    'bazel': 'BUILD.bazel',
}

class BazelCustomTarget(SinglePassCustomTarget):
    def __init__(self, target: SinglePassCustomTarget):
        self.__dict__.update(target.__dict__)
        self.out = self.generated_headers + self.generated_sources
        self.tools = []
        for p in self.single_pass_cmds:
            if p.cmd_type == SinglePassCmdPartType.TOOL:
                self.tools.append(p.cmd)
            elif p.cmd_type == SinglePassCmdPartType.PYTHON_BINARY:
                self.tools.append(f':{p.cmd}')
        self.cmd = self._get_cmd()

    def _get_cmd(self) -> str:
        final_cmd = []
        for p in self.single_pass_cmds:
            if p.cmd_type == SinglePassCmdPartType.TOOL:
                final_cmd.append(f'$(location {p.cmd})')
            elif p.cmd_type == SinglePassCmdPartType.PYTHON_BINARY:
                final_cmd.append(f'$(location :{p.cmd})')
            elif p.cmd_type == SinglePassCmdPartType.INPUT:
                final_cmd.append(f'$(location {p.cmd})')
            elif p.cmd_type == SinglePassCmdPartType.OUTPUT:
                final_cmd.append(f'$(location {p.cmd})')
            elif p.cmd_type == SinglePassCmdPartType.STRING:
                processed_cmd = p.cmd.replace('@@GEN_DIR@@', '$(GENDIR)')
                final_cmd.append(processed_cmd)
        return ' '.join(final_cmd)

class BazelHermeticGenerator(Generator):
    def __init__(self, output_dir: str, hermetic_state: HermeticState, project_info: VirtualProjectInfo):
        super().__init__('bazel_hermetic', output_dir, hermetic_state, project_info)
        self.hermetic_state = hermetic_state

    def generate(self):
        copyright_template = self.jinja_env.get_template('copyright.tmpl')
        copyright_string = copyright_template.render(**self.project_info.copyright)

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
            custom_targets.append(BazelCustomTarget(t))

        for t in custom_targets:
            hermetic_targets.setdefault(t.subdir, []).append(t)
        for t in self.hermetic_state.python_targets.values():
            hermetic_targets.setdefault(t.subdir, []).append(t)
        for t in self.hermetic_state.include_directories.values():
            hermetic_targets.setdefault(t.subdir, []).append(t)
        for t in self.hermetic_state.flags.values():
            hermetic_targets.setdefault(t.subdir, []).append(t)

        for subdir, targets in hermetic_targets.items():
            build_file_name = BUILD_FILES.get('bazel')
            if not build_file_name:
                sys.exit(f'Build system bazel not supported.')

            targets.sort(key=lambda t: (
                0 if isinstance(t, HermeticFileGroup) else
                1 if isinstance(t, HermeticPythonTarget) else
                2 if isinstance(t, BazelCustomTarget) else
                3 if isinstance(t, HermeticIncludeDirectory) else
                4 if isinstance(t, HermeticFlag) else
                5, t.name
            ))

            content = copyright_string
            for i, target in enumerate(targets):
                if isinstance(target, HermeticStaticLibrary):
                    is_rust_target = False
                    for bucket in target.srcs.buckets.values():
                        for value in bucket.values():
                            if any(s.endswith(".rs") for s in value):
                                is_rust_target = True
                                break
                        if is_rust_target:
                            break
                    if is_rust_target:
                        template_name = 'rustlibrary.txt'
                    else:
                        template_name = 'staticlibrary.txt'
                else:
                    template_name = f'{type(target).__name__.replace("Hermetic", "").replace("Bazel", "").lower()}.txt'

                if isinstance(target, BazelCustomTarget):
                    template_name = "customtarget.txt"

                try:
                    template = self.jinja_env.get_template(template_name)
                    content += template.render(target=target, state=self.hermetic_state)
                except Exception as e:
                    print(f'Could not find template for {template_name}: {e}')

            self.write_build_file(subdir, build_file_name, content)
