import copy
import hashlib
import json
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = REPOSITORY_ROOT / "mediastorm-api.lock.json"
PROBE_ROOT = REPOSITORY_ROOT / "Probes" / "MediaStormAPIProbe"

EXPECTED_LOCK = {
    "commit": "d14ab17dd6c743e88d71bb806b9f42fe8a069daf",
    "repository": "https://github.com/ayooooo123/mediastorm-frontend.git",
    "schemaVersion": 1,
    "sources": [
        {
            "copiedPath": "Probes/MediaStormAPIProbe/UpstreamApiProbe.swift",
            "originalPath": "modules/cast-transport/ios/UpstreamApiProbe.swift",
            "sha256": (
                "7b71a9e3c64895916d1cc77851a7221e879f6bba8e476bef6351aefc3c287a8a"
            ),
        },
        {
            "copiedPath": (
                "Probes/MediaStormAPIProbe/BonjourEndpointResolver.swift"
            ),
            "originalPath": (
                "modules/cast-transport/ios-core/Sources/"
                "CastTransportCore/BonjourEndpointResolver.swift"
            ),
            "sha256": (
                "10ff1d64de562c6f9a7363c99821d4219511deede88c6ac9aafbfdcf4e73a293"
            ),
        },
    ],
}
EXPECTED_MAIN = b'print("mediastorm-fcast-api-probe-compiled")\n'
EXPECTED_COMPATIBILITY = b"""public extension ChromecastDevice {
  func startMirroringSession(signaller: FwrtcSignaller) throws {
    try startMirroringSession(sig: signaller)
  }
}

public extension CastingDeviceProtocol {
  func load(request: LoadRequest) throws {
    try load(request: request, progressUpdateIntervalMillis: nil)
  }
}

public extension DeviceInfo {
  init(
    name: String,
    protocol: ProtocolType,
    addresses: [IpAddr],
    port: UInt16
  ) {
    self.init(
      name: name,
      protocol: `protocol`,
      addresses: addresses,
      port: port,
      txtRecords: [:]
    )
  }
}

public extension DeviceEventHandler {
  func playbackStopped() {}

  func tracksAvailable(tracks: [MediaTrack]) {}

  func trackSelected(id: UInt32?, typ: MediaTrackType) {}

  func tracksChanged(tracks: TrackList) {}

  func queueChanged(queue: QueueState) {}

  func commandError(error: ReceiverError) {}
}

public struct GenericKeyEvent: Sendable, Equatable, Hashable {
  public var released: Bool
  public var `repeat`: Bool
  public var handled: Bool
  public var name: String

  public init(released: Bool, repeat: Bool, handled: Bool, name: String) {
    self.released = released
    self.`repeat` = `repeat`
    self.handled = handled
    self.name = name
  }
}

public enum GenericMediaEvent: Sendable, Equatable, Hashable {
  case started
  case ended
  case changed
}
"""


def validate_lock(lock, file_reader):
    if set(lock) != {"schemaVersion", "repository", "commit", "sources"}:
        raise ValueError("lock root schema")
    if lock["schemaVersion"] != 1:
        raise ValueError("schema version")
    if lock["repository"] != EXPECTED_LOCK["repository"]:
        raise ValueError("repository")
    if lock["commit"] != EXPECTED_LOCK["commit"]:
        raise ValueError("full source commit")
    if lock["sources"] != EXPECTED_LOCK["sources"]:
        raise ValueError("source entries")

    for source in lock["sources"]:
        if set(source) != {"originalPath", "copiedPath", "sha256"}:
            raise ValueError("source entry schema")
        payload = file_reader(source["copiedPath"])
        if hashlib.sha256(payload).hexdigest() != source["sha256"]:
            raise ValueError(f"source hash: {source['copiedPath']}")


