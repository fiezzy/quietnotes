import ArgumentParser
import Foundation
import ScreenCaptureKit

@main
struct AudioCaptureCommand: AsyncParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "audio-capture",
        abstract: "Capture system audio + microphone to a stereo WAV file (L=system, R=mic, 48kHz)."
    )

    @Option(name: .shortAndLong, help: "Output .wav file path.")
    var output: String

    @Option(name: .shortAndLong, help: "Recording duration in seconds. Omit to record until Ctrl+C.")
    var duration: Double?

    func run() async throws {
        guard #available(macOS 15.0, *) else {
            throw ValidationError("macOS 15.0 or later required (running on \(ProcessInfo.processInfo.operatingSystemVersionString)).")
        }

        let outputURL = URL(fileURLWithPath: (output as NSString).expandingTildeInPath)
        try? FileManager.default.removeItem(at: outputURL)

        print("audio-capture: requesting shareable content...")
        let content = try await SCShareableContent.current
        guard let display = content.displays.first else {
            throw ValidationError("No displays available.")
        }
        print("Display \(display.displayID) (\(display.width)x\(display.height)) selected.")

        let session = CaptureSession(outputURL: outputURL, display: display)
        try await session.start()
        print("Recording → \(outputURL.path)")

        if let duration {
            print("Duration: \(duration)s")
            try await Task.sleep(nanoseconds: UInt64(duration * 1_000_000_000))
        } else {
            print("Press Ctrl+C to stop.")
            signal(SIGINT, SIG_IGN)
            let signalSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
            await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
                signalSource.setEventHandler {
                    continuation.resume()
                }
                signalSource.resume()
            }
            signalSource.cancel()
        }

        try await session.stop()

        let size = (try? FileManager.default.attributesOfItem(atPath: outputURL.path)[.size] as? UInt64) ?? 0
        print(String(format: "Done. Wrote stereo %@ (%.1f KB)", outputURL.path, Double(size) / 1024))
    }
}
