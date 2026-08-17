// macman-audio — tap one app's audio without touching the rest of the system.
//
//   macman-audio check                  → tap support + audio processes
//   macman-audio tap --app FaceTime     → stream that app's audio as raw PCM
//   macman-audio tap --pid 1234 --seconds 5
//
// This is v3's downlink: hearing the person on a FaceTime call.
//
// ## Why a process tap and not a virtual device
//
// The obvious approach — the one the project this is modelled on used — is to
// set the system output to a virtual device and read from it. That works, and
// it also silences your speakers and captures every other app on the machine.
//
// Core Audio process taps (macOS 14.4+) capture a *single* process. FaceTime's
// audio is tapped; music, notifications and everything else keep playing to
// your speakers untouched. That is strictly better, and it is the only reason
// MACman needs one virtual device (BlackHole, for the uplink) rather than two.
//
// PCM is written to stdout so Python can pipe it straight into transcription;
// status stays on stderr so the two never mix.

import AppKit
import AVFoundation
import CoreAudio
import Foundation

// MARK: - Wire format

struct AudioProcess: Codable {
    var pid: Int32
    var name: String
}

struct CheckResult: Codable {
    var tapSupported: Bool
    var detail: String
    var processes: [AudioProcess]
}

func emitJSON<T: Encodable>(_ value: T) {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    if let data = try? encoder.encode(value),
       let text = String(data: data, encoding: .utf8) {
        print(text)
        fflush(stdout)
    }
}

func note(_ message: String) {
    FileHandle.standardError.write(Data((message + "\n").utf8))
}

func argument(_ name: String, _ args: [String]) -> String? {
    guard let index = args.firstIndex(of: name), index + 1 < args.count else { return nil }
    return args[index + 1]
}

// MARK: - Discovering audio processes

/// Every process CoreAudio knows about, with a usable name.
///
/// Reported so Python can tell "FaceTime isn't running" from "the tap failed",
/// which are very different problems for the caller.
func audioProcesses() -> [AudioProcess] {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyProcessObjectList,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)

    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(
        AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size) == noErr
    else { return [] }

    var objects = [AudioObjectID](repeating: 0,
                                  count: Int(size) / MemoryLayout<AudioObjectID>.size)
    guard AudioObjectGetPropertyData(
        AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &objects) == noErr
    else { return [] }

    return objects.compactMap { object -> AudioProcess? in
        var pidAddress = AudioObjectPropertyAddress(
            mSelector: kAudioProcessPropertyPID,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        var pid: pid_t = 0
        var pidSize = UInt32(MemoryLayout<pid_t>.size)
        guard AudioObjectGetPropertyData(
            object, &pidAddress, 0, nil, &pidSize, &pid) == noErr, pid > 0
        else { return nil }

        let name = NSRunningApplication(processIdentifier: pid)?.localizedName
            ?? ProcessInfo.processInfo.processName
        return AudioProcess(pid: pid, name: name)
    }
}

func processObject(forPID pid: pid_t) -> AudioObjectID? {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyTranslatePIDToProcessObject,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)

    var input = pid
    var object = AudioObjectID(kAudioObjectUnknown)
    var size = UInt32(MemoryLayout<AudioObjectID>.size)

    let status = AudioObjectGetPropertyData(
        AudioObjectID(kAudioObjectSystemObject), &address,
        UInt32(MemoryLayout<pid_t>.size), &input, &size, &object)
    return (status == noErr && object != kAudioObjectUnknown) ? object : nil
}

func pid(forApp name: String) -> pid_t? {
    NSWorkspace.shared.runningApplications.first {
        ($0.localizedName ?? "").localizedCaseInsensitiveContains(name)
    }?.processIdentifier
}

// MARK: - Tapping

