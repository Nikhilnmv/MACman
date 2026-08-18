// macman-speech — on-device transcription and speech, over JSON-on-stdio.
//
// Both halves of voice, free and offline:
//
//   macman-speech check                   → availability + permissions
//   macman-speech listen [--seconds N]    → transcribe from the microphone
//   macman-speech say --text "..."        → speak through the default output
//   macman-speech say --device "BlackHole 2ch" --text "..."
//
// `SFSpeechRecognizer` with `requiresOnDeviceRecognition = true` keeps audio on
// the machine — nothing is uploaded, and it works with no network. That matters
// here for the same reason the rest of MACman is on-device: a voice assistant
// that ships your speech to a server is a different product.
//
// The `--device` option exists for v3: speaking into BlackHole is how MACman
// talks *into* a FaceTime call, since macOS offers no way to inject audio into
// a microphone directly.

import AVFoundation
import Foundation
import Speech

// MARK: - Wire format

struct CheckResult: Codable {
    var recogniserAvailable: Bool
    var onDeviceSupported: Bool
    var microphone: String
    var speechRecognition: String
    var outputDevices: [String]
}

struct ListenResult: Codable {
    var ok: Bool
    var text: String?
    var error: String?
    var elapsedMs: Int
}

struct SayResult: Codable {
    var ok: Bool
    var error: String?
    var elapsedMs: Int
}

struct TranscribeResult: Codable {
    var ok: Bool
    var text: String?
    var error: String?
    /// Whether on-device recognition was actually requested. Reported rather
    /// than assumed — if this is false, the audio went to Apple's servers.
    var onDevice: Bool
    var elapsedMs: Int
}

func emit<T: Encodable>(_ value: T) {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    guard let data = try? encoder.encode(value),
          let text = String(data: data, encoding: .utf8) else {
        FileHandle.standardError.write(Data("macman-speech: encoding failed\n".utf8))
        exit(1)
    }
    print(text)
    fflush(stdout)
}

func argument(_ name: String, _ args: [String]) -> String? {
    guard let index = args.firstIndex(of: name), index + 1 < args.count else { return nil }
    return args[index + 1]
}

// MARK: - Permissions

/// Mutable state shared between a callback thread and a reader.
///
/// The recognition callback and the silence-watching loop run on different
/// queues and touch the same values, which is a genuine data race rather than a
/// theoretical one — transcription callbacks fire continuously while the
/// watcher polls. Everything crossing that boundary goes through here.
final class Locked<Value>: @unchecked Sendable {
    private var value: Value
    private let lock = NSLock()

    init(_ value: Value) { self.value = value }

    func withLock<Result>(_ body: (inout Value) -> Result) -> Result {
        lock.lock()
        defer { lock.unlock() }
        return body(&value)
    }

    var current: Value { withLock { $0 } }
}

/// Request microphone and speech permission, waiting until macOS answers.
///
/// Both are one-time prompts attached to the *calling* binary, so the first run
/// of this helper raises them even if the terminal already has the grants.
///
/// The wait spins the run loop rather than blocking on a semaphore. Blocking
/// starves the very callbacks being waited on — they are delivered through the
/// run loop — so the permissions never resolve and the audio engine then aborts
/// on an unauthorised microphone. This is the same trap as `write` and
/// playback below; every wait in this file has to keep the run loop alive.
func requestPermissions(timeout: TimeInterval = 60) -> (mic: Bool, speech: Bool) {
    let granted = Locked((mic: false, speech: false))
    let answered = Locked((mic: false, speech: false))

    AVCaptureDevice.requestAccess(for: .audio) { allowed in
        granted.withLock { $0.mic = allowed }
        answered.withLock { $0.mic = true }
    }
    SFSpeechRecognizer.requestAuthorization { status in
        granted.withLock { $0.speech = (status == .authorized) }
        answered.withLock { $0.speech = true }
    }

    let deadline = Date().addingTimeInterval(timeout)
    while Date() < deadline {
        let done = answered.current
        if done.mic && done.speech { break }
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
    }
    return granted.current
}

