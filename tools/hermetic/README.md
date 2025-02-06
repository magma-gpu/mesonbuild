# meson2hermetic: Meson to Hermetic Build System Converter

The `meson2hermetic` tool facilitates the integration of Meson-based projects into hermetic build
systems like Bazel, Soong, and potentially Buck2. It works by interpreting a Meson project and
generating the necessary build files for the target hermetic system.

## How it Works

The tool invokes Meson's interpreter to understand the project's structure, dependencies, and build
targets. It then uses this information, combined with a user-provided configuration file, to render
a series of build files from Jinja2 templates. These generated files can then be used by the target
hermetic build system.

## Usage

To use the tool, run the `meson2hermetic.py` script. You must provide a path to a configuration file
using the `--config` argument, and a path to a toolchain configuration file using the `--toolchain`
argument.

By default, the script will use the current directory as the project directory. You can specify a
different project directory using the `--project-dir` option.

The `--output-dir` option controls where the generated build files are placed.

- If `--output-dir` is provided and is different from the project directory, the script will create
  a new directory at that path. The generated build files will be placed within this new directory,
  mirroring the subdirectory structure of the original project.
- If `--output-dir` is not provided, or if it is the same as the project directory, the generated
  build files will be placed directly within the project's subdirectories.

For now, since it's likely an user as installed meson on their system, `PYTHONPATH` must specify the
path to meson checkout.

**Example:**

```bash
PYTHONPATH=/path/to/meson python3 /path/to/meson/tools/hermetic/meson2hermetic.py \
    --config=/path/to/your/config.toml \
    --toolchain=/path/to/your/toolchain.toml \
    --project-dir=/path/to/your/project \
    --output-dir=/path/to/your/output
```

This will generate the hermetic build files in the appropriate subdirectories of
`/path/to/your/output`. For example, if you are targeting Soong, it will create `Android.bp` files.

## Configuration

The `meson2hermetic` tool uses TOML files for configuration. The main configuration file specifies
the target build system, project name, machine configurations, and any Meson options. The toolchain
configuration is specified in a separate file and passed to the script via the `--toolchain`
argument.

**Example `config.toml`:**

```toml
# The target hermetic build system.
build = 'soong'

[project_config]
# The name of the project.
name = 'my_project'

# Machine configuration for the host.
# See Meson's documentation for more details on machine configurations.
[project_config.host_machine]
cpu = 'aarch64'
cpu_family = 'aarch64'
system = 'android'
endian = 'little'

# Optional: Machine configuration for the build machine.
# If empty, the script will default to the native machine specs.
[project_config.build_machine]
# cpu = 'x86_64'
# cpu_family = 'x86_64'
# system = 'linux'
# endian = 'little'

# Meson options to be passed to the interpreter.
[project_config.meson_options]
vulkan-drivers = "freedreno,gfxstream"
gallium-drivers = ""
opengl = false
```

### Configuration Options:

- `build`: (Required) The name of the target hermetic build system (e.g., `'soong'`).
- `[project_config]`: A table containing the main project configuration.
  - `name`: The name of your project.
- `[project_config.host_machine]`: A table defining the host machine.
- `[project_config.build_machine]`: An optional table defining the build machine.
- `[project_config.target_machine]`: An optional table defining the target machine.
- `[project_config.meson_options]`: A table of Meson project options to be set during
  interpretation.

## Development Status

This tool is currently under active development. While it is functional for many use cases, it may
not support all features of Meson or all target build systems. Contributions and feedback are
welcome.
