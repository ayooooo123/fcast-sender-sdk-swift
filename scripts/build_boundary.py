import argparse
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


class BuildBoundaryError(ValueError):
    """Raised when ambient state could alter compiler output."""


def validate_ambient_environment(environment):
    for name in sorted(environment):
        if (
            name in EXACT_FORBIDDEN
            or any(name.startswith(prefix) for prefix in FORBIDDEN_PREFIXES)
            or (
                name.startswith("CARGO_")
                and name not in ALLOWED_CARGO_TRANSPORT
            )
            or name.startswith("RUSTUP_")
        ):
            raise BuildBoundaryError(
                f"compiler-affecting environment variable is forbidden: {name}"
            )


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check-environment",))
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "check-environment":
            validate_ambient_environment(os.environ)
    except BuildBoundaryError as error:
        print(f"build boundary error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
