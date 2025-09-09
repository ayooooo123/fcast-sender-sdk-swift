import Network

public struct FoundDevice: Sendable {
    public var name: String
    public var endpoint: NWEndpoint
    public var proto: ProtocolType
}

public final class NWDeviceDiscoverer {
    private var fCastBrowser: NWBrowser
    private var chromecastBrowser: NWBrowser

    public init(
        onAdded: @escaping @Sendable (FoundDevice) -> Void,
        onRemoved: @escaping @Sendable (NWEndpoint) -> Void,
    ) {
        fCastBrowser = NWBrowser(
            for: .bonjourWithTXTRecord(type: "_fcast._tcp", domain: nil),
            using: .tcp
        )
        chromecastBrowser = NWBrowser(
            for: .bonjourWithTXTRecord(type: "_googlecast._tcp", domain: nil),
            using: .tcp
        )

        fCastBrowser.browseResultsChangedHandler = { newResults, changes in
            for result in changes {
                switch result {
                case .added(let added):
                    if case .service(let name, _, _, _) = added.endpoint {
                        onAdded(
                            FoundDevice(
                                name: name,
                                endpoint: added.endpoint,
                                proto: ProtocolType.fCast
                            )
                        )
                    }
                case .removed(let removed):
                    onRemoved(removed.endpoint)
                default:
                    break
                }
            }
        }
        chromecastBrowser.browseResultsChangedHandler = { newResults, changes in
            for result in changes {
                switch result {
                case .added(let added):
                    if case .service(var name, _, _, _) = added.endpoint {
                        if case .bonjour(let txt) = added.metadata,
                            let maybeFriendlyNameData = txt.getEntry(for: "fn"),
                            let friendlyNameData = maybeFriendlyNameData.data,
                            let friendlyName = String(
                                data: friendlyNameData,
                                encoding: .utf8
                            )
                        {
                            name = friendlyName
                        }
                        onAdded(
                            FoundDevice(
                                name: name,
                                endpoint: added.endpoint,
                                proto: ProtocolType.chromecast
                            )
                        )
                    }
                case .removed(let removed):
                    onRemoved(removed.endpoint)
                default:
                    break
                }
            }
        }

        fCastBrowser.start(queue: .main)
        chromecastBrowser.start(queue: .main)
    }
}
