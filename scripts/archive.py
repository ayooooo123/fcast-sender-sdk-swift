import argparse
import hashlib
import os
import stat
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path


XCFRAMEWORK_NAME = "fcast_sender_sdk.xcframework"
ZIP_EXECUTABLE = "/usr/bin/zip"


class ArchiveError(ValueError):
    """Raised when an XCFramework cannot be archived deterministically."""


def create_archive(source: Path, output: Path, source_epoch: int) -> str:
    """Write a normalized ZIP and return its lowercase SHA-256."""
    source_path = Path(source)
    output_path = Path(output)
    _validate_source_path(source_path)
    _validate_source_epoch(source_epoch)
    zip_epoch = source_epoch - (source_epoch % 2)

    entries = _collect_entries(source_path)
    if not output_path.parent.is_dir():
        raise ArchiveError(
            f"archive output parent does not exist: {output_path.parent}"
        )

    with tempfile.TemporaryDirectory(prefix="fcast-archive.") as directory:
        workspace = Path(directory)
        staging = workspace / "staging"
        staged_root = staging / XCFRAMEWORK_NAME
        staged_root.mkdir(parents=True, mode=0o755)

        for relative, kind in entries:
            source_entry = source_path / relative
            staged_entry = staged_root / relative
            if kind == "directory":
                staged_entry.mkdir(mode=0o755)
            else:
                _copy_regular_file(source_entry, staged_entry, relative)
                os.chmod(staged_entry, 0o644)

        normalized_paths = [staged_root]
        normalized_paths.extend(staged_root / relative for relative, _ in entries)
        for path in reversed(normalized_paths):
            mode = 0o755 if path.is_dir() else 0o644
            os.chmod(path, mode)
            os.utime(path, (zip_epoch, zip_epoch))

        archive_entries = [f"{XCFRAMEWORK_NAME}/"]
        archive_entries.extend(
            f"{XCFRAMEWORK_NAME}/{relative.as_posix()}"
            + ("/" if kind == "directory" else "")
            for relative, kind in entries
        )
        archive_entries.sort()

        temporary_archive = workspace / "archive.zip"
        environment = {
            "TZ": "UTC",
            "LC_ALL": "C",
            "LANG": "C",
        }
        try:
            subprocess.run(
                [
                    ZIP_EXECUTABLE,
                    "-X",
                    "-q",
                    str(temporary_archive),
                    "-@",
                ],
                cwd=staging,
                env=environment,
                input="\n".join(archive_entries) + "\n",
                text=True,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            detail = getattr(error, "stderr", None) or str(error)
            raise ArchiveError(f"unable to create normalized ZIP: {detail}") from error

        _verify_archive_entries(temporary_archive, archive_entries)

        try:
            os.replace(temporary_archive, output_path)
        except OSError as error:
            raise ArchiveError(
                f"unable to write archive {output_path}: {error}"
            ) from error

    return _sha256_file(output_path)


def _validate_source_path(source):
    if ".." in source.parts:
        raise ArchiveError("source path must not contain .. traversal")
    try:
        source_stat = source.lstat()
    except OSError as error:
        raise ArchiveError(f"unable to inspect source {source}: {error}") from error
    if stat.S_ISLNK(source_stat.st_mode):
        raise ArchiveError("source must not be a symlink")
    if not stat.S_ISDIR(source_stat.st_mode):
        raise ArchiveError("source must be an XCFramework directory")
    if source.name != XCFRAMEWORK_NAME:
        raise ArchiveError(
            f"source directory must be named exactly {XCFRAMEWORK_NAME}"
        )


def _validate_source_epoch(source_epoch):
    if type(source_epoch) is not int or source_epoch < 0:
        raise ArchiveError("source epoch must be a non-negative integer")
    try:
        timestamp = datetime.fromtimestamp(source_epoch, timezone.utc)
    except (OverflowError, OSError, ValueError) as error:
        raise ArchiveError(f"source epoch is outside the ZIP range: {error}") from error
    if timestamp.year < 1980 or timestamp.year > 2107:
        raise ArchiveError(
            "source epoch must produce a timestamp from 1980 through 2107"
        )


def _collect_entries(source):
    collected = []

    def visit(directory, relative_directory):
        try:
            with os.scandir(directory) as iterator:
                children = sorted(iterator, key=lambda entry: entry.name)
        except OSError as error:
            raise ArchiveError(
                f"unable to inspect archive source {directory}: {error}"
            ) from error
        for child in children:
            relative = relative_directory / child.name
            _reject_control_characters(relative)
            _reject_host_metadata(relative)
            try:
                child_stat = child.stat(follow_symlinks=False)
            except OSError as error:
                raise ArchiveError(
                    f"unable to inspect archive entry {relative}: {error}"
                ) from error
            if stat.S_ISLNK(child_stat.st_mode):
                raise ArchiveError(
                    f"archive source must not contain symlink: {relative}"
                )
            if stat.S_ISDIR(child_stat.st_mode):
                collected.append((relative, "directory"))
                visit(Path(child.path), relative)
            elif stat.S_ISREG(child_stat.st_mode):
                collected.append((relative, "file"))
            else:
                raise ArchiveError(
                    "archive source entries must be regular files or "
                    f"directories: {relative}"
                )

    visit(source, Path())
    collected.sort(key=lambda entry: entry[0].as_posix())
    return collected


def _reject_control_characters(relative):
    if any(
        "\n" in component or "\r" in component
        for component in relative.parts
    ):
        raise ArchiveError(
            "archive path components must not contain a newline or "
            f"carriage return: {relative!r}"
        )


def _reject_host_metadata(relative):
    for part in relative.parts:
        if part in {".DS_Store", "__MACOSX"} or part.startswith("._"):
            raise ArchiveError(
                f"archive source contains forbidden host metadata: {relative}"
            )


def _copy_regular_file(source, destination, relative):
    source_flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        source_flags |= os.O_CLOEXEC

    destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        destination_flags |= os.O_CLOEXEC

    source_fd = None
    destination_fd = None
    try:
        source_fd = os.open(source, source_flags)
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode):
            raise ArchiveError(
                f"archive source entry is no longer a regular file: {relative}"
            )
        destination_fd = os.open(destination, destination_flags, 0o644)
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            remaining = memoryview(chunk)
            while remaining:
                written = os.write(destination_fd, remaining)
                if written == 0:
                    raise OSError("zero-byte write while staging archive")
                remaining = remaining[written:]
    except OSError as error:
        raise ArchiveError(
            f"unable to copy regular file {relative}: {error}"
        ) from error
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        if source_fd is not None:
            os.close(source_fd)


def _verify_archive_entries(archive, submitted_entries):
    try:
        with zipfile.ZipFile(archive) as opened:
            actual_entries = opened.namelist()
    except (OSError, zipfile.BadZipFile) as error:
        raise ArchiveError(
            f"unable to verify normalized ZIP entries: {error}"
        ) from error
    if actual_entries != submitted_entries:
        raise ArchiveError(
            "normalized ZIP entries do not exactly match the submitted "
            "sorted entry list"
        )


def _sha256_file(path):
    digest = hashlib.sha256()
    try:
        with path.open("rb") as archive:
            for chunk in iter(lambda: archive.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ArchiveError(f"unable to hash archive {path}: {error}") from error
    return digest.hexdigest()


def _parse_arguments():
    parser = argparse.ArgumentParser(
        description="Create a deterministic FCast sender SDK XCFramework ZIP."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-epoch", type=int, required=True)
    return parser.parse_args()


def main():
    arguments = _parse_arguments()
    try:
        checksum = create_archive(
            arguments.source,
            arguments.output,
            arguments.source_epoch,
        )
    except ArchiveError as error:
        raise SystemExit(f"archive error: {error}") from error
    print(checksum)


if __name__ == "__main__":
    main()