func describeMic() -> String {
    ["notDetermined", "restricted", "denied", "authorized"][
        AVCaptureDevice.authorizationStatus(for: .audio).rawValue]
}

func describeSpeech() -> String {
    ["notDetermined", "denied", "restricted", "authorized"][
        SFSpeechRecognizer.authorizationStatus().rawValue]
}

// MARK: - Output devices

/// Names of every audio output device, so Python can confirm BlackHole exists
/// before trying to speak into it.
func outputDeviceNames() -> [String] {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDevices,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)

    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(
        AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size) == noErr
    else { return [] }

    let count = Int(size) / MemoryLayout<AudioDeviceID>.size
    var ids = [AudioDeviceID](repeating: 0, count: count)
    guard AudioObjectGetPropertyData(
        AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &ids) == noErr
    else { return [] }

    return ids.compactMap { id -> String? in
        // Only report devices that can actually play audio.
        var streamAddress = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyStreams,
            mScope: kAudioDevicePropertyScopeOutput,
            mElement: kAudioObjectPropertyElementMain)
        var streamSize: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(id, &streamAddress, 0, nil, &streamSize) == noErr,
              streamSize > 0 else { return nil }

        var nameAddress = AudioObjectPropertyAddress(
            mSelector: kAudioObjectPropertyName,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        // `Unmanaged<CFString>?`, not `CFString`. Core Audio writes a +1
        // retained CFStringRef into this address, which ARC neither knows to
        // release nor expects to appear. Passing a managed `CFString` also
        // hands Core Audio a pointer to an ARC-owned reference and leaks the
        // placeholder it overwrites — the compiler flags exactly this.
        var name: Unmanaged<CFString>?
        var nameSize = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        guard AudioObjectGetPropertyData(
                  id, &nameAddress, 0, nil, &nameSize, &name) == noErr,
              let name else { return nil }
        return name.takeRetainedValue() as String
    }
}

// MARK: - Listen

/// Transcribe from the microphone until the speaker stops.
///
/// Ends on silence rather than a fixed timer: a fixed window either truncates a
/// long sentence or leaves the caller waiting after a short one. `maxSeconds`
/// is only a backstop against a stuck stream.
func listen(maxSeconds: Double, silenceSeconds: Double) {
    let started = Date()

    func fail(_ message: String) -> Never {
        emit(ListenResult(ok: false, text: nil, error: message,
                          elapsedMs: Int(Date().timeIntervalSince(started) * 1000)))
        exit(1)
    }

    // Report rather than request, for the reason spelled out in `transcribe`:
    // a permission *request* is attributed to whichever app launched us, and
    // TCC kills the process outright if that app declares no usage string.
    // `authorise` is the one command allowed to prompt.
    guard AVCaptureDevice.authorizationStatus(for: .audio) == .authorized else {
        fail("Microphone permission not granted (\(describeMic())). "
             + "Run `macman setup` from Terminal.")
    }
    guard SFSpeechRecognizer.authorizationStatus() == .authorized else {
        fail("Speech recognition permission not granted (\(describeSpeech())). "
             + "Run `macman setup` from Terminal.")
    }

    guard let recogniser = SFSpeechRecognizer(locale: Locale(identifier: "en-US")),
          recogniser.isAvailable else {
        fail("Speech recogniser is unavailable.")
    }

    let request = SFSpeechAudioBufferRecognitionRequest()
    request.shouldReportPartialResults = true
    if recogniser.supportsOnDeviceRecognition {
        // The whole point: audio never leaves the Mac.
        request.requiresOnDeviceRecognition = true
    }

    let engine = AVAudioEngine()
    let input = engine.inputNode
    let format = input.outputFormat(forBus: 0)

    struct Progress {
        var transcript = ""
        var lastUpdate = Date()
        var settled = false
    }
    let state = Locked(Progress())

    let task = recogniser.recognitionTask(with: request) { result, error in
        var shouldFinish = false
        state.withLock { progress in
            if let result {
                let latest = result.bestTranscription.formattedString
                if latest != progress.transcript {
                    progress.transcript = latest
                    progress.lastUpdate = Date()
                }
                if result.isFinal, !progress.settled {
                    progress.settled = true
                    shouldFinish = true
                }
            }
            if error != nil, !progress.settled {
                progress.settled = true
                shouldFinish = true
            }
        }
        _ = shouldFinish
    }

    input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
        request.append(buffer)
    }

    do {
        engine.prepare()
        try engine.start()
    } catch {
        fail("Could not start audio input: \(error.localizedDescription)")
    }

    // Wait on the main run loop, not a semaphore: recognition callbacks are
    // delivered through it, and blocking here means they never arrive.
    while true {
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
        let done: Bool = state.withLock { progress in
            if progress.settled { return true }
            let quietFor = Date().timeIntervalSince(progress.lastUpdate)
            let ranTooLong = Date().timeIntervalSince(started) > maxSeconds
            if (!progress.transcript.isEmpty && quietFor > silenceSeconds) || ranTooLong {
                progress.settled = true
                return true
            }
            return false
        }
        if done { break }
    }
    engine.stop()
    input.removeTap(onBus: 0)
    request.endAudio()
    task.cancel()

    let elapsed = Int(Date().timeIntervalSince(started) * 1000)
    let heard = state.current.transcript
        .trimmingCharacters(in: .whitespacesAndNewlines)
    if heard.isEmpty {
        emit(ListenResult(ok: false, text: nil, error: "Nothing was heard.",
                          elapsedMs: elapsed))
        exit(1)
    }
    emit(ListenResult(ok: true, text: heard, error: nil, elapsedMs: elapsed))
}

