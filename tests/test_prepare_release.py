import copy
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.release_metadata as release_metadata
from scripts.release_metadata import (
    BuildEnvironment,
    ReleaseMetadataError,
    build_provenance,
    release_url,
    render_package,
)
from scripts.source_pin import SourcePin


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PREPARE_RELEASE = REPOSITORY_ROOT / "scripts" / "prepare-release.sh"
SOURCE_LOCK = REPOSITORY_ROOT / "source.lock.json"
ENVIRONMENT_LOCK = REPOSITORY_ROOT / "build-environment.lock.json"
VERSION = "0.0.8-mediastorm.1"
RELEASE_INPUT_COMMIT = "a" * 40
RELEASE_TAG_COMMIT = "b" * 40
CHECKSUM = "c" * 64


class ProvenancePreparationTests(unittest.TestCase):
    def setUp(self):
        self.source_pin = SourcePin.load(SOURCE_LOCK)
        self.environment = BuildEnvironment.load(ENVIRONMENT_LOCK)
        self.package_swift = render_package(VERSION, CHECKSUM)
        self.base = build_provenance(
            version=VERSION,
            distribution_commit=RELEASE_INPUT_COMMIT,
            source_pin=self.source_pin,
            environment=self.environment,
            archive_sha256=CHECKSUM,
            binary_target="fcast_sender_sdkFFI",
            product="FCastSenderSDK",
            artifact_url=release_url(VERSION),
            swiftpm_checksum=CHECKSUM,
        )

    def test_release_input_commit_is_distinct_from_release_tag_commit(self):
        self.assertEqual(
            self.base["distributionInputCommit"],
            RELEASE_INPUT_COMMIT,
        )
        self.assertNotIn("releaseTagCommit", self.base)

        augmented = release_metadata.augment_provenance(
            self.base,
            RELEASE_TAG_COMMIT,
        )

        self.assertEqual(augmented["releaseTagCommit"], RELEASE_TAG_COMMIT)
        self.assertEqual(
            augmented["distributionInputCommit"],
            RELEASE_INPUT_COMMIT,
        )
        self.assertNotEqual(
            augmented["distributionInputCommit"],
            augmented["releaseTagCommit"],
        )

    def test_asset_augmentation_is_deterministic_and_adds_only_tag_commit(self):
        first = release_metadata.augment_provenance(
            self.base,
            RELEASE_TAG_COMMIT,
        )
        second = release_metadata.augment_provenance(
            copy.deepcopy(self.base),
            RELEASE_TAG_COMMIT,
        )

        self.assertEqual(first, second)
        self.assertEqual(
            set(first) - set(self.base),
            {"releaseTagCommit"},
        )
        without_tag = dict(first)
        del without_tag["releaseTagCommit"]
        self.assertEqual(without_tag, self.base)
        self.assertNotIn("releaseTagCommit", self.base)

    def test_augmentation_requires_full_lowercase_commit_and_base_schema(self):
        for commit in (
            "a" * 39,
            "A" * 40,
            "main",
            RELEASE_TAG_COMMIT + "\n",
        ):
            with self.subTest(commit=commit):
                with self.assertRaisesRegex(
                    ReleaseMetadataError,
                    "release tag commit.*40 lowercase hexadecimal",
                ):
                    release_metadata.augment_provenance(self.base, commit)

        bad = copy.deepcopy(self.base)
        bad["unknown"] = True
        with self.assertRaisesRegex(ReleaseMetadataError, "unknown"):
            release_metadata.augment_provenance(bad, RELEASE_TAG_COMMIT)

    def test_augmentation_rejects_existing_release_tag_commit(self):
        tagged = dict(self.base)
        tagged["releaseTagCommit"] = RELEASE_TAG_COMMIT
        with self.assertRaisesRegex(ReleaseMetadataError, "already"):
            release_metadata.augment_provenance(tagged, RELEASE_TAG_COMMIT)

    def test_verify_provenance_accepts_exact_base_candidate(self):
        release_metadata.verify_provenance(
            self.base,
            package_swift=self.package_swift,
            source_pin=self.source_pin,
            release_input_commit=RELEASE_INPUT_COMMIT,
            release_tag_commit=None,
            archive=None,
        )

    def test_verify_provenance_accepts_exact_asset_candidate(self):
        tagged = release_metadata.augment_provenance(
            self.base,
            RELEASE_TAG_COMMIT,
        )
        release_metadata.verify_provenance(
            tagged,
            package_swift=self.package_swift,
            source_pin=self.source_pin,
            release_input_commit=RELEASE_INPUT_COMMIT,
            release_tag_commit=RELEASE_TAG_COMMIT,
            archive=None,
        )

    def test_verify_provenance_rejects_unknown_or_disagreeing_fields(self):
        cases = {
            "unknown": lambda value: value.update({"unknown": True}),
            "release input": lambda value: value.update(
                {"distributionInputCommit": "d" * 40}
            ),
            "source": lambda value: value["source"].update({"tag": "main"}),
            "checksum": lambda value: value["package"].update(
                {"swiftPMChecksum": "e" * 64}
            ),
            "mutable URL": lambda value: value["package"].update(
                {
                    "assetURL":
                        "https://gitlab.futo.org/jobs/123/artifacts/download"
                }
            ),
        }
        for name, mutation in cases.items():
            with self.subTest(name=name):
                changed = copy.deepcopy(self.base)
                mutation(changed)
                with self.assertRaises(ReleaseMetadataError):
                    release_metadata.verify_provenance(
                        changed,
                        package_swift=self.package_swift,
                        source_pin=self.source_pin,
                        release_input_commit=RELEASE_INPUT_COMMIT,
                        release_tag_commit=None,
                        archive=None,
                    )

    def test_verify_provenance_rejects_latest_branch_and_checksum_bypasses(self):
        bad_manifests = (
            self.package_swift.replace(
                release_url(VERSION),
                "https://example.invalid/releases/latest/download/sdk.zip",
            ),
            self.package_swift.replace(
                release_url(VERSION),
                "https://example.invalid/archive/refs/heads/main.zip",
            ),
            self.package_swift.replace(
                release_url(VERSION),
                "https://gitlab.futo.org/jobs/123/artifacts/download",
            ),
            self.package_swift.replace(
                f'let checksum = "{CHECKSUM}"',
                'let checksum = ""',
            ),
        )
        for manifest in bad_manifests:
            with self.subTest(manifest=manifest):
                with self.assertRaises(ReleaseMetadataError):
                    release_metadata.verify_provenance(
                        self.base,
                        package_swift=manifest,
                        source_pin=self.source_pin,
                        release_input_commit=RELEASE_INPUT_COMMIT,
                        release_tag_commit=None,
                        archive=None,
                    )

    def test_verify_provenance_requires_expected_tag_commit_presence(self):
        with self.assertRaisesRegex(ReleaseMetadataError, "releaseTagCommit"):
            release_metadata.verify_provenance(
                self.base,
                package_swift=self.package_swift,
                source_pin=self.source_pin,
                release_input_commit=RELEASE_INPUT_COMMIT,
                release_tag_commit=RELEASE_TAG_COMMIT,
                archive=None,
            )

        tagged = release_metadata.augment_provenance(
            self.base,
            RELEASE_TAG_COMMIT,
        )
        with self.assertRaisesRegex(ReleaseMetadataError, "releaseTagCommit"):
            release_metadata.verify_provenance(
                tagged,
                package_swift=self.package_swift,
                source_pin=self.source_pin,
                release_input_commit=RELEASE_INPUT_COMMIT,
                release_tag_commit=None,
                archive=None,
            )

    def test_archive_python_sha_and_swiftpm_checksum_must_both_match(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "artifact.zip"
            archive.write_bytes(b"candidate archive")
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            package = render_package(VERSION, checksum)
            provenance = build_provenance(
                version=VERSION,
                distribution_commit=RELEASE_INPUT_COMMIT,
                source_pin=self.source_pin,
                environment=self.environment,
                archive_sha256=checksum,
                binary_target="fcast_sender_sdkFFI",
                product="FCastSenderSDK",
                artifact_url=release_url(VERSION),
                swiftpm_checksum=checksum,
            )
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=checksum + "\n",
                stderr="",
            )
            with mock.patch(
                "scripts.release_metadata.subprocess.run",
                return_value=completed,
            ) as run:
                release_metadata.verify_provenance(
                    provenance,
                    package_swift=package,
                    source_pin=self.source_pin,
                    release_input_commit=RELEASE_INPUT_COMMIT,
                    release_tag_commit=None,
                    archive=archive,
                )
            run.assert_called_once()
            self.assertIn("compute-checksum", run.call_args.args[0])

            completed.stdout = "f" * 64 + "\n"
            with mock.patch(
                "scripts.release_metadata.subprocess.run",
                return_value=completed,
            ):
                with self.assertRaisesRegex(
                    ReleaseMetadataError,
                    "SwiftPM checksum",
                ):
                    release_metadata.verify_provenance(
                        provenance,
                        package_swift=package,
                        source_pin=self.source_pin,
                        release_input_commit=RELEASE_INPUT_COMMIT,
                        release_tag_commit=None,
                        archive=archive,
                    )


class PrepareReleaseScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = PREPARE_RELEASE.read_text(encoding="utf-8")

    def test_exact_dry_run_cli_and_first_version_are_required(self):
        self.assertIn("--version", self.text)
        self.assertIn("--input-commit", self.text)
        self.assertIn("--dry-run", self.text)
        self.assertIn("0.0.8-mediastorm.1", self.text)
        self.assertRegex(self.text, r"[0-9a-f]\{40\}|40")

    def test_release_input_must_be_clean_full_head(self):
        self.assertIn("git status --short --untracked-files=no", self.text)
        self.assertIn("git rev-parse HEAD", self.text)
        self.assertIn("RELEASE_INPUT_COMMIT", self.text)
        self.assertIn("refs/tags/", self.text)
        self.assertIn("git show", self.text)

    def test_runs_full_tests_shell_syntax_and_two_independent_clean_builds(self):
        self.assertIn("python3 -m unittest discover -s tests", self.text)
        self.assertIn("bash -n", self.text)
        self.assertIn(".build/release-build-1", self.text)
        self.assertIn(".build/release-build-2", self.text)
        self.assertEqual(self.text.count("./scripts/build-ios.sh"), 2)
        self.assertGreaterEqual(self.text.count("verify-artifact.sh"), 2)

    def test_bytecode_suppression_is_exported_to_every_release_subprocess(self):
        export = "export PYTHONDONTWRITEBYTECODE=1"
        self.assertIn(export, self.text)
        self.assertLess(
            self.text.index(export),
            self.text.index("python3 -m unittest discover -s tests"),
        )
        self.assertLess(
            self.text.index(export),
            self.text.index("./scripts/build-ios.sh"),
        )

        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            repository = fixture / "repository"
            scripts = repository / "scripts"
            fake_bin = fixture / "bin"
            scripts.mkdir(parents=True)
            fake_bin.mkdir()
            prepared = scripts / "prepare-release.sh"
            prepared.write_text(self.text, encoding="utf-8")
            prepared.chmod(0o755)
            build = scripts / "build-ios.sh"
            build.write_text(
                "#!/bin/sh\n"
                'test "${PYTHONDONTWRITEBYTECODE:-}" = 1\n'
                "test $? -eq 0 && exit 91\n"
                "exit 92\n",
                encoding="utf-8",
            )
            build.chmod(0o755)
            (repository / ".gitignore").write_text(
                "/.build/\n",
                encoding="utf-8",
            )
            fake_python = fake_bin / "python3"
            fake_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_python.chmod(0o755)
            subprocess.run(
                ["git", "init", "-q", str(repository)],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repository), "add", "."],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "-c",
                    "user.name=Release Test",
                    "-c",
                    "user.email=release-test@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )
            head = subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
            environment = dict(os.environ)
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            completed = subprocess.run(
                [
                    str(prepared),
                    "--version",
                    VERSION,
                    "--input-commit",
                    head,
                    "--dry-run",
                ],
                cwd=repository,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(completed.returncode, 91, completed.stderr)
            status = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "status",
                    "--short",
                    "--untracked-files=all",
                ],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout
            self.assertEqual(status, "")

    def test_generated_binding_and_two_archives_are_exactly_equal(self):
        self.assertIn("FCastSenderSDK.swift", self.text)
        self.assertIn("cmp", self.text)
        self.assertIn("scripts/archive.py", self.text)
        self.assertIn("swift package compute-checksum", self.text)
        self.assertIn("shasum -a 256", self.text)
        self.assertIn("SOURCE_DATE_EPOCH", self.text)

    def test_generates_only_immutable_manifest_and_base_provenance(self):
        self.assertIn("render-package", self.text)
        self.assertIn("render-provenance", self.text)
        self.assertIn("Package.swift", self.text)
        self.assertIn("provenance.json", self.text)
        self.assertIn(".build/release", self.text)
        self.assertNotIn("augment-provenance", self.text)
        self.assertNotIn("releaseTagCommit", self.text)

    def test_final_unzipped_archive_is_verified_by_local_api_probe(self):
        self.assertIn("unzip", self.text)
        self.assertIn("verify-local-package.sh", self.text)
        self.assertIn("fcast_sender_sdk.xcframework.zip", self.text)

    def test_dry_run_has_no_remote_or_git_mutation(self):
        forbidden_commands = (
            r"gh\s+(?:release|api)",
            r"git\s+push",
            r"git\s+tag",
            r"git\s+commit",
            r"git\s+add",
            r"git\s+reset",
        )
        for command in forbidden_commands:
            with self.subTest(command=command):
                self.assertNotRegex(
                    self.text,
                    rf"(?m)^[ \t]*{command}(?:\s|$)",
                )
        self.assertNotRegex(self.text, r"(?m)^\s*gh\s")
        self.assertNotIn("eval ", self.text)

    def test_prints_checksum_changed_files_and_exact_next_commands_as_data(self):
        self.assertIn(
            "git status --short --untracked-files=all --",
            self.text,
        )
        for candidate_path in (
            "Package.swift",
            "provenance.json",
            "Sources/FCastSenderSDK/FCastSenderSDK.swift",
        ):
            with self.subTest(candidate_path=candidate_path):
                self.assertIn(candidate_path, self.text)
        self.assertNotIn("git diff --name-only", self.text)
        self.assertIn("archive checksum:", self.text)
        self.assertIn("next commands:", self.text)
        self.assertIn("release: prepare 0.0.8-mediastorm.1", self.text)

    def test_invalid_invocations_fail_before_any_build(self):
        cases = (
            [],
            ["--version", VERSION, "--input-commit", "a" * 39, "--dry-run"],
            [
                "--version",
                "0.0.8-mediastorm.2",
                "--input-commit",
                RELEASE_INPUT_COMMIT,
                "--dry-run",
            ],
            ["--version", VERSION, "--input-commit", RELEASE_INPUT_COMMIT],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                completed = subprocess.run(
                    [str(PREPARE_RELEASE), *arguments],
                    cwd=REPOSITORY_ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertNotIn("release-build-1", completed.stdout)
