import AppKit
import Carbon
import Foundation

// Property codes are from the installed Mail.sdef. No arbitrary event/property input exists.
private func code(_ text: String) -> FourCharCode {
    text.utf8.reduce(0) { ($0 << 8) | FourCharCode($1) }
}

private enum Property: String {
    case id = "ID  "
    case name = "pnam"
    case sender = "sndr"
    case subject = "subj"
    case received = "rdrc"
    case read = "isrd"
}

private enum Element: String {
    case account = "mact"
    case mailbox = "mbxp"
    case message = "mssg"
}

private enum Failure: Error {
    case missing, denied, deadline, invalid
}

private func eventFailure(_ number: Int) -> Failure {
    if number == errAEEventNotPermitted { return .denied }
    if number == errAENoSuchObject || number == errAEIllegalIndex { return .missing }
    if number == errAETimeout { return .deadline }
    return .invalid
}

private func object(
    _ kind: FourCharCode, _ container: NSAppleEventDescriptor,
    _ form: FourCharCode, _ selector: NSAppleEventDescriptor
) -> NSAppleEventDescriptor {
    let record = NSAppleEventDescriptor.record()
    record.setDescriptor(NSAppleEventDescriptor(typeCode: kind), forKeyword: UInt32(keyAEDesiredClass))
    record.setDescriptor(container, forKeyword: UInt32(keyAEContainer))
    record.setDescriptor(NSAppleEventDescriptor(enumCode: form), forKeyword: UInt32(keyAEKeyForm))
    record.setDescriptor(selector, forKeyword: UInt32(keyAEKeyData))
    return record.coerce(toDescriptorType: typeObjectSpecifier)!
}

private final class Reader {
    let target: NSAppleEventDescriptor
    let deadline = ProcessInfo.processInfo.systemUptime + 8
    var truncated = false

    init(_ target: NSAppleEventDescriptor) { self.target = target }

    func element(
        _ kind: Element, in container: NSAppleEventDescriptor, index: Int
    ) -> NSAppleEventDescriptor {
        object(code(kind.rawValue), container, UInt32(formAbsolutePosition),
               NSAppleEventDescriptor(int32: Int32(index)))
    }

    func named(
        _ kind: Element, in container: NSAppleEventDescriptor, name: String
    ) -> NSAppleEventDescriptor {
        object(code(kind.rawValue), container, UInt32(formName), NSAppleEventDescriptor(string: name))
    }

    func get(_ property: Property, of container: NSAppleEventDescriptor) throws
        -> NSAppleEventDescriptor {
        guard ProcessInfo.processInfo.systemUptime < deadline else { throw Failure.deadline }
        let event = NSAppleEventDescriptor(
            eventClass: code("core"), eventID: code("getd"), targetDescriptor: target,
            returnID: AEReturnID(kAutoGenerateReturnID), transactionID: AETransactionID(kAnyTransactionID)
        )
        event.setParam(
            object(code("prop"), container, UInt32(formPropertyID),
                   NSAppleEventDescriptor(typeCode: code(property.rawValue))),
            forKeyword: keyDirectObject
        )
        let reply: NSAppleEventDescriptor
        do {
            reply = try event.sendEvent(
                options: [.waitForReply, .neverInteract, .dontRecord], timeout: 1
            )
        } catch { throw eventFailure((error as NSError).code) }
        if let error = reply.paramDescriptor(forKeyword: keyErrorNumber), error.int32Value != 0 {
            throw eventFailure(Int(error.int32Value))
        }
        guard let value = reply.paramDescriptor(forKeyword: keyDirectObject) else {
            throw Failure.missing
        }
        return value
    }

    func text(_ property: Property, of container: NSAppleEventDescriptor, limit: Int) throws
        -> String {
        guard let value = try get(property, of: container).stringValue,
              value.count <= limit else { throw Failure.invalid }
        return value
    }
}

private struct Selection {
    let account: String
    let path: [String]
}

private func selections(_ argument: String) throws -> [Selection] {
    guard argument.utf8.count <= 20000,
          let data = argument.data(using: .utf8),
          let values = try JSONSerialization.jsonObject(with: data) as? [[String: Any]],
          (1...5).contains(values.count) else { throw Failure.invalid }
    return try values.map { value in
        guard let account = value["account_id"] as? String, !account.isEmpty, account.count <= 256,
              let path = value["mailbox_path"] as? [String], (1...8).contains(path.count),
              path.allSatisfy({ !$0.isEmpty && $0.count <= 200 }) else { throw Failure.invalid }
        return Selection(account: account, path: path)
    }
}