// MARK: - Say

/// Look up an output device's CoreAudio ID by (partial) name.
func outputDeviceID(named wanted: String) -> AudioDeviceID? {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDevices,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)

    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(
        AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size) == noErr
    else { return nil }

    var ids = [AudioDeviceID](repeating: 0,
                              count: Int(size) / MemoryLayout<AudioDeviceID>.size)
    guard AudioObjectGetPropertyData(
        AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &ids) == noErr
    else { return nil }

    for id in ids {
        var nameAddress = AudioObjectPropertyAddress(
            mSelector: kAudioObjectPropertyName,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        // See the note in `outputDeviceNames`: Core Audio returns a +1
        // retained reference, so it must be taken as retained rather than
        // written into an ARC-managed variable.
        var name: Unmanaged<CFString>?
        var nameSize = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        guard AudioObjectGetPropertyData(
                  id, &nameAddress, 0, nil, &nameSize, &name) == noErr,
              let name else { continue }
        if (name.takeRetainedValue() as String)
            .localizedCaseInsensitiveContains(wanted) { return id }
    }
    return nil
}

/// Speak text aloud, optionally through a named output device.
///
/// Synthesised to PCM and played through `AVAudioEngine` rather than handed to
/// `AVSpeechSynthesizer.speak`, for two reasons that both bit earlier versions:
///
/// * **`speak` is fire-and-forget.** `isSpeaking` is still false immediately
///   after calling it, so a short-lived CLI exits before any sound is produced
///   — reporting success while playing nothing.
/// * **`speak` has no device selection.** It always uses the system default.
///   v3 needs MACman to talk into BlackHole (and so into a FaceTime call)
///   while the speakers stay free, which is only possible by owning playback.
func say(text: String, device: String?) {
    let started = Date()

    func finish(_ ok: Bool, _ error: String?) -> Never {
        emit(SayResult(ok: ok, error: error,
                       elapsedMs: Int(Date().timeIntervalSince(started) * 1000)))
        exit(ok ? 0 : 1)
    }

    guard !text.trimmingCharacters(in: .whitespaces).isEmpty else {
        finish(false, "Nothing to say.")
    }

    var targetDevice: AudioDeviceID?
    if let device, !device.isEmpty {
        guard let found = outputDeviceID(named: device) else {
            finish(false, "No output device matching \(device). Is it installed and loaded?")
        }
        targetDevice = found
    }

    // Synthesise fully to buffers first; nothing is played until we own it.
    let synthesiser = AVSpeechSynthesizer()
    let utterance = AVSpeechUtterance(string: text)
    utterance.voice = AVSpeechSynthesisVoice(language: "en-US")
    utterance.rate = AVSpeechUtteranceDefaultSpeechRate

    // `write` delivers buffers via the run loop, so the wait below spins it
    // rather than blocking. Blocking here starves the callback and yields zero
    // buffers — measured: 0 with a semaphore, 157 with a run loop.
    let collected = Locked([AVAudioPCMBuffer]())
    let synthesised = Locked(false)

    synthesiser.write(utterance) { buffer in
        guard let pcm = buffer as? AVAudioPCMBuffer else { return }
        if pcm.frameLength == 0 {
            synthesised.withLock { $0 = true }   // zero-length marks the end
        } else {
            collected.withLock { $0.append(pcm) }
        }
    }

    let synthesisDeadline = Date().addingTimeInterval(30)
    while !synthesised.current && Date() < synthesisDeadline {
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
    }
    guard synthesised.current else { finish(false, "Speech synthesis timed out.") }

    let buffers = collected.current
    guard let first = buffers.first else { finish(false, "Synthesis produced no audio.") }

    let engine = AVAudioEngine()
    let player = AVAudioPlayerNode()

    if let targetDevice {
        do {
            try engine.outputNode.auAudioUnit.setDeviceID(targetDevice)
        } catch {
            finish(false, "Could not route to that device: \(error.localizedDescription)")
        }
    }

    engine.attach(player)
    engine.connect(player, to: engine.mainMixerNode, format: first.format)

    do {
        engine.prepare()
        try engine.start()
    } catch {
        finish(false, "Could not start audio output: \(error.localizedDescription)")
    }

    let finishedPlaying = Locked(false)
    let remaining = Locked(buffers.count)
    for buffer in buffers {
        player.scheduleBuffer(buffer) {
            let done = remaining.withLock { count -> Bool in
                count -= 1
                return count == 0
            }
            if done { finishedPlaying.withLock { $0 = true } }
        }
    }

    player.play()
    // Same reason as above: keep the run loop alive while audio plays.
    let playDeadline = Date().addingTimeInterval(120)
    while !finishedPlaying.current && Date() < playDeadline {
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
    }
    guard finishedPlaying.current else {
        engine.stop()
        finish(false, "Playback timed out.")
    }

    player.stop()
    engine.stop()
    finish(true, nil)
}

