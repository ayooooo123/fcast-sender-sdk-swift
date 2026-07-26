import argparse
import os
import secrets
import stat
import sys
from dataclasses import dataclass
from pathlib import Path


class OutputDirectoryError(ValueError):
    """Raised when a requested build output cannot be handled safely."""


@dataclass(frozen=True)
class PreparedOutput:
    final_name: str
    staging_name: str
    build_device: int
    build_inode: int
    staging_device: int
    staging_inode: int
    final_device: int
    final_inode: int

    def as_tsv(self):
        return "\t".join(
            str(value)
            for value in (
                self.final_name,
                self.staging_name,
                self.build_device,
                self.build_inode,
                self.staging_device,
                self.staging_inode,
                self.final_device,
                self.final_inode,
            )
        )


def _validate_requested(requested):
    if type(requested) is not str:
        raise OutputDirectoryError("output path must be text")
    if any(ord(character) < 32 or ord(character) == 127 for character in requested):
        raise OutputDirectoryError("output path must not contain control characters")
    components = requested.split("/")
    if len(components) != 2 or components[0] != ".build":
        raise OutputDirectoryError(
            "output must be exactly one direct .build/<name> child"
        )
    name = components[1]
    if name in ("", ".", ".."):
        raise OutputDirectoryError("output name must not be empty, . or ..")
    allowed = set(
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789._-"
    )
    if any(character not in allowed for character in name):
        raise OutputDirectoryError(
            "output name may contain only ASCII letters, digits, ., _ and -"
        )
    return name


def _directory_flags():
    return os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)


