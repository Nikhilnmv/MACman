// macman-local — Apple's on-device model, over JSON-on-stdio.
//
// This is what makes MACman's private task set cost nothing to install. The
// model ships with macOS 26; there is no download, no daemon, no disk cost.
// Ollama remains supported as an opt-in upgrade, but should not be the default
// a new user is asked to accept — nobody installs 5 GB to try a free tool.
//
//   macman-local check                       → availability as JSON
//   macman-local generate --prompt "..."     → one response as JSON
//   echo '{"prompt":"..."}' | macman-local generate
//
// Prompts arrive on argv or stdin; stdin is preferred for anything long or
// containing quotes, which is most real prompts.

import Foundation

#if canImport(FoundationModels)
import FoundationModels
#endif

// MARK: - Wire format

struct CheckResult: Codable {
    var available: Bool
    var detail: String
    /// Whether this binary was compiled with tool support. Python needs to
    /// know: without it the engine can reason but cannot take actions, and
    /// that limitation should be reported rather than discovered.
    var tools: Bool
}

struct GenerateResult: Codable {
    var ok: Bool
    var content: String?
    var error: String?
    var elapsedMs: Int
}

func emit<T: Encodable>(_ value: T) {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    guard let data = try? encoder.encode(value),
          let text = String(data: data, encoding: .utf8) else {
        FileHandle.standardError.write(Data("macman-local: encoding failed\n".utf8))
        exit(1)
    }
    print(text)
}

// MARK: - Input

/// Reads the prompt from `--prompt`, or as a single line on stdin.
///
/// **One line, not to EOF.** Reading to EOF would deadlock: the tool proxy
/// keeps stdin open for the whole session so results can be sent back, so EOF
/// never arrives and the helper waits forever having never started work.
///
/// stdin still matters as an input path — a prompt containing quotes,
/// newlines, or a file's contents cannot be passed safely on argv — so long
/// prompts are sent as one line of JSON: `{"prompt": "..."}`.
func readPrompt(_ arguments: [String]) -> String? {
    if let index = arguments.firstIndex(of: "--prompt"), index + 1 < arguments.count {
        return arguments[index + 1]
    }

    guard let raw = readLine(strippingNewline: true)?
            .trimmingCharacters(in: .whitespacesAndNewlines),
          !raw.isEmpty else { return nil }

    if let json = try? JSONSerialization.jsonObject(with: Data(raw.utf8)) as? [String: Any],
       let prompt = json["prompt"] as? String {
        return prompt
    }
    return raw
}

// MARK: - Model

@main
struct MacmanLocal {

    static func main() async {
        let arguments = Array(CommandLine.arguments.dropFirst())
        let command = arguments.first ?? "check"

        switch command {
        case "check":
            emit(availability())

        case "generate":
            await generate(arguments: arguments)

        default:
            FileHandle.standardError.write(Data("""
                macman-local: unknown command '\(command)'
                usage: macman-local [check|generate] [--prompt TEXT] [--instructions TEXT]

                """.utf8))
            exit(2)
        }
    }

    /// True when built with `-DMACMAN_TOOLS`, which needs full Xcode for the
    /// `FoundationModelsMacros` plugin. A build-time fact, so a compile-time
    /// constant rather than something probed at runtime.
    static var toolsCompiledIn: Bool {
        #if MACMAN_TOOLS
        return true
        #else
        return false
        #endif
    }

    /// Whether the on-device model can serve a request right now.
    ///
    /// Reported rather than assumed: Apple Intelligence can be switched off, or
    /// still downloading, or unsupported on the hardware. Each is a different
    /// message to the user, and "unavailable" alone would send them looking in
    /// the wrong place.
    static func availability() -> CheckResult {
        #if canImport(FoundationModels)
        guard #available(macOS 26.0, *) else {
            return CheckResult(available: false, detail: "requires macOS 26 or later",
                               tools: toolsCompiledIn)
        }
        switch SystemLanguageModel.default.availability {
        case .available:
            return CheckResult(available: true, detail: "ready", tools: toolsCompiledIn)
        case .unavailable(let reason):
            return CheckResult(available: false, detail: describe(reason),
                               tools: toolsCompiledIn)
        @unknown default:
            return CheckResult(available: false, detail: "unavailable for an unknown reason",
                               tools: toolsCompiledIn)
        }
        #else
        return CheckResult(available: false,
                           detail: "built without FoundationModels — needs the macOS 26 SDK",
                           tools: toolsCompiledIn)
        #endif
    }

    #if canImport(FoundationModels)
    @available(macOS 26.0, *)
    static func describe(_ reason: SystemLanguageModel.Availability.UnavailableReason) -> String {
        switch reason {
        case .appleIntelligenceNotEnabled:
            return "Apple Intelligence is off — turn it on in System Settings → Apple Intelligence & Siri"
        case .modelNotReady:
            return "the model is still downloading — this finishes on its own, try again shortly"
        case .deviceNotEligible:
            return "this Mac doesn't support Apple Intelligence"
        @unknown default:
            return "unavailable for an unknown reason"
        }
    }
    #endif

    static func generate(arguments: [String]) async {
        let started = Date()

        func fail(_ message: String) -> Never {
            emit(GenerateResult(ok: false, content: nil, error: message,
                                elapsedMs: Int(Date().timeIntervalSince(started) * 1000)))
            exit(1)
        }

        let check = availability()
        guard check.available else { fail(check.detail) }
        guard let prompt = readPrompt(arguments) else {
            fail("no prompt given — pass --prompt or write one to stdin")
        }

        #if canImport(FoundationModels)
        guard #available(macOS 26.0, *) else { fail("requires macOS 26 or later") }

        // Instructions are kept separate from the prompt because the model
        // treats them as standing guidance rather than as part of the request.
        let instructions = arguments.firstIndex(of: "--instructions")
            .flatMap { $0 + 1 < arguments.count ? arguments[$0 + 1] : nil }

        // With tools compiled in, the session can act; without, it can only
        // reason about text. The tools themselves proxy back to Python so the
        // guard and audit log stay in one place — see ToolProxy.swift.
        #if MACMAN_TOOLS
        let tools = macmanTools()
        let session = instructions.map { LanguageModelSession(tools: tools, instructions: $0) }
            ?? LanguageModelSession(tools: tools)
        #else
        let session = instructions.map { LanguageModelSession(instructions: $0) }
            ?? LanguageModelSession()
        #endif

        do {
            let response = try await session.respond(to: prompt)
            emit(GenerateResult(
                ok: true, content: response.content, error: nil,
                elapsedMs: Int(Date().timeIntervalSince(started) * 1000)
            ))
        } catch {
            // Guardrail rejections surface here too; passing the message
            // through unchanged lets the Python side report the real reason
            // instead of a generic failure.
            fail("\(error)")
        }
        #else
        fail("built without FoundationModels")
        #endif
    }
}