private func discover(_ reader: Reader) throws -> [[String: Any]] {
    var result = [[String: Any]]()
    let root = NSAppleEventDescriptor.null()
    func visit(_ container: NSAppleEventDescriptor, _ account: String, _ name: String,
               _ path: [String]) throws {
        guard path.count < 8 else { reader.truncated = true; return }
        for index in 1...201 {
            guard result.count < 200 else { reader.truncated = true; return }
            let box = reader.element(.mailbox, in: container, index: index)
            let label: String
            do { label = try reader.text(.name, of: box, limit: 200) }
            catch Failure.missing { return }
            let child = path + [label]
            result.append(["account_id": account, "account_name": name, "mailbox_path": child])
            try visit(box, account, name, child)
        }
    }
    for index in 1...21 {
        if index > 20 { reader.truncated = true; break }
        let account = reader.element(.account, in: root, index: index)
        let id: String
        do { id = try reader.text(.id, of: account, limit: 256) }
        catch Failure.missing { break }
        let name = try reader.text(.name, of: account, limit: 100)
        try visit(account, id, name, [])
    }
    return result
}

private func headers(_ reader: Reader, _ selected: [Selection]) throws -> [String: Any] {
    var result = [[String: Any]]()
    var missing = false
    let formatter = ISO8601DateFormatter()
    for selection in selected {
        let account = object(
            code(Element.account.rawValue), NSAppleEventDescriptor.null(), UInt32(formUniqueID),
            NSAppleEventDescriptor(string: selection.account)
        )
        var box = account
        for part in selection.path { box = reader.named(.mailbox, in: box, name: part) }
        do { _ = try reader.text(.name, of: box, limit: 200) }
        catch Failure.missing { missing = true; continue }
        for index in 1...20 {
            if result.count >= 50 { reader.truncated = true; break }
            let candidate = reader.element(.message, in: box, index: index)
            let identifier: Int32
            do { identifier = try reader.get(.id, of: candidate).int32Value }
            catch Failure.missing { break }
            let message = object(
                code(Element.message.rawValue), box, UInt32(formUniqueID),
                NSAppleEventDescriptor(int32: identifier)
            )
            let sender = try reader.text(.sender, of: message, limit: 500)
            let subject = try reader.text(.subject, of: message, limit: 500)
            guard let received = try reader.get(.received, of: message).dateValue else {
                throw Failure.invalid
            }
            let read = try reader.get(.read, of: message).booleanValue
            result.append([
                "id": String(identifier), "account_id": selection.account,
                "mailbox_path": selection.path, "sender": sender, "subject": subject,
                "received": formatter.string(from: received), "read": read,
            ])
            if index == 20 { reader.truncated = true }
        }
    }
    return ["headers": result, "selection_missing": missing, "truncated": reader.truncated]
}

@main
private struct MailBridgeMain {
    static func main() {
        var result: [String: Any] = ["schema_version": 1, "status": "unavailable"]
        defer {
            if let data = try? JSONSerialization.data(withJSONObject: result),
               data.count <= 250000 {
                FileHandle.standardOutput.write(data)
            }
        }
        let args = CommandLine.arguments
        guard args.count >= 2 else { return }
        let operation = args[1]
        guard ["status", "authorize", "discover", "headers"].contains(operation),
              args.count == (operation == "headers" ? 3 : 2) else { return }
        // NSRunningApplication lookup does not open or launch Mail.
        guard let mail = NSRunningApplication.runningApplications(
            withBundleIdentifier: "com.apple.mail"
        ).first, !mail.isTerminated else { result["status"] = "not_running"; return }
        var pid = mail.processIdentifier
        guard let target = withUnsafeBytes(of: &pid, {
            NSAppleEventDescriptor(descriptorType: typeKernelProcessID, data: Data($0))
        }) else { return }
        let permission = AEDeterminePermissionToAutomateTarget(
            target.aeDesc, typeWildCard, typeWildCard, operation == "authorize"
        )
        if permission != noErr {
            result["status"] = permission == errAEEventWouldRequireUserConsent
                ? "not_determined" : "denied"
            return
        }
        result["status"] = "authorized"
        if operation == "status" || operation == "authorize" { return }
        let reader = Reader(target)
        do {
            if operation == "discover" {
                result["mailboxes"] = try discover(reader)
                result["truncated"] = reader.truncated
            } else {
                let sampled = try headers(reader, selections(args[2]))
                result.merge(sampled) { _, new in new }
            }
        } catch Failure.denied {
            result = ["schema_version": 1, "status": "denied"]
        } catch {
            // No raw AppleEvent errors, descriptors, paths, or header contents on stderr.
            result = ["schema_version": 1, "status": "unavailable"]
        }
    }
}