/// Capture one process's audio for a while, writing raw PCM to stdout.
///
/// A tap on its own produces nothing — it has to be wrapped in an aggregate
/// device, which is what actually delivers buffers through an IO proc.
func tap(pid targetPID: pid_t, seconds: Double) -> Never {
    guard let object = processObject(forPID: targetPID) else {
        note("No audio process for pid \(targetPID). Is it running and producing audio?")
        exit(1)
    }

    let description = CATapDescription(stereoMixdownOfProcesses: [object])
    description.name = "MACman tap \(targetPID)"
    description.isPrivate = true          // don't advertise it system-wide
    description.muteBehavior = .unmuted   // the user must keep hearing the call

    var tapID = AudioObjectID(kAudioObjectUnknown)
    let tapStatus = AudioHardwareCreateProcessTap(description, &tapID)
    guard tapStatus == noErr, tapID != kAudioObjectUnknown else {
        note("Could not create the tap (OSStatus \(tapStatus)). "
             + "This usually means audio-recording permission is missing.")
        exit(1)
    }
    defer { AudioHardwareDestroyProcessTap(tapID) }

    // The aggregate device is what the IO proc reads from; the tap alone is
    // only a description of what to capture.
    let uid = "com.macman.tap.\(targetPID).\(UUID().uuidString)"
    let aggregate: [String: Any] = [
        kAudioAggregateDeviceNameKey as String: "MACman Tap",
        kAudioAggregateDeviceUIDKey as String: uid,
        kAudioAggregateDeviceIsPrivateKey as String: true,
        kAudioAggregateDeviceIsStackedKey as String: false,
        kAudioAggregateDeviceTapAutoStartKey as String: true,
        kAudioAggregateDeviceSubDeviceListKey as String: [],
        kAudioAggregateDeviceTapListKey as String: [[
            kAudioSubTapUIDKey as String: description.uuid.uuidString,
            kAudioSubTapDriftCompensationKey as String: true,
        ]],
    ]

    var deviceID = AudioObjectID(kAudioObjectUnknown)
    let deviceStatus = AudioHardwareCreateAggregateDevice(
        aggregate as CFDictionary, &deviceID)
    guard deviceStatus == noErr, deviceID != kAudioObjectUnknown else {
        note("Could not create the aggregate device (OSStatus \(deviceStatus)).")
        exit(1)
    }
    defer { AudioHardwareDestroyAggregateDevice(deviceID) }

    // Read the tap's real format rather than assuming one.
    var formatAddress = AudioObjectPropertyAddress(
        mSelector: kAudioTapPropertyFormat,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var asbd = AudioStreamBasicDescription()
    var asbdSize = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
    guard AudioObjectGetPropertyData(
        tapID, &formatAddress, 0, nil, &asbdSize, &asbd) == noErr else {
        note("Could not read the tap's audio format.")
        exit(1)
    }

    note("tap ready: \(asbd.mSampleRate)Hz, \(asbd.mChannelsPerFrame)ch")

    let bytesWritten = Locked(0)
    var procID: AudioDeviceIOProcID?
    let status = AudioDeviceCreateIOProcIDWithBlock(&procID, deviceID, nil) {
        _, inputData, _, _, _ in
        let buffers = UnsafeMutableAudioBufferListPointer(
            UnsafeMutablePointer(mutating: inputData))
        for buffer in buffers {
            guard let data = buffer.mData, buffer.mDataByteSize > 0 else { continue }
            FileHandle.standardOutput.write(
                Data(bytes: data, count: Int(buffer.mDataByteSize)))
            bytesWritten.withLock { $0 += Int(buffer.mDataByteSize) }
        }
    }
    guard status == noErr, let procID else {
        note("Could not create the IO proc (OSStatus \(status)).")
        exit(1)
    }
    defer {
        AudioDeviceStop(deviceID, procID)
        AudioDeviceDestroyIOProcID(deviceID, procID)
    }

    guard AudioDeviceStart(deviceID, procID) == noErr else {
        note("Could not start the device.")
        exit(1)
    }

    let deadline = Date().addingTimeInterval(seconds)
    while Date() < deadline {
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
    }

    note("captured \(bytesWritten.current) bytes")
    exit(bytesWritten.current > 0 ? 0 : 2)   // 2 = tap worked but heard silence
}

/// Small lock wrapper — the IO proc runs on a realtime thread.
final class Locked<Value>: @unchecked Sendable {
    private var value: Value
    private let lock = NSLock()
    init(_ value: Value) { self.value = value }
    func withLock<R>(_ body: (inout Value) -> R) -> R {
        lock.lock(); defer { lock.unlock() }
        return body(&value)
    }
    var current: Value { withLock { $0 } }
}

// MARK: - Entry point

let args = Array(CommandLine.arguments.dropFirst())

switch args.first {
case "check", nil:
    // Creating and immediately destroying a tap on ourselves is the only
    // honest way to know whether tapping is permitted on this machine.
    var probe = AudioObjectID(kAudioObjectUnknown)
    var supported = false
    var detail = "process taps unavailable"
    if let selfObject = processObject(forPID: ProcessInfo.processInfo.processIdentifier) {
        let description = CATapDescription(stereoMixdownOfProcesses: [selfObject])
        description.isPrivate = true
        if AudioHardwareCreateProcessTap(description, &probe) == noErr {
            supported = true
            detail = "ready"
            AudioHardwareDestroyProcessTap(probe)
        } else {
            detail = "tap creation refused — audio recording permission may be missing"
        }
    } else {
        detail = "could not resolve this process in CoreAudio"
    }
    emitJSON(CheckResult(tapSupported: supported, detail: detail,
                         processes: audioProcesses()))

case "tap":
    let seconds = Double(argument("--seconds", args) ?? "") ?? 5.0
    if let raw = argument("--pid", args), let value = pid_t(raw) {
        tap(pid: value, seconds: seconds)
    } else if let app = argument("--app", args) {
        guard let found = pid(forApp: app) else {
            note("\(app) does not appear to be running.")
            exit(1)
        }
        tap(pid: found, seconds: seconds)
    } else {
        note("tap needs --app NAME or --pid N")
        exit(2)
    }

case let other?:
    note("macman-audio: unknown command '\(other)'")
    note("usage: macman-audio [check|tap] [--app NAME|--pid N] [--seconds N]")
    exit(2)
}
