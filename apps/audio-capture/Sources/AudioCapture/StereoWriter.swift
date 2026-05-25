import AVFoundation
import CoreMedia
import Foundation

@available(macOS 15.0, *)
final class StereoWriter: @unchecked Sendable {
    private let outputURL: URL
    private let outputFormat: AVAudioFormat
    private let micTargetFormat: AVAudioFormat
    private let writerQueue = DispatchQueue(label: "audio-capture.stereo-writer")

    // mic resampling state — touched only on the caller's mic queue
    private var micConverter: AVAudioConverter?

    // writer state — touched only on writerQueue
    private var audioFile: AVAudioFile?
    private var sysSamples: [Float] = []
    private var micSamples: [Float] = []
    private var sysFirstPTS: CMTime?
    private var micFirstPTS: CMTime?
    private var aligned = false

    init(outputURL: URL) {
        self.outputURL = outputURL
        self.outputFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: 48_000,
            channels: 2,
            interleaved: true)!
        self.micTargetFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: 48_000,
            channels: 1,
            interleaved: false)!
    }

    /// Called from the system-audio delegate queue. `samples` is a copy already.
    func appendSystem(samples: [Float], pts: CMTime) {
        writerQueue.async { [self] in
            if sysFirstPTS == nil { sysFirstPTS = pts }
            sysSamples.append(contentsOf: samples)
            tryFlush()
        }
    }

    /// Called from the mic delegate queue. Mic input may be at any sample rate;
    /// we resample to 48 kHz mono before queueing.
    func appendMic(buffer: AVAudioPCMBuffer, pts: CMTime) {
        if micConverter == nil {
            micConverter = AVAudioConverter(from: buffer.format, to: micTargetFormat)
        }
        guard let converter = micConverter else { return }

        let ratio = micTargetFormat.sampleRate / buffer.format.sampleRate
        let outCapacity = AVAudioFrameCount(ceil(Double(buffer.frameLength) * ratio) + 1024)
        guard let outBuf = AVAudioPCMBuffer(pcmFormat: micTargetFormat, frameCapacity: outCapacity) else { return }

        var error: NSError?
        var fed = false
        let status = converter.convert(to: outBuf, error: &error) { _, statusPtr in
            if fed {
                statusPtr.pointee = .noDataNow
                return nil
            }
            fed = true
            statusPtr.pointee = .haveData
            return buffer
        }
        if status == .error {
            print("mic resample error: \(error?.localizedDescription ?? "?")")
            return
        }
        guard let data = outBuf.floatChannelData else { return }
        let count = Int(outBuf.frameLength)
        let samples = Array(UnsafeBufferPointer(start: data[0], count: count))

        writerQueue.async { [self] in
            if micFirstPTS == nil { micFirstPTS = pts }
            micSamples.append(contentsOf: samples)
            tryFlush()
        }
    }

    /// Synchronous; drains any remaining buffered samples and closes the file.
    func finalize() {
        writerQueue.sync { [self] in
            tryFlush(forceDrain: true)
            audioFile = nil
        }
    }

    private func tryFlush(forceDrain: Bool = false) {
        if !aligned, let s = sysFirstPTS, let m = micFirstPTS {
            let dt = CMTimeGetSeconds(CMTimeSubtract(m, s))
            let frames = Int(round(dt * outputFormat.sampleRate))
            if frames > 0 {
                micSamples.insert(contentsOf: [Float](repeating: 0, count: frames), at: 0)
            } else if frames < 0 {
                sysSamples.insert(contentsOf: [Float](repeating: 0, count: -frames), at: 0)
            }
            aligned = true
            print(String(format: "stereo align: mic-vs-system Δ = %.1f ms (%d frames)", dt * 1000, frames))
        }
        guard aligned else {
            if forceDrain {
                print("stereo: only one source produced audio — nothing to mix")
            }
            return
        }

        let pairCount: Int
        if forceDrain {
            let target = max(sysSamples.count, micSamples.count)
            if sysSamples.count < target {
                sysSamples.append(contentsOf: [Float](repeating: 0, count: target - sysSamples.count))
            }
            if micSamples.count < target {
                micSamples.append(contentsOf: [Float](repeating: 0, count: target - micSamples.count))
            }
            pairCount = target
        } else {
            pairCount = min(sysSamples.count, micSamples.count)
        }
        if pairCount == 0 { return }

        if audioFile == nil {
            do {
                try? FileManager.default.removeItem(at: outputURL)
                audioFile = try AVAudioFile(
                    forWriting: outputURL,
                    settings: outputFormat.settings,
                    commonFormat: outputFormat.commonFormat,
                    interleaved: outputFormat.isInterleaved)
            } catch {
                print("failed to open stereo output: \(error)")
                return
            }
        }

        guard let outBuf = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: AVAudioFrameCount(pairCount)) else { return }
        outBuf.frameLength = AVAudioFrameCount(pairCount)
        guard let ptr = outBuf.floatChannelData?[0] else { return }
        for i in 0..<pairCount {
            ptr[i * 2] = sysSamples[i]
            ptr[i * 2 + 1] = micSamples[i]
        }

        do {
            try audioFile?.write(from: outBuf)
            sysSamples.removeFirst(pairCount)
            micSamples.removeFirst(pairCount)
        } catch {
            print("stereo write error: \(error)")
        }
    }
}