def _open_build_root(root, *, create):
    root_path = Path(root)
    try:
        root_fd = os.open(root_path, _directory_flags())
    except OSError as error:
        raise OutputDirectoryError(
            f"unable to safely open repository root: {error}"
        ) from error
    try:
        if create:
            try:
                os.mkdir(".build", mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
        try:
            build_fd = os.open(".build", _directory_flags(), dir_fd=root_fd)
        except OSError as error:
            raise OutputDirectoryError(
                f"repo .build must be a real non-symlink directory: {error}"
            ) from error
    finally:
        os.close(root_fd)
    return build_fd


def _lstat_at(directory_fd, name):
    try:
        return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise OutputDirectoryError(
            f"unable to inspect output component {name}: {error}"
        ) from error


def _require_same_directory(build_fd, prepared):
    build_stat = os.fstat(build_fd)
    if (
        build_stat.st_dev != prepared.build_device
        or build_stat.st_ino != prepared.build_inode
    ):
        raise OutputDirectoryError(
            "repo .build changed after output preparation"
        )


def _unique_name(build_fd, prefix, *, create):
    for _ in range(128):
        name = prefix + secrets.token_hex(12)
        try:
            if create:
                os.mkdir(name, mode=0o700, dir_fd=build_fd)
            elif _lstat_at(build_fd, name) is not None:
                continue
            return name
        except FileExistsError:
            continue
    raise OutputDirectoryError("unable to allocate private output name")


def prepare_output(root, requested):
    final_name = _validate_requested(requested)
    build_fd = _open_build_root(root, create=True)
    try:
        existing = _lstat_at(build_fd, final_name)
        if existing is not None:
            if stat.S_ISLNK(existing.st_mode):
                raise OutputDirectoryError(
                    "existing output target must not be a symlink"
                )
            if not stat.S_ISDIR(existing.st_mode):
                raise OutputDirectoryError(
                    "existing output target must be a real directory"
                )
        staging_name = _unique_name(
            build_fd,
            f".{final_name}.staging-",
            create=True,
        )
        build_stat = os.fstat(build_fd)
        staging_stat = _lstat_at(build_fd, staging_name)
        return PreparedOutput(
            final_name=final_name,
            staging_name=staging_name,
            build_device=build_stat.st_dev,
            build_inode=build_stat.st_ino,
            staging_device=staging_stat.st_dev,
            staging_inode=staging_stat.st_ino,
            final_device=-1 if existing is None else existing.st_dev,
            final_inode=-1 if existing is None else existing.st_ino,
        )
    finally:
        os.close(build_fd)


def _remove_tree_at(parent_fd, name):
    entry_stat = _lstat_at(parent_fd, name)
    if entry_stat is None:
        return
    if not stat.S_ISDIR(entry_stat.st_mode) or stat.S_ISLNK(entry_stat.st_mode):
        os.unlink(name, dir_fd=parent_fd)
        return
    child_fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
    try:
        for child_name in os.listdir(child_fd):
            _remove_tree_at(child_fd, child_name)
    finally:
        os.close(child_fd)
    os.rmdir(name, dir_fd=parent_fd)


def _require_staging(build_fd, prepared):
    staging_stat = _lstat_at(build_fd, prepared.staging_name)
    if (
        staging_stat is None
        or not stat.S_ISDIR(staging_stat.st_mode)
        or stat.S_ISLNK(staging_stat.st_mode)
        or staging_stat.st_dev != prepared.staging_device
        or staging_stat.st_ino != prepared.staging_inode
    ):
        raise OutputDirectoryError(
            "private staging directory changed after preparation"
        )


def _require_final_unchanged(build_fd, prepared):
    existing = _lstat_at(build_fd, prepared.final_name)
    if prepared.final_device == -1 and prepared.final_inode == -1:
        if existing is not None:
            raise OutputDirectoryError(
                "publication target changed after output preparation"
            )
        return None
    if (
        existing is None
        or stat.S_ISLNK(existing.st_mode)
        or not stat.S_ISDIR(existing.st_mode)
        or existing.st_dev != prepared.final_device
        or existing.st_ino != prepared.final_inode
    ):
        raise OutputDirectoryError(
            "publication target changed after output preparation"
        )
    return existing


def _validate_prepared(prepared):
    if type(prepared) is not PreparedOutput:
        raise OutputDirectoryError("invalid prepared output token type")
    try:
        validated_final = _validate_requested(f".build/{prepared.final_name}")
    except OutputDirectoryError as error:
        raise OutputDirectoryError(
            f"invalid prepared output token: {error}"
        ) from error
    if validated_final != prepared.final_name:
        raise OutputDirectoryError("invalid prepared output final-name token")
    staging_prefix = f".{prepared.final_name}.staging-"
    staging_suffix = prepared.staging_name.removeprefix(staging_prefix)
    if (
        not prepared.staging_name.startswith(staging_prefix)
        or len(staging_suffix) != 24
        or any(character not in "0123456789abcdef" for character in staging_suffix)
    ):
        raise OutputDirectoryError("invalid prepared output staging-name token")
    required_nonnegative = (
        prepared.build_device,
        prepared.build_inode,
        prepared.staging_device,
        prepared.staging_inode,
    )
    if any(type(value) is not int or value < 0 for value in required_nonnegative):
        raise OutputDirectoryError("invalid prepared output inode token")
    final_absent = prepared.final_device == -1 and prepared.final_inode == -1
    final_present = (
        type(prepared.final_device) is int
        and type(prepared.final_inode) is int
        and prepared.final_device >= 0
        and prepared.final_inode >= 0
    )
    if not final_absent and not final_present:
        raise OutputDirectoryError("invalid prepared output final-inode token")


def publish_output(root, prepared):
    _validate_prepared(prepared)
    build_fd = _open_build_root(root, create=False)
    quarantine_name = None
    try:
        _require_same_directory(build_fd, prepared)
        _require_staging(build_fd, prepared)
        existing = _require_final_unchanged(build_fd, prepared)
        if existing is not None:
            quarantine_name = _unique_name(
                build_fd,
                f".{prepared.final_name}.quarantine-",
                create=False,
            )
            os.rename(
                prepared.final_name,
                quarantine_name,
                src_dir_fd=build_fd,
                dst_dir_fd=build_fd,
            )
        try:
            os.rename(
                prepared.staging_name,
                prepared.final_name,
                src_dir_fd=build_fd,
                dst_dir_fd=build_fd,
            )
        except BaseException:
            if quarantine_name is not None:
                os.rename(
                    quarantine_name,
                    prepared.final_name,
                    src_dir_fd=build_fd,
                    dst_dir_fd=build_fd,
                )
            raise
        if quarantine_name is not None:
            _remove_tree_at(build_fd, quarantine_name)
        os.fsync(build_fd)
    except OSError as error:
        raise OutputDirectoryError(f"unable to publish output safely: {error}") from error
    finally:
        os.close(build_fd)


def discard_output(root, prepared):
    _validate_prepared(prepared)
    build_fd = _open_build_root(root, create=False)
    try:
        _require_same_directory(build_fd, prepared)
        staging_stat = _lstat_at(build_fd, prepared.staging_name)
        if staging_stat is None:
            return
        _require_staging(build_fd, prepared)
        _remove_tree_at(build_fd, prepared.staging_name)
        os.fsync(build_fd)
    except OSError as error:
        raise OutputDirectoryError(f"unable to discard output safely: {error}") from error
    finally:
        os.close(build_fd)


def _prepared_from_args(arguments):
    return PreparedOutput(
        final_name=arguments.final_name,
        staging_name=arguments.staging_name,
        build_device=arguments.build_device,
        build_inode=arguments.build_inode,
        staging_device=arguments.staging_device,
        staging_inode=arguments.staging_inode,
        final_device=arguments.final_device,
        final_inode=arguments.final_inode,
    )


def _add_token_arguments(parser):
    parser.add_argument("--final-name", required=True)
    parser.add_argument("--staging-name", required=True)
    parser.add_argument("--build-device", required=True, type=int)
    parser.add_argument("--build-inode", required=True, type=int)
    parser.add_argument("--staging-device", required=True, type=int)
    parser.add_argument("--staging-inode", required=True, type=int)
    parser.add_argument("--final-device", required=True, type=int)
    parser.add_argument("--final-inode", required=True, type=int)


def _build_parser():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--root", required=True)
    prepare.add_argument("--requested", required=True)
    for name in ("publish", "discard"):
        command = commands.add_parser(name)
        command.add_argument("--root", required=True)
        _add_token_arguments(command)
    return parser


def main(argv=None):
    arguments = _build_parser().parse_args(argv)
    try:
        if arguments.command == "prepare":
            prepared = prepare_output(arguments.root, arguments.requested)
            print(prepared.as_tsv())
        elif arguments.command == "publish":
            publish_output(arguments.root, _prepared_from_args(arguments))
        else:
            discard_output(arguments.root, _prepared_from_args(arguments))
    except OutputDirectoryError as error:
        print(f"output directory error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
