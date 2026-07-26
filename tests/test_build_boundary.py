import os
import tempfile
import unittest
from pathlib import Path


class OutputDirectorySafetyTests(unittest.TestCase):
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

            publish(root, prepared)

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