// MARK: - Transcribing a file

/// Transcribe an audio file on-device.
///
/// Exists so transcription accuracy can be *measured* against a known
/// transcript rather than judged by ear. Live `listen` cannot do that: there is
/// no ground truth for what was spoken into a microphone.
///
/// It is also the path a recorded call would take, so this is not test-only
/// scaffolding.
///
/// `onDevice` in the result reports what was actually requested, not what we
/// hoped for. If the recogniser does not support on-device work for this
/// locale, the request would otherwise silently go to Apple's servers — which
/// for this project is the difference between the central claim holding and
/// not. Callers should check the flag rather than assume it.
func transcribe(path: String) {
    let started = Date()

    func finish(_ ok: Bool, _ text: String?, _ error: String?, onDevice: Bool) -> Never {
        emit(TranscribeResult(
            ok: ok, text: text, error: error, onDevice: onDevice,
            elapsedMs: Int(Date().timeIntervalSince(started) * 1000)))
        exit(ok ? 0 : 1)
    }

    let url = URL(fileURLWithPath: (path as NSString).expandingTildeInPath)
    guard FileManager.default.fileExists(atPath: url.path) else {
        finish(false, nil, "No such file: \(url.path)", onDevice: false)
    }

    // Deliberately does **not** request authorisation — it reports instead.
    //
    // Requesting is what crashes. macOS attributes a permission request to the
    // *responsible process* (the app that launched this one), and if that app
    // declares no usage description, TCC kills us with SIGABRT before any code
    // of ours runs. There is no way to catch it.
    //
    // So the rule is: asking for permission belongs in `macman setup`, where a
    // human is at the keyboard and can answer the dialog. A transcription in
    // the middle of a call must never trigger a prompt — it fails with
    // something the caller can act on.
    switch SFSpeechRecognizer.authorizationStatus() {
    case .authorized:
        break
    case .notDetermined:
        finish(false, nil,
               "Speech recognition permission has not been granted yet. "
               + "Run `macman setup` from Terminal to grant it.",
               onDevice: false)
    case .denied, .restricted:
        finish(false, nil,
               "Speech recognition access is denied. Enable it in System "
               + "Settings → Privacy & Security → Speech Recognition.",
               onDevice: false)
    @unknown default:
        finish(false, nil, "Speech recognition permission is in an unknown state.",
               onDevice: false)
    }

    guard let recogniser = SFSpeechRecognizer(locale: Locale(identifier: "en-US")),
          recogniser.isAvailable else {
        finish(false, nil, "Speech recogniser is unavailable.", onDevice: false)
    }

    let request = SFSpeechURLRecognitionRequest(url: url)
    request.shouldReportPartialResults = false
    let onDevice = recogniser.supportsOnDeviceRecognition
    request.requiresOnDeviceRecognition = onDevice

    struct Outcome {
        var text: String?
        var error: String?
        var done = false
    }
    let state = Locked(Outcome())

    recogniser.recognitionTask(with: request) { result, error in
        state.withLock { outcome in
            if outcome.done { return }
            if let error {
                outcome.error = error.localizedDescription
                outcome.done = true
            } else if let result, result.isFinal {
                outcome.text = result.bestTranscription.formattedString
                outcome.done = true
            }
        }
    }

    // Long files need time; the ceiling is generous because a timeout here
    // would be indistinguishable from a transcription failure.
    let deadline = Date().addingTimeInterval(180)
    while Date() < deadline {
        if state.withLock({ $0.done }) { break }
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
    }

    let outcome = state.withLock { $0 }
    if let error = outcome.error {
        finish(false, nil, error, onDevice: onDevice)
    }
    guard let text = outcome.text else {
        finish(false, nil, "Transcription timed out.", onDevice: onDevice)
    }
    // An empty transcript is reported as success with empty text: the
    // recogniser ran and heard nothing, which is a result, not an error.
    finish(true, text, nil, onDevice: onDevice)
}

