# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 The Meson development team

import typing as T

from . import backends
from mesonbuild import build, interpreter

class HermeticBackend(backends.Backend):
    name = 'hermetic'

    def __init__(self, build: build.Build):
        super().__init__(build)

    def generate(self, capture: bool = False, vslite_ctx: T.Optional[T.Dict] = None) -> None:
        pass
