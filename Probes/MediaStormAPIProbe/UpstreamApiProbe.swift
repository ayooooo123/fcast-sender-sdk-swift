#if os(iOS)
import FCastSenderSDK
import Foundation
import Network

/// Compile-only proof of the released sender surface. Never invoke `probe()`.
private enum UpstreamApiProbe {
  private final class DeviceProbeHandler: DeviceEventHandler, @unchecked Sendable {
    func connectionStateChanged(state: DeviceConnectionState) {}

    func volumeChanged(volume: Double) {}

    func timeChanged(time: Double) {}

    func playbackStateChanged(state: PlaybackState) {}

    func durationChanged(duration: Double) {}

    func speedChanged(speed: Double) {}

    func sourceChanged(source: Source) {}

    func keyEvent(event: GenericKeyEvent) {}

    func mediaEvent(event: GenericMediaEvent) {}

    func playbackError(message: String) {}
  }

  private static let resolver = BonjourEndpointResolver()
  private static let deviceHandler = DeviceProbeHandler()

  static func probe() {
    let discoverer = NWDeviceDiscoverer(
      onAdded: { found in
        _ = found.proto
        _ = found.endpoint
        resolver.resolve(found) { info in
          do {
            _ = info.protocol
            _ = info.addresses
            _ = info.port
            let device = try CastContext().createDeviceFromInfo(info: info)
            _ = device.getAddresses()
            _ = device.getPort()
            try device.connect(
              appInfo: ApplicationInfo(
                name: "mediastorm",
                version: "spike",
                displayName: "mediastorm"
              ),
              eventHandler: deviceHandler,
              reconnectIntervalMillis: 1_000
            )
            try device.load(
              request: .url(
                contentType: "application/vnd.apple.mpegurl",
                url: "http://127.0.0.1/probe.m3u8",
                resumePosition: 12.0,
                speed: 1.0,
                volume: 0.5,
                metadata: Metadata(
                  title: "Probe",
                  thumbnailUrl: "http://127.0.0.1/probe.jpg"
                ),
                requestHeaders: ["X-Probe": "true"]
              )
            )
            try device.seek(timeSeconds: 15.0)
            try device.pausePlayback()
            try device.resumePlayback()
            try device.changeVolume(volume: 0.4)
            try device.changeSpeed(speed: 1.0)
            try device.stopPlayback()
            try device.disconnect()
          } catch {
            return
          }
        }
      },
      onRemoved: { endpoint in
        resolver.cancel(endpoint)
      }
    )
    _ = discoverer
  }
}
#endif