// MARK: - Entry point

let args = Array(CommandLine.arguments.dropFirst())

switch args.first {
case "check", nil:
    emit(CheckResult(
        recogniserAvailable: SFSpeechRecognizer(
            locale: Locale(identifier: "en-US"))?.isAvailable ?? false,
        onDeviceSupported: SFSpeechRecognizer(
            locale: Locale(identifier: "en-US"))?.supportsOnDeviceRecognition ?? false,
        microphone: describeMic(),
        speechRecognition: describeSpeech(),
        outputDevices: outputDeviceNames()))

case "listen":
    listen(maxSeconds: Double(argument("--seconds", args) ?? "") ?? 20.0,
           silenceSeconds: Double(argument("--silence", args) ?? "") ?? 1.2)

case "say":
    say(text: argument("--text", args) ?? "", device: argument("--device", args))

case "transcribe":
    transcribe(path: argument("--file", args) ?? "")

case "authorise", "authorize":
    // The only command that may show a permission dialog. Run by `macman
    // setup`, from a terminal, with a human present to answer it.
    //
    // If the launching app declares no usage description, macOS terminates
    // this process rather than returning an error — so it is deliberately
    // isolated in its own command instead of being reached from `listen` or
    // `transcribe`, where a crash would look like a MACman fault mid-call.
    let (mic, speech) = requestPermissions()
    emit(CheckResult(
        recogniserAvailable: SFSpeechRecognizer(
            locale: Locale(identifier: "en-US"))?.isAvailable ?? false,
        onDeviceSupported: SFSpeechRecognizer(
            locale: Locale(identifier: "en-US"))?.supportsOnDeviceRecognition ?? false,
        microphone: describeMic(),
        speechRecognition: describeSpeech(),
        outputDevices: outputDeviceNames()))
    exit(mic && speech ? 0 : 1)

case let other?:
    FileHandle.standardError.write(Data("""
        macman-speech: unknown command '\(other)'
        usage: macman-speech [check|authorise|listen|say|transcribe]
                             [--text T] [--device D] [--seconds N] [--file PATH]

        """.utf8))
    exit(2)
}