class MediaStormApiProbeTests(unittest.TestCase):
    def load_lock(self):
        self.assertTrue(LOCK_PATH.is_file(), f"missing {LOCK_PATH}")
        return json.loads(LOCK_PATH.read_text(encoding="utf-8"))

    @staticmethod
    def repository_reader(relative_path):
        return (REPOSITORY_ROOT / relative_path).read_bytes()

    def test_lock_is_exact_canonical_json_and_copied_sources_match_hashes(self):
        lock = self.load_lock()
        validate_lock(lock, self.repository_reader)
        canonical = json.dumps(
            EXPECTED_LOCK,
            indent=2,
            sort_keys=True,
        ) + "\n"
        self.assertEqual(LOCK_PATH.read_text(encoding="utf-8"), canonical)

    def test_rejects_unknown_root_and_source_entry_keys_independently(self):
        cases = []
        root_mutation = copy.deepcopy(EXPECTED_LOCK)
        root_mutation["unexpected"] = True
        cases.append(("root", root_mutation))
        entry_mutation = copy.deepcopy(EXPECTED_LOCK)
        entry_mutation["sources"][0]["unexpected"] = True
        cases.append(("entry", entry_mutation))

        for label, mutation in cases:
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    validate_lock(mutation, self.repository_reader)

    def test_rejects_wrong_paths_repository_and_commits_independently(self):
        cases = []
        wrong_path = copy.deepcopy(EXPECTED_LOCK)
        wrong_path["sources"][0]["originalPath"] = "wrong.swift"
        cases.append(("path", wrong_path))
        wrong_repository = copy.deepcopy(EXPECTED_LOCK)
        wrong_repository["repository"] = "https://example.invalid/repo.git"
        cases.append(("repository", wrong_repository))
        short_commit = copy.deepcopy(EXPECTED_LOCK)
        short_commit["commit"] = EXPECTED_LOCK["commit"][:12]
        cases.append(("short-commit", short_commit))
        wrong_commit = copy.deepcopy(EXPECTED_LOCK)
        wrong_commit["commit"] = "0" * 40
        cases.append(("wrong-commit", wrong_commit))

        for label, mutation in cases:
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    validate_lock(mutation, self.repository_reader)

    def test_rejects_drift_in_either_copied_source_independently(self):
        lock = self.load_lock()
        canonical_files = {
            source["copiedPath"]: self.repository_reader(source["copiedPath"])
            for source in lock["sources"]
        }
        for drifted_path in canonical_files:
            with self.subTest(drifted_path=drifted_path):
                def reader(relative_path, drifted_path=drifted_path):
                    payload = canonical_files[relative_path]
                    return (
                        payload + b"\n// drift\n"
                        if relative_path == drifted_path
                        else payload
                    )

                with self.assertRaisesRegex(ValueError, "source hash"):
                    validate_lock(lock, reader)

    def test_main_is_only_the_exact_harmless_sentinel(self):
        main_path = PROBE_ROOT / "main.swift"
        self.assertTrue(main_path.is_file(), f"missing {main_path}")
        self.assertEqual(main_path.read_bytes(), EXPECTED_MAIN)

    def test_rejects_a_noncanonical_sentinel(self):
        for mutation in (
            EXPECTED_MAIN.rstrip(b"\n"),
            b'print("different")\n',
            EXPECTED_MAIN + b"UpstreamApiProbe.probe()\n",
        ):
            with self.subTest(mutation=mutation):
                self.assertNotEqual(mutation, EXPECTED_MAIN)

    def test_mediastorm_compatibility_surface_is_exact_and_has_no_ffi(self):
        compatibility_path = (
            REPOSITORY_ROOT
            / "Sources"
            / "FCastSenderSDK"
            / "MediaStormCompatibility.swift"
        )
        self.assertTrue(
            compatibility_path.is_file(),
            f"missing {compatibility_path}",
        )
        payload = compatibility_path.read_bytes()
        self.assertEqual(payload, EXPECTED_COMPATIBILITY)
        for forbidden in (b"FfiConverter", b"rustCall", b"uniffi_"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, payload)


if __name__ == "__main__":
    unittest.main()
