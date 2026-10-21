// Protection for routes that change things. Web pages open in Chrome on this
// machine can send requests to 127.0.0.1, and a DNS rebinding trick can make
// them look same-origin. A page's request carries an Origin header (browsers
// add it to cross-site POSTs) and a Host header with a name we do not use. Our
// own clients (curl, Python) send no Origin and Host is 127.0.0.1:<port>.
public enum LocalGuard {
    /// Returns nil when the request may go on, or the 403 response to send.
    public static func check(_ req: HTTPRequest, port: UInt16) -> HTTPResponse? {
        if req.headers["origin"] != nil {
            return HTTPResponse.json(#"{"error":"forbidden_origin"}"#, status: 403)
        }
        guard let host = req.headers["host"]?.lowercased(),
              host == "127.0.0.1:\(port)" || host == "localhost:\(port)" else {
            return HTTPResponse.json(#"{"error":"forbidden_host"}"#, status: 403)
        }
        return nil
    }
}
