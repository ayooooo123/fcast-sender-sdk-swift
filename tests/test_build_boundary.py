import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class OutputDirectorySafetyTests(unittest.TestCase):
    def load_module_object(self):
        import scripts.output_directory as output_directory

        return output_directory

    def load_module(self):
        from scripts.output_directory import (
            OutputDirectoryError,
            discard_output,
            prepare_output,
            publish_output,
        )

        return (
            OutputDirectoryError,
            prepare_output,
            publish_output,
            discard_output,
        )

    def test_rejects_symlink_alias_without_mutating_its_sibling_target(self):
        Error, prepare, _, _ = self.load_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / ".build"
            victim = build / "victim"
            victim.mkdir(parents=True)
            marker = victim / "marker"
            marker.write_text("preserve", encoding="utf-8")
            (build / "alias").symlink_to(victim, target_is_directory=True)

            with self.assertRaisesRegex(Error, "symlink"):
                prepare(root, ".build/alias")

            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
            self.assertTrue((build / "alias").is_symlink())

    def test_rejects_nested_dotdot_separator_and_control_character_names(self):
        Error, prepare, _, _ = self.load_module()
        invalid = (
            ".build/a/b",
            ".build/.",
            ".build/..",
            ".build/a\nb",
            ".build/a\rb",
            ".build/a\tb",
            ".build/a/b/..",
            "outside",
        )
        with tempfile.TemporaryDirectory() as directory:
            for value in invalid:
                with self.subTest(value=repr(value)):
                    with self.assertRaises(Error):
                        prepare(directory, value)

    def test_private_staging_and_atomic_publication_replace_only_target(self):
        _, prepare, publish, discard = self.load_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sibling = root / ".build" / "sibling"
            sibling.mkdir(parents=True)
            (sibling / "marker").write_text("sibling", encoding="utf-8")
            target = root / ".build" / "repro1"
            target.mkdir()
            (target / "old").write_text("old", encoding="utf-8")

            prepared = prepare(root, ".build/repro1")
            staging = root / ".build" / prepared.staging_name
            self.assertTrue(prepared.staging_name.startswith(".repro1.staging-"))
            self.assertEqual(
                (target / "old").read_text(encoding="utf-8"),
                "old",
            )
            (staging / "new").write_text("new", encoding="utf-8")

            result = publish(root, prepared)

            self.assertTrue(result.committed)
            self.assertEqual(result.warnings, ())
            self.assertFalse((target / "old").exists())
            self.assertEqual(
                (target / "new").read_text(encoding="utf-8"),
                "new",
            )
            self.assertEqual(
                (sibling / "marker").read_text(encoding="utf-8"),
                "sibling",
            )
            discard(root, prepared)

    def test_build_script_validates_output_before_clone_and_never_deletes_final_target(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts" / "build-ios.sh").read_text(encoding="utf-8")
        self.assertLess(text.index("output_directory.py"), text.index("git clone"))
        self.assertNotIn('rm -rf "$DISTRIBUTION_OUTPUT"', text)

    def test_publication_rejects_a_real_directory_swapped_into_the_target(self):
        Error, prepare, publish, discard = self.load_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / ".build" / "repro1"
            target.mkdir(parents=True)
            (target / "old").write_text("old", encoding="utf-8")
            sibling = root / ".build" / "victim"
            sibling.mkdir()
            marker = sibling / "marker"
            marker.write_text("preserve", encoding="utf-8")
            prepared = prepare(root, ".build/repro1")

            target.rename(root / ".build" / "moved-old")
            sibling.rename(target)
            with self.assertRaisesRegex(Error, "changed"):
                publish(root, prepared)

            self.assertEqual(
                (target / "marker").read_text(encoding="utf-8"),
                "preserve",
            )
            discard(root, prepared)

    def test_publication_rejects_a_new_directory_created_at_absent_target(self):
        Error, prepare, publish, discard = self.load_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = prepare(root, ".build/repro2")
            target = root / ".build" / "repro2"
            target.mkdir()
            (target / "marker").write_text("preserve", encoding="utf-8")

            with self.assertRaisesRegex(Error, "changed"):
                publish(root, prepared)

            self.assertEqual(
                (target / "marker").read_text(encoding="utf-8"),
                "preserve",
            )
            discard(root, prepared)

    def test_discard_rejects_forged_staging_tokens_without_removing_the_target(self):
        from scripts.output_directory import PreparedOutput

        Error, _, _, discard = self.load_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / ".build"
            victim = build / "victim"
            victim.mkdir(parents=True)
            marker = victim / "marker"
            marker.write_text("preserve", encoding="utf-8")
            build_stat = build.stat()
            victim_stat = victim.stat()
            forged = PreparedOutput(
                final_name="repro1",
                staging_name="victim",
                build_device=build_stat.st_dev,
                build_inode=build_stat.st_ino,
                staging_device=victim_stat.st_dev,
                staging_inode=victim_stat.st_ino,
                final_device=-1,
                final_inode=-1,
            )

            with self.assertRaisesRegex(Error, "token"):
                discard(root, forged)

            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_existing_target_swap_after_check_is_restored_without_deleting_victim(self):
        module = self.load_module_object()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / ".build" / "repro1"
            target.mkdir(parents=True)
            (target / "old").write_text("old", encoding="utf-8")
            victim = root / ".build" / "victim"
            victim.mkdir()
            marker = victim / "marker"
            marker.write_text("preserve", encoding="utf-8")
            prepared = module.prepare_output(root, ".build/repro1")
            staging = root / ".build" / prepared.staging_name
            (staging / "new").write_text("new", encoding="utf-8")
            original_unique_name = module._unique_name

            def swap_after_check(build_fd, prefix, *, create):
                if ".quarantine-" in prefix:
                    target.rename(root / ".build" / "moved-old")
                    victim.rename(target)
                return original_unique_name(build_fd, prefix, create=create)

            with mock.patch.object(
                module,
                "_unique_name",
                side_effect=swap_after_check,
            ):
                with self.assertRaisesRegex(
                    module.OutputDirectoryError,
                    "changed.*quarantine|quarantine.*changed",
                ):
                    module.publish_output(root, prepared)

            self.assertEqual(
                (target / "marker").read_text(encoding="utf-8"),
                "preserve",
            )
            self.assertTrue(staging.is_dir())

    def test_absent_target_concurrently_created_after_check_is_not_replaced(self):
        module = self.load_module_object()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = module.prepare_output(root, ".build/repro2")
            staging = root / ".build" / prepared.staging_name
            (staging / "new").write_text("new", encoding="utf-8")
            target = root / ".build" / "repro2"
            original_check = module._require_final_unchanged
            concurrent_inode = None

            def create_after_check(build_fd, checked):
                nonlocal concurrent_inode
                result = original_check(build_fd, checked)
                target.mkdir()
                concurrent_inode = target.stat().st_ino
                return result

            with mock.patch.object(
                module,
                "_require_final_unchanged",
                side_effect=create_after_check,
            ):
                with self.assertRaisesRegex(
                    module.OutputDirectoryError,
                    "concurrent|exclusive|already exists",
                ):
                    module.publish_output(root, prepared)

            self.assertEqual(target.stat().st_ino, concurrent_inode)
            self.assertTrue(staging.is_dir())

    def test_symlink_swapped_for_checked_staging_is_recovered_and_old_target_restored(
        self,
    ):
        module = self.load_module_object()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / ".build"
            target = build / "repro1"
            target.mkdir(parents=True)
            (target / "old").write_text("old", encoding="utf-8")
            victim = build / "symlink-target"
            victim.mkdir()
            (victim / "unexpected").write_text("preserve", encoding="utf-8")
            prepared = module.prepare_output(root, ".build/repro1")
            staging = build / prepared.staging_name
            (staging / "verified").write_text("verified", encoding="utf-8")
            moved_verified = build / "verified-staging-moved"
            original_require_staging = module._require_staging

            def swap_after_staging_check(build_fd, checked):
                result = original_require_staging(build_fd, checked)
                staging.rename(moved_verified)
                staging.symlink_to(victim, target_is_directory=True)
                return result

            with mock.patch.object(
                module,
                "_require_staging",
                side_effect=swap_after_staging_check,
            ):
                with self.assertRaisesRegex(
                    module.OutputDirectoryError,
                    "staging identity.*recovery.*restored",
                ):
                    module.publish_output(root, prepared)

            self.assertFalse(target.is_symlink())
            self.assertEqual(
                (target / "old").read_text(encoding="utf-8"),
                "old",
            )
            self.assertEqual(
                (moved_verified / "verified").read_text(encoding="utf-8"),
                "verified",
            )
            recoveries = list(build.glob(".repro1.recovery-*"))
            self.assertEqual(len(recoveries), 1)
            self.assertTrue(recoveries[0].is_symlink())
            self.assertEqual(
                (recoveries[0].resolve() / "unexpected").read_text(
                    encoding="utf-8"
                ),
                "preserve",
            )
            self.assertEqual(list(build.glob(".repro1.quarantine-*")), [])

    def test_unverified_directory_swapped_for_checked_staging_is_recovered(
        self,
    ):
        module = self.load_module_object()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / ".build"
            target = build / "repro1"
            target.mkdir(parents=True)
            (target / "old").write_text("old", encoding="utf-8")
            prepared = module.prepare_output(root, ".build/repro1")
            staging = build / prepared.staging_name
            (staging / "verified").write_text("verified", encoding="utf-8")
            moved_verified = build / "verified-staging-moved"
            unexpected_inode = None
            original_require_staging = module._require_staging

            def swap_after_staging_check(build_fd, checked):
                nonlocal unexpected_inode
                result = original_require_staging(build_fd, checked)
                staging.rename(moved_verified)
                staging.mkdir()
                (staging / "unexpected").write_text(
                    "preserve",
                    encoding="utf-8",
                )
                unexpected_inode = staging.stat().st_ino
                return result

            with mock.patch.object(
                module,
                "_require_staging",
                side_effect=swap_after_staging_check,
            ):
                with self.assertRaisesRegex(
                    module.OutputDirectoryError,
                    "staging identity.*recovery.*restored",
                ):
                    module.publish_output(root, prepared)

            self.assertEqual(
                (target / "old").read_text(encoding="utf-8"),
                "old",
            )
            self.assertEqual(
                (moved_verified / "verified").read_text(encoding="utf-8"),
                "verified",
            )
            recoveries = list(build.glob(".repro1.recovery-*"))
            self.assertEqual(len(recoveries), 1)
            self.assertEqual(recoveries[0].stat().st_ino, unexpected_inode)
            self.assertEqual(
                (recoveries[0] / "unexpected").read_text(encoding="utf-8"),
                "preserve",
            )
            self.assertEqual(list(build.glob(".repro1.quarantine-*")), [])

    def test_unverified_directory_swapped_for_checked_staging_at_absent_target_is_recovered(
        self,
    ):
        module = self.load_module_object()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / ".build"
            prepared = module.prepare_output(root, ".build/repro2")
            staging = build / prepared.staging_name
            (staging / "verified").write_text("verified", encoding="utf-8")
            moved_verified = build / "verified-staging-moved"
            original_require_staging = module._require_staging

            def swap_after_staging_check(build_fd, checked):
                result = original_require_staging(build_fd, checked)
                staging.rename(moved_verified)
                staging.mkdir()
                (staging / "unexpected").write_text(
                    "preserve",
                    encoding="utf-8",
                )
                return result

            with mock.patch.object(
                module,
                "_require_staging",
                side_effect=swap_after_staging_check,
            ):
                with self.assertRaisesRegex(
                    module.OutputDirectoryError,
                    "staging identity.*recovery.*final.*absent",
                ):
                    module.publish_output(root, prepared)

            self.assertFalse((build / "repro2").exists())
            self.assertEqual(
                (moved_verified / "verified").read_text(encoding="utf-8"),
                "verified",
            )
            recoveries = list(build.glob(".repro2.recovery-*"))
            self.assertEqual(len(recoveries), 1)
            self.assertEqual(
                (recoveries[0] / "unexpected").read_text(encoding="utf-8"),
                "preserve",
            )

    def test_blocked_staging_recovery_preserves_unexpected_final_and_old_quarantine(
        self,
    ):
        module = self.load_module_object()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / ".build"
            target = build / "repro1"
            target.mkdir(parents=True)
            (target / "old").write_text("old", encoding="utf-8")
            prepared = module.prepare_output(root, ".build/repro1")
            staging = build / prepared.staging_name
            (staging / "verified").write_text("verified", encoding="utf-8")
            moved_verified = build / "verified-staging-moved"
            original_require_staging = module._require_staging
            original_unique_name = module._unique_name
            recovery_name = None

            def swap_after_staging_check(build_fd, checked):
                result = original_require_staging(build_fd, checked)
                staging.rename(moved_verified)
                staging.mkdir()
                (staging / "unexpected").write_text(
                    "preserve",
                    encoding="utf-8",
                )
                return result

            def block_recovery_name(build_fd, prefix, *, create):
                nonlocal recovery_name
                name = original_unique_name(build_fd, prefix, create=create)
                if ".recovery-" in prefix:
                    recovery_name = name
                    (build / name).mkdir()
                return name

            with (
                mock.patch.object(
                    module,
                    "_require_staging",
                    side_effect=swap_after_staging_check,
                ),
                mock.patch.object(
                    module,
                    "_unique_name",
                    side_effect=block_recovery_name,
                ),
            ):
                with self.assertRaisesRegex(
                    module.OutputDirectoryError,
                    "staging identity.*unexpected final.*quarantine.*preserved",
                ):
                    module.publish_output(root, prepared)

            self.assertEqual(
                (target / "unexpected").read_text(encoding="utf-8"),
                "preserve",
            )
            quarantines = list(build.glob(".repro1.quarantine-*"))
            self.assertEqual(len(quarantines), 1)
            self.assertEqual(
                (quarantines[0] / "old").read_text(encoding="utf-8"),
                "old",
            )
            self.assertTrue((build / recovery_name).is_dir())
            self.assertEqual(
                (moved_verified / "verified").read_text(encoding="utf-8"),
                "verified",
            )

    def test_blocked_old_target_restore_preserves_every_publication_entry(self):
        module = self.load_module_object()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / ".build"
            target = build / "repro1"
            target.mkdir(parents=True)
            (target / "old").write_text("old", encoding="utf-8")
            prepared = module.prepare_output(root, ".build/repro1")
            staging = build / prepared.staging_name
            (staging / "verified").write_text("verified", encoding="utf-8")
            moved_verified = build / "verified-staging-moved"
            original_require_staging = module._require_staging
            original_rename = module._rename_exclusive
            rename_count = 0

            def swap_after_staging_check(build_fd, checked):
                result = original_require_staging(build_fd, checked)
                staging.rename(moved_verified)
                staging.mkdir()
                (staging / "unexpected").write_text(
                    "preserve",
                    encoding="utf-8",
                )
                return result

            def block_restore(
                source,
                destination,
                *,
                source_fd,
                destination_fd,
            ):
                nonlocal rename_count
                rename_count += 1
                if rename_count == 4:
                    target.mkdir()
                    (target / "concurrent").write_text(
                        "preserve",
                        encoding="utf-8",
                    )
                return original_rename(
                    source,
                    destination,
                    source_fd=source_fd,
                    destination_fd=destination_fd,
                )

            with (
                mock.patch.object(
                    module,
                    "_require_staging",
                    side_effect=swap_after_staging_check,
                ),
                mock.patch.object(
                    module,
                    "_rename_exclusive",
                    side_effect=block_restore,
                ),
            ):
                with self.assertRaisesRegex(
                    module.OutputDirectoryError,
                    "staging identity.*recovery.*quarantine.*preserved",
                ):
                    module.publish_output(root, prepared)

            self.assertEqual(
                (target / "concurrent").read_text(encoding="utf-8"),
                "preserve",
            )
            recoveries = list(build.glob(".repro1.recovery-*"))
            self.assertEqual(len(recoveries), 1)
            self.assertEqual(
                (recoveries[0] / "unexpected").read_text(encoding="utf-8"),
                "preserve",
            )
            quarantines = list(build.glob(".repro1.quarantine-*"))
            self.assertEqual(len(quarantines), 1)
            self.assertEqual(
                (quarantines[0] / "old").read_text(encoding="utf-8"),
                "old",
            )
            self.assertEqual(
                (moved_verified / "verified").read_text(encoding="utf-8"),
                "verified",
            )

    def test_concurrent_quarantine_destination_is_not_replaced(self):
        module = self.load_module_object()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / ".build" / "repro1"
            target.mkdir(parents=True)
            (target / "old").write_text("old", encoding="utf-8")
            prepared = module.prepare_output(root, ".build/repro1")
            staging = root / ".build" / prepared.staging_name
            (staging / "new").write_text("new", encoding="utf-8")
            original_unique_name = module._unique_name
            concurrent_name = None
            concurrent_inode = None

            def reserve_quarantine(build_fd, prefix, *, create):
                nonlocal concurrent_name, concurrent_inode
                name = original_unique_name(build_fd, prefix, create=create)
                if ".quarantine-" in prefix:
                    concurrent_name = name
                    concurrent = root / ".build" / name
                    concurrent.mkdir()
                    concurrent_inode = concurrent.stat().st_ino
                return name

            with mock.patch.object(
                module,
                "_unique_name",
                side_effect=reserve_quarantine,
            ):
                with self.assertRaisesRegex(
                    module.OutputDirectoryError,
                    "quarantine.*exists|exclusive",
                ):
                    module.publish_output(root, prepared)

            self.assertEqual(
                (root / ".build" / concurrent_name).stat().st_ino,
                concurrent_inode,
            )
            self.assertEqual(
                (target / "old").read_text(encoding="utf-8"),
                "old",
            )
            self.assertTrue(staging.is_dir())

    def test_failed_stage_publication_preserves_concurrent_final_and_old_quarantine(self):
        module = self.load_module_object()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / ".build"
            target = build / "repro1"
            target.mkdir(parents=True)
            (target / "old").write_text("old", encoding="utf-8")
            prepared = module.prepare_output(root, ".build/repro1")
            staging = build / prepared.staging_name
            (staging / "new").write_text("new", encoding="utf-8")
            original_rename = module._rename_exclusive
            call_count = 0

            def race_rename(
                source,
                destination,
                *,
                source_fd,
                destination_fd,
            ):
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    target.mkdir()
                    (target / "concurrent").write_text(
                        "preserve",
                        encoding="utf-8",
                    )
                return original_rename(
                    source,
                    destination,
                    source_fd=source_fd,
                    destination_fd=destination_fd,
                )

            with mock.patch.object(
                module,
                "_rename_exclusive",
                side_effect=race_rename,
            ):
                with self.assertRaisesRegex(
                    module.OutputDirectoryError,
                    "quarantine.*preserved|preserved.*quarantine",
                ):
                    module.publish_output(root, prepared)

            self.assertEqual(
                (target / "concurrent").read_text(encoding="utf-8"),
                "preserve",
            )
            quarantines = list(build.glob(".repro1.quarantine-*"))
            self.assertEqual(len(quarantines), 1)
            self.assertEqual(
                (quarantines[0] / "old").read_text(encoding="utf-8"),
                "old",
            )
            self.assertTrue(staging.is_dir())

    def test_exclusive_publication_fails_closed_when_platform_support_is_unavailable(self):
        module = self.load_module_object()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = module.prepare_output(root, ".build/repro2")
            staging = root / ".build" / prepared.staging_name
            (staging / "new").write_text("new", encoding="utf-8")

            with mock.patch.object(module.sys, "platform", "linux"):
                with self.assertRaisesRegex(
                    module.OutputDirectoryError,
                    "exclusive rename.*unavailable",
                ):
                    module.publish_output(root, prepared)

            self.assertTrue(staging.is_dir())
            self.assertFalse((root / ".build" / "repro2").exists())

    def test_cleanup_failure_after_commit_returns_warning_and_keeps_new_final(self):
        module = self.load_module_object()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / ".build"
            target = build / "repro1"
            target.mkdir(parents=True)
            (target / "old").write_text("old", encoding="utf-8")
            prepared = module.prepare_output(root, ".build/repro1")
            staging = build / prepared.staging_name
            (staging / "new").write_text("new", encoding="utf-8")

            with mock.patch.object(
                module,
                "_remove_tree_at",
                side_effect=module.OutputDirectoryError(
                    "cleanup inspection denied"
                ),
            ):
                result = module.publish_output(root, prepared)

            self.assertTrue(result.committed)
            self.assertRegex(
                "\n".join(result.warnings),
                "cleanup inspection denied",
            )
            self.assertEqual(
                (target / "new").read_text(encoding="utf-8"),
                "new",
            )
            quarantines = list(build.glob(".repro1.quarantine-*"))
            self.assertEqual(len(quarantines), 1)
            self.assertEqual(
                (quarantines[0] / "old").read_text(encoding="utf-8"),
                "old",
            )

    def test_post_commit_fsync_failure_returns_warning_and_cli_success(self):
        module = self.load_module_object()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / ".build" / "repro2"
            prepared = module.prepare_output(root, ".build/repro2")
            staging = root / ".build" / prepared.staging_name
            (staging / "new").write_text("new", encoding="utf-8")

            with mock.patch.object(
                module.os,
                "fsync",
                side_effect=OSError("post-commit fsync denied"),
            ):
                result = module.publish_output(root, prepared)

            self.assertTrue(result.committed)
            self.assertRegex(
                "\n".join(result.warnings),
                "committed.*fsync|fsync.*committed",
            )
            self.assertEqual(
                (target / "new").read_text(encoding="utf-8"),
                "new",
            )
            with mock.patch.object(module, "publish_output", return_value=result):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    return_code = module.main(
                        [
                            "publish",
                            "--root",
                            str(root),
                            "--final-name",
                            prepared.final_name,
                            "--staging-name",
                            prepared.staging_name,
                            "--build-device",
                            str(prepared.build_device),
                            "--build-inode",
                            str(prepared.build_inode),
                            "--staging-device",
                            str(prepared.staging_device),
                            "--staging-inode",
                            str(prepared.staging_inode),
                            "--final-device",
                            str(prepared.final_device),
                            "--final-inode",
                            str(prepared.final_inode),
                        ]
                    )
            self.assertEqual(return_code, 0)
            self.assertRegex(stderr.getvalue(), "warning.*committed")


class AmbientBuildEnvironmentTests(unittest.TestCase):
    def load_validator(self):
        from scripts.build_boundary import (
            BuildBoundaryError,
            validate_ambient_environment,
        )

        return BuildBoundaryError, validate_ambient_environment

    def test_rejects_each_compiler_profile_and_target_poisoning_class(self):
        Error, validate = self.load_validator()
        poisoned_names = (
            "RUSTFLAGS",
            "CARGO_ENCODED_RUSTFLAGS",
            "RUSTDOCFLAGS",
            "RUSTC",
            "RUSTDOC",
            "RUSTC_WRAPPER",
            "RUSTC_WORKSPACE_WRAPPER",
            "CARGO_BUILD_TARGET",
            "CARGO_TARGET_DIR",
            "CARGO_INCREMENTAL",
            "CARGO_PROFILE_RELEASE_LTO",
            "CARGO_BUILD_RUSTFLAGS",
            "CARGO_TARGET_AARCH64_APPLE_IOS_LINKER",
            "CC",
            "CXX",
            "AR",
            "LD",
            "CFLAGS",
            "CXXFLAGS",
            "LDFLAGS",
            "SDKROOT",
            "IPHONEOS_DEPLOYMENT_TARGET",
        )
        for name in poisoned_names:
            with self.subTest(name=name):
                with self.assertRaisesRegex(Error, name):
                    validate({name: "attacker"})

    def test_allows_transport_only_network_settings(self):
        _, validate = self.load_validator()
        validate(
            {
                "HTTPS_PROXY": "http://127.0.0.1:8080",
                "CARGO_HTTP_TIMEOUT": "600",
                "CARGO_NET_RETRY": "4",
            }
        )

    def test_rejects_unknown_cargo_rustup_and_compiler_search_path_variables(self):
        Error, validate = self.load_validator()
        poisoned_names = (
            "CARGO_FUTURE_OUTPUT_SWITCH",
            "RUSTUP_HOME",
            "RUSTUP_TOOLCHAIN",
            "CPPFLAGS",
            "CPATH",
            "LIBRARY_PATH",
            "DEVELOPER_DIR",
            "TOOLCHAINS",
            "SWIFT_EXEC",
        )
        for name in poisoned_names:
            with self.subTest(name=name):
                with self.assertRaisesRegex(Error, name):
                    validate({name: "attacker"})

    def test_rejects_cc_rs_bindgen_libclang_and_pkg_config_variable_families(self):
        Error, validate = self.load_validator()
        poisoned_names = (
            "CC_aarch64_apple_ios",
            "CC_aarch64-apple-ios",
            "CFLAGS_AARCH64_APPLE_IOS",
            "AR_x86_64_apple_darwin",
            "TARGET_CC",
            "HOST_CC",
            "TARGET_CXXFLAGS",
            "HOST_CPPFLAGS",
            "TARGET_LD",
            "HOST_LDFLAGS",
            "CRATE_CC_NO_DEFAULTS",
            "BINDGEN_EXTRA_CLANG_ARGS",
            "BINDGEN_EXTRA_CLANG_ARGS_aarch64_apple_ios",
            "LIBCLANG_PATH",
            "CLANG_PATH",
            "PKG_CONFIG",
            "PKG_CONFIG_PATH",
            "PKG_CONFIG_SYSROOT_DIR_aarch64_apple_ios",
            "AARCH64_APPLE_IOS_PKG_CONFIG_PATH",
        )
        for name in poisoned_names:
            with self.subTest(name=name):
                with self.assertRaisesRegex(Error, name):
                    validate({name: "attacker"})

    def test_sanitized_child_environment_is_an_explicit_allowlist(self):
        root = Path(__file__).resolve().parents[1]
        command = [
            sys.executable,
            str(root / "scripts" / "build_boundary.py"),
            "sanitized-environment",
        ]
        ambient = {
            "PATH": "/safe/bin",
            "HOME": "/safe/home",
            "TMPDIR": "/safe/tmp",
            "LANG": "en_US.UTF-8",
            "LC_ALL": "C",
            "HTTPS_PROXY": "http://127.0.0.1:8080",
            "NO_PROXY": "localhost",
            "CARGO_HTTP_TIMEOUT": "600",
            "CARGO_NET_GIT_FETCH_WITH_CLI": "true",
            "RUST_LOG": "secret",
            "SSH_AUTH_SOCK": "/secret/agent",
            "UNRELATED_SECRET": "do-not-leak",
        }
        result = subprocess.run(
            command,
            env=ambient,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        sanitized = json.loads(result.stdout)
        self.assertEqual(
            sanitized,
            {
                "CARGO_HTTP_TIMEOUT": "600",
                "CARGO_NET_GIT_FETCH_WITH_CLI": "true",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "HOME": "/safe/home",
                "HTTPS_PROXY": "http://127.0.0.1:8080",
                "LANG": "en_US.UTF-8",
                "LC_ALL": "C",
                "MEDIASTORM_SANITIZED_BUILD": "1",
                "NO_PROXY": "localhost",
                "PATH": "/safe/bin",
                "TMPDIR": "/safe/tmp",
            },
        )

    def test_sanitized_environment_check_rejects_spoofed_sentinel_with_extra_state(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                str(root / "scripts" / "build_boundary.py"),
                "check-sanitized-environment",
            ],
            env={
                "PATH": "/safe/bin",
                "HOME": "/safe/home",
                "MEDIASTORM_SANITIZED_BUILD": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "UNRELATED_SECRET": "must-not-reach-build",
            },
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertRegex(result.stderr, "UNRELATED_SECRET")

    def test_sanitized_environment_check_allows_macos_encoding_runtime_state(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                str(root / "scripts" / "build_boundary.py"),
                "check-sanitized-environment",
            ],
            env={
                "PATH": "/safe/bin",
                "HOME": "/safe/home",
                "MEDIASTORM_SANITIZED_BUILD": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "__CF_USER_TEXT_ENCODING": "0x1F5:0x0:0x0",
            },
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reexec_preserves_the_build_output_option_and_value(self):
        import scripts.build_boundary as build_boundary

        child_environment = {"PATH": "/safe/bin"}
        with (
            mock.patch.object(
                build_boundary,
                "sanitized_environment",
                return_value=child_environment,
            ),
            mock.patch.object(build_boundary.os, "execve") as execve,
        ):
            result = build_boundary.main(
                [
                    "re-exec",
                    "--script",
                    "/repo/scripts/build-ios.sh",
                    "--argument",
                    ".build/repro1",
                ]
            )

        self.assertEqual(result, 0)
        execve.assert_called_once_with(
            "/repo/scripts/build-ios.sh",
            [
                "/repo/scripts/build-ios.sh",
                "--output",
                ".build/repro1",
            ],
            child_environment,
        )

    def test_output_preparation_failure_removes_private_build_temp(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            temporary_root = fixture / "tmp"
            temporary_root.mkdir()
            fake_bin = fixture / "bin"
            fake_bin.mkdir()
            (fake_bin / "python3").symlink_to(sys.executable)
            command_outputs = {
                "sw_vers": "26.0\n",
                "xcodebuild": "Xcode 26.6\nBuild version 17F113\n",
                "swift": (
                    "Apple Swift version 6.3.3 "
                    "(swiftlang-6.3.3.1.3 clang-1700.0.0.0)\n"
                ),
                "rustup": "rustc 1.96.1 (31fca3adb 2026-06-26)\n",
            }
            for name in (
                "git",
                "rustup",
                "cargo",
                "xcodebuild",
                "lipo",
                "swift",
                "sw_vers",
                "shasum",
            ):
                executable = fake_bin / name
                executable.write_text(
                    "#!/bin/sh\n"
                    f"printf '%b' {command_outputs.get(name, '')!r}\n",
                    encoding="utf-8",
                )
                executable.chmod(0o755)
            environment = {
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "HOME": str(fixture / "home"),
                "TMPDIR": f"{temporary_root}/",
                "LANG": "C",
            }
            result = subprocess.run(
                [
                    str(root / "scripts" / "build-ios.sh"),
                    "--output",
                    ".build/a/b",
                ],
                cwd=root,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                list(temporary_root.glob("fcast-ios-distribution.*")),
                [],
                result.stderr,
            )

    def test_build_script_checks_poisoning_and_uses_private_cargo_home(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts" / "build-ios.sh").read_text(encoding="utf-8")
        poison_check = text.index("check-environment")
        clone = text.index("git clone")
        self.assertLess(poison_check, clone)
        self.assertIn('CARGO_HOME="$BUILD_TEMP/cargo-home"', text)
        self.assertIn('export CARGO_HOME', text)

    def test_build_script_remaps_the_private_build_root_before_rust_compilation(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "scripts" / "build-ios.sh").read_text(encoding="utf-8")
        poison_check = text.index("check-environment")
        canonicalize = text.index(
            'BUILD_TEMP="$(cd "$BUILD_TEMP" && pwd -P)"'
        )
        remap = text.index(
            'CARGO_ENCODED_RUSTFLAGS="--remap-path-prefix=$BUILD_TEMP=/fcast-build"'
        )
        native_remap = text.index(
            'CFLAGS="-ffile-prefix-map=$BUILD_TEMP=/fcast-build '
            '-fdebug-prefix-map=$BUILD_TEMP=/fcast-build"'
        )
        first_cargo = text.index('cargo "+$RUST_TOOLCHAIN" test')

        self.assertLess(poison_check, canonicalize)
        self.assertLess(canonicalize, remap)
        self.assertLess(remap, native_remap)
        self.assertLess(native_remap, first_cargo)
        self.assertLess(remap, first_cargo)
        self.assertIn("export CARGO_ENCODED_RUSTFLAGS", text)
        self.assertIn("export CFLAGS", text)


if __name__ == "__main__":
    unittest.main()
