import argparse
import json
import os
import sys


EXACT_FORBIDDEN = {
    "AR",
    "CC",
    "CFLAGS",
    "CPPFLAGS",
    "CPATH",
    "CXX",
    "CXXFLAGS",
    "CPLUS_INCLUDE_PATH",
    "C_INCLUDE_PATH",
    "DEVELOPER_DIR",
    "IPHONEOS_DEPLOYMENT_TARGET",
    "LD",
    "LDFLAGS",
    "LIBRARY_PATH",
    "MACOSX_DEPLOYMENT_TARGET",
    "OBJCFLAGS",
    "OBJC_INCLUDE_PATH",
    "RUSTC",
    "RUSTC_WORKSPACE_WRAPPER",
    "RUSTC_WRAPPER",
    "RUSTDOC",
    "RUSTDOCFLAGS",
    "RUSTFLAGS",
    "CARGO_BUILD_TARGET",
    "CARGO_ENCODED_RUSTFLAGS",
    "CARGO_INCREMENTAL",
    "CARGO_TARGET_DIR",
    "SDKROOT",
    "SWIFT_EXEC",
    "TOOLCHAINS",
}
FORBIDDEN_PREFIXES = (
    "CARGO_BUILD_",
    "CARGO_PROFILE_",
    "CARGO_TARGET_",
)
ALLOWED_CARGO_TRANSPORT = {
    "CARGO_HTTP_TIMEOUT",
    "CARGO_NET_GIT_FETCH_WITH_CLI",
    "CARGO_NET_RETRY",
}
PASSTHROUGH_NAMES = {
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "TMPDIR",
}
PASSTHROUGH_NETWORK_NAMES = {
    "ALL_PROXY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "all_proxy",
    "http_proxy",
    "https_proxy",
    "no_proxy",
}
INTERNAL_ENVIRONMENT = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "MEDIASTORM_SANITIZED_BUILD": "1",
}
CC_RS_NAMES = (
    "AR",
    "CC",
    "CFLAGS",
    "CPPFLAGS",
    "CXX",
    "CXXFLAGS",
    "LD",
    "LDFLAGS",
)


class BuildBoundaryError(ValueError):
    """Raised when ambient state could alter compiler output."""


def validate_ambient_environment(environment):
    for name in sorted(environment):
        uppercase = name.upper()
        compiler_family = any(
            uppercase == base
            or uppercase.startswith(f"{base}_")
            or uppercase.startswith(f"TARGET_{base}")
            or uppercase.startswith(f"HOST_{base}")
            for base in CC_RS_NAMES
        )
        native_dependency_family = (
            uppercase.startswith("CRATE_CC_")
            or uppercase.startswith("BINDGEN_EXTRA_CLANG_ARGS")
            or uppercase in {"CLANG_PATH", "LIBCLANG_PATH"}
            or uppercase.startswith("PKG_CONFIG")
            or "_PKG_CONFIG" in uppercase
        )
        if (
            name in EXACT_FORBIDDEN
            or any(name.startswith(prefix) for prefix in FORBIDDEN_PREFIXES)
            or (
                name.startswith("CARGO_")
                and name not in ALLOWED_CARGO_TRANSPORT
            )
            or name.startswith("RUSTUP_")
            or compiler_family
            or native_dependency_family
        ):
            raise BuildBoundaryError(
                f"compiler-affecting environment variable is forbidden: {name}"
            )


def sanitized_environment(environment):
    sanitized = {}
    allowed = (
        PASSTHROUGH_NAMES
        | PASSTHROUGH_NETWORK_NAMES
        | ALLOWED_CARGO_TRANSPORT
    )
    for name in sorted(allowed):
        value = environment.get(name)
        if value is not None:
            sanitized[name] = value
    sanitized.update(INTERNAL_ENVIRONMENT)
    return sanitized


def validate_sanitized_environment(environment):
    allowed = (
        PASSTHROUGH_NAMES
        | PASSTHROUGH_NETWORK_NAMES
        | ALLOWED_CARGO_TRANSPORT
        | set(INTERNAL_ENVIRONMENT)
        | {"PWD", "SHLVL", "_", "__CF_USER_TEXT_ENCODING"}
    )
    unexpected = set(environment) - allowed
    if unexpected:
        raise BuildBoundaryError(
            "sanitized build environment contains unexpected variables: "
            + ", ".join(sorted(unexpected))
        )
    for name, expected in INTERNAL_ENVIRONMENT.items():
        if environment.get(name) != expected:
            raise BuildBoundaryError(
                f"sanitized build environment has invalid {name}"
            )


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "check-environment",
            "check-sanitized-environment",
            "re-exec",
            "sanitized-environment",
        ),
    )
    parser.add_argument("--script")
    parser.add_argument("--argument")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "check-environment":
            validate_ambient_environment(os.environ)
        elif arguments.command == "check-sanitized-environment":
            validate_sanitized_environment(os.environ)
        elif arguments.command == "sanitized-environment":
            print(
                json.dumps(
                    sanitized_environment(os.environ),
                    sort_keys=True,
                )
            )
        else:
            if not arguments.script or arguments.argument is None:
                raise BuildBoundaryError(
                    "re-exec requires --script and --argument"
                )
            child_environment = sanitized_environment(os.environ)
            os.execve(
                arguments.script,
                [
                    arguments.script,
                    "--output",
                    arguments.argument,
                ],
                child_environment,
            )
    except BuildBoundaryError as error:
        print(f"build boundary error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
