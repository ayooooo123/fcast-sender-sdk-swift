public extension ChromecastDevice {
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
