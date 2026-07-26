import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from scripts.archive import ArchiveError, create_archive


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_SCRIPT = REPOSITORY_ROOT / "scripts" / "archive.py"
SOURCE_EPOCH = 1784900561
EXPECTED_ZIP_TIME = (2026, 7, 24, 13, 42, 40)


class ArchiveTests(unittest.TestCase):
    def make_framework(self, directory):
        framework = Path(directory) / "fcast_sender_sdk.xcframework"
        headers = framework / "ios-arm64" / "Headers"
        headers.mkdir(parents=True)
        (framework / "Info.plist").write_bytes(b"plist\n")
        (headers / "fcast_sender_sdkFFI.h").write_bytes(b"header\n")
        (headers / "module.modulemap").write_bytes(b"module\n")
        return framework

    def test_identical_trees_produce_identical_archives_and_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            framework = self.make_framework(directory)
            first = Path(directory) / "first.zip"
            second = Path(directory) / "second.zip"

            first_hash = create_archive(framework, first, SOURCE_EPOCH)
            os.chmod(framework / "Info.plist", 0o777)
            os.utime(framework / "Info.plist", (1, 1))
            second_hash = create_archive(framework, second, SOURCE_EPOCH)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first_hash, second_hash)
            self.assertRegex(first_hash, r"^[0-9a-f]{64}$")
            self.assertEqual(
                first_hash,
                hashlib.sha256(first.read_bytes()).hexdigest(),
            )

    def test_archive_entries_are_sorted_rooted_and_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            framework = self.make_framework(directory)
            archive = Path(directory) / "framework.zip"

            create_archive(framework, archive, SOURCE_EPOCH)

            with zipfile.ZipFile(archive) as opened:
                infos = opened.infolist()

            names = [info.filename for info in infos]
            self.assertEqual(names, sorted(names))
            self.assertEqual(names[0], "fcast_sender_sdk.xcframework/")
            self.assertTrue(
                all(
                    name.startswith("fcast_sender_sdk.xcframework/")
                    for name in names
                )
            )
            self.assertTrue(all(not name.startswith("/") for name in names))
            self.assertTrue(
                all(".." not in Path(name).parts for name in names)
            )
            for info in infos:
                with self.subTest(entry=info.filename):
                    self.assertEqual(info.date_time, EXPECTED_ZIP_TIME)
                    expected_mode = 0o755 if info.is_dir() else 0o644
                    self.assertEqual(
                        (info.external_attr >> 16) & 0o777,
                        expected_mode,
                    )
                    self.assertEqual(info.extra, b"")

    def test_rejects_wrong_root_name_and_lexical_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            wrong = Path(directory) / "Other.xcframework"
            wrong.mkdir()
            with self.assertRaisesRegex(ArchiveError, "source.*named"):
                create_archive(wrong, Path(directory) / "wrong.zip", SOURCE_EPOCH)

            framework = self.make_framework(directory)
            traversal = framework.parent / ".." / framework.parent.name / framework.name
            with self.assertRaisesRegex(ArchiveError, "source.*\\.\\."):
                create_archive(
                    traversal,
                    Path(directory) / "traversal.zip",
                    SOURCE_EPOCH,
                )

    def test_rejects_symlinks_and_non_regular_files(self):
        with tempfile.TemporaryDirectory() as directory:
            framework = self.make_framework(directory)
            (framework / "link").symlink_to("Info.plist")
            with self.assertRaisesRegex(ArchiveError, "symlink.*link"):
                create_archive(framework, Path(directory) / "link.zip", SOURCE_EPOCH)

        if hasattr(os, "mkfifo"):
            with tempfile.TemporaryDirectory() as directory:
                framework = self.make_framework(directory)
                os.mkfifo(framework / "pipe")
                with self.assertRaisesRegex(ArchiveError, "regular.*pipe"):
                    create_archive(
                        framework,
                        Path(directory) / "pipe.zip",
                        SOURCE_EPOCH,
                    )

    def test_rejects_host_metadata_and_appledouble(self):
        invalid_paths = (
            ".DS_Store",
            "__MACOSX",
            "._Info.plist",
            "nested/._metadata",
        )
        for invalid_path in invalid_paths:
            with self.subTest(path=invalid_path):
                with tempfile.TemporaryDirectory() as directory:
                    framework = self.make_framework(directory)
                    path = framework / invalid_path
                    if invalid_path == "__MACOSX":
                        path.mkdir()
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(b"metadata")
                    with self.assertRaisesRegex(
                        ArchiveError,
                        "host metadata",
                    ):
                        create_archive(
                            framework,
                            Path(directory) / "metadata.zip",
                            SOURCE_EPOCH,
                        )

    def test_zip_invocation_is_explicit_sorted_and_utc(self):
        with tempfile.TemporaryDirectory() as directory:
            framework = self.make_framework(directory)
            archive = Path(directory) / "framework.zip"

            with mock.patch(
                "scripts.archive.subprocess.run",
                wraps=subprocess.run,
            ) as run:
                create_archive(framework, archive, SOURCE_EPOCH)

            run.assert_called_once()
            positional, keywords = run.call_args
            arguments = positional[0]
            self.assertEqual(arguments[0:3], ["/usr/bin/zip", "-X", "-q"])
            self.assertEqual(arguments[-1], "-@")
            self.assertNotIn("shell", keywords)
            self.assertEqual(keywords["env"]["TZ"], "UTC")
            paths = keywords["input"].splitlines()
            self.assertEqual(paths, sorted(paths))
            self.assertTrue(
                all(path.startswith("fcast_sender_sdk.xcframework/") for path in paths)
            )

    def test_cli_is_independent_of_parent_timezone(self):
        with tempfile.TemporaryDirectory() as directory:
            framework = self.make_framework(directory)
            utc_archive = Path(directory) / "utc.zip"
            honolulu_archive = Path(directory) / "honolulu.zip"

            hashes = []
            for timezone, output in (
                ("UTC", utc_archive),
                ("Pacific/Honolulu", honolulu_archive),
            ):
                environment = os.environ.copy()
                environment["TZ"] = timezone
                result = subprocess.run(
                    [
                        sys.executable,
                        str(ARCHIVE_SCRIPT),
                        "--source",
                        str(framework),
                        "--output",
                        str(output),
                        "--source-epoch",
                        str(SOURCE_EPOCH),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                hashes.append(result.stdout.strip())

            self.assertEqual(utc_archive.read_bytes(), honolulu_archive.read_bytes())
            self.assertEqual(hashes[0], hashes[1])

    def test_rejects_invalid_source_epoch(self):
        with tempfile.TemporaryDirectory() as directory:
            framework = self.make_framework(directory)
            for epoch in (True, -1, 2**40):
                with self.subTest(epoch=epoch):
                    with self.assertRaisesRegex(ArchiveError, "source epoch"):
                        create_archive(
                            framework,
                            Path(directory) / "invalid.zip",
                            epoch,
                        )


if __name__ == "__main__":
    unittest.main()
