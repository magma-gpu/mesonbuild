#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 The Meson Development Team

from __future__ import annotations
import typing as T
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

if T.TYPE_CHECKING:
    from tools.hermetic.single_pass.single_pass_types import SinglePassState
    from tools.hermetic.virtual.virtual_project_info import VirtualProjectInfo


class Generator:
    def __init__(self, build_system: str, output_dir: str, single_pass_state: SinglePassState, project_info: VirtualProjectInfo):
        self.build_system = build_system
        self.output_dir = output_dir
        self.single_pass_state = single_pass_state
        self.project_info = project_info
        self.jinja_env = Environment(keep_trailing_newline=True, extensions=['jinja2.ext.do'])
        self.jinja_env.loader = FileSystemLoader(Path(__file__).parent.resolve() / f'templates/{build_system}')
        self.jinja_env.globals.update(hasattr=hasattr, getattr=getattr)

    def generate(self):
        raise NotImplementedError

    def write_build_file(self, subdir: str, build_file_name: str, content: str):
        output_path = Path(self.output_dir) / subdir
        output_path.mkdir(parents=True, exist_ok=True)
        (output_path / build_file_name).write_text(content, encoding='utf-8')
