#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 The Meson Development Team

import textwrap

def hermetic_textwrap(value, width=80, wrapstring=None):
    """
    A custom textwrap filter that preserves trailing whitespace.
    """
    wrapper = textwrap.TextWrapper(
        width=width,
        break_long_words=False,
        break_on_hyphens=False,
        drop_whitespace=False
    )
    lines = wrapper.wrap(value)
    if wrapstring:
        return wrapstring.join(lines)
    return '\n'.join(lines)
