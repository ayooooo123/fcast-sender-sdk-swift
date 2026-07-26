#if os(iOS)
import Darwin
import FCastSenderSDK
import Foundation
import Network

final class BonjourEndpointResolver: NSObject, @unchecked Sendable {
  typealias ResolutionHandler = @Sendable (DeviceInfo) -> Void

  private final class Delegate: NSObject, NetServiceDelegate, @unchecked Sendable {
    weak var owner: BonjourEndpointResolver?
    let key: String

    init(owner: BonjourEndpointResolver, key: String) {
      self.owner = owner
      self.key = key
    }

    func netServiceDidResolveAddress(_ sender: NetService) {
      owner?.resolved(sender, key: key)
    }

    func netService(_ sender: NetService, didNotResolve errorDict: [String: NSNumber]) {
      owner?.forget(sender, key: key)
    }
  }

  private struct PendingResolution {
    let found: FoundDevice
    let service: NetService
    let delegate: Delegate
    let completion: ResolutionHandler
  }

  private let lock = NSLock()
  private var pending: [String: PendingResolution] = [:]

  func resolve(_ found: FoundDevice, completion: @escaping ResolutionHandler) {
    guard case let .service(name, type, domain, _) = found.endpoint else {
      return
    }

    let key = Self.key(for: found.endpoint)
    let service = NetService(domain: domain, type: type, name: name)
    let delegate = Delegate(owner: self, key: key)
    service.delegate = delegate

    let replacement = PendingResolution(
      found: found,
      service: service,
      delegate: delegate,
      completion: completion
    )
    lock.lock()
    let existing = pending.updateValue(replacement, forKey: key)
    lock.unlock()
    existing?.service.stop()

    service.resolve(withTimeout: 5)
  }

  func cancel(_ endpoint: NWEndpoint) {
    take(key: Self.key(for: endpoint))?.service.stop()
  }

  private static func key(for endpoint: NWEndpoint) -> String {
    guard case let .service(name, type, domain, interface) = endpoint else {
      return String(describing: endpoint)
    }
    return "\(name)|\(type)|\(domain)|\(String(describing: interface))"
  }

  private func take(
    key: String,
    matching service: NetService? = nil
  ) -> PendingResolution? {
    lock.lock()
    defer { lock.unlock() }
    if let service,
       let current = pending[key],
       current.service !== service {
      return nil
    }
    return pending.removeValue(forKey: key)
  }

  fileprivate func forget(_ service: NetService, key: String) {
    let resolution = take(key: key, matching: service)
    (resolution?.service ?? service).stop()
  }

  fileprivate func resolved(_ service: NetService, key: String) {
    guard let resolution = take(key: key, matching: service) else {
      service.stop()
      return
    }
    service.stop()

    guard (1...65_535).contains(service.port),
          let socketAddresses = service.addresses,
          !socketAddresses.isEmpty,
          let addresses = Self.convert(socketAddresses),
          !addresses.isEmpty else {
      return
    }

    resolution.completion(
      DeviceInfo(
        name: resolution.found.name,
        protocol: resolution.found.proto,
        addresses: addresses,
        port: UInt16(service.port)
      )
    )
  }

  private static func convert(_ socketAddresses: [Data]) -> [IpAddr]? {
    var ipv4: [(String, IpAddr)] = []
    var ipv6: [(String, IpAddr)] = []

    for data in socketAddresses {
      guard let converted = numericAddress(data) else {
        return nil
      }
      do {
        let address = try tryIpAddrFromStr(s: converted.host)
        if converted.family == sa_family_t(AF_INET) {
          ipv4.append((converted.host, address))
        } else {
          ipv6.append((converted.host, address))
        }
      } catch {
        return nil
      }
    }

    var seen = Set<String>()
    return (ipv4 + ipv6).compactMap { host, address in
      seen.insert(host).inserted ? address : nil
    }
  }

  private static func numericAddress(_ data: Data) -> (host: String, family: sa_family_t)? {
    data.withUnsafeBytes { rawBuffer in
      guard let baseAddress = rawBuffer.baseAddress,
            rawBuffer.count >= MemoryLayout<sockaddr>.size else {
        return nil
      }

      let socketAddress = baseAddress.assumingMemoryBound(to: sockaddr.self)
      let family = socketAddress.pointee.sa_family
      let requiredLength: Int
      switch Int32(family) {
      case AF_INET:
        requiredLength = MemoryLayout<sockaddr_in>.size
      case AF_INET6:
        requiredLength = MemoryLayout<sockaddr_in6>.size
      default:
        return nil
      }
      guard rawBuffer.count >= requiredLength else {
        return nil
      }

      var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
      let status = getnameinfo(
        socketAddress,
        socklen_t(requiredLength),
        &host,
        socklen_t(host.count),
        nil,
        0,
        NI_NUMERICHOST
      )
      guard status == 0 else {
        return nil
      }
      return (String(cString: host), family)
    }
  }
}
#endif
