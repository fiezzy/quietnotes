import AVFoundation
import CoreMedia
import Foundation
import ScreenCaptureKit

@available(macOS 15.0, *)
final class CaptureSession: NSObject, SCStreamOutput, SCStreamDelegate, @unchecked Sendable {
    private let outputURL: URL
    private let display: SCDisplay
    private let stereoWriter: StereoWriter

    private var stream: SCStream?
    private let systemQueue = DispatchQueue(label: "audio-capture.system")
    private let micQueue = DispatchQueue(label: "audio-capture.mic")

    private var didLogSystemFormat = false
    private var didLogMicFormat = false

    init(outputURL: URL, display: SCDisplay) {
        self.outputURL = outputURL
        self.display = display
        self.stereoWriter = StereoWriter(outputURL: outputURL)
        super.init()
    }

    func start() async throws {
        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true
        config.sampleRate = 48_000
        config.channelCount = 1

        config.captureMicrophone = true
        config.microphoneCaptureDeviceID = nil

        config.width = 2
        config.height = 2
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)

        let filter = SCContentFilter(display: display, excludingWindows: [])
        let stream = SCStream(filter: filter, configuration: config, delegate: self)
        try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: systemQueue)
        try stream.addStreamOutput(self, type: .microphone, sampleHandlerQueue: micQueue)

        try await stream.startCapture()
        self.stream = stream
    }

    func stop() async throws {
        if let stream {
            try await stream.stopCapture()
        }
        stream = nil
        stereoWriter.finalize()
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard sampleBuffer.isValid else { return }
        guard var asbd = sampleBuffer.formatDescription?.audioStreamBasicDescription else { return }
        guard let format = AVAudioFormat(streamDescription: &asbd) else { return }
        let pts = sampleBuffer.presentationTimeStamp

        do {
            try sampleBuffer.withAudioBufferList { audioBufferList, _ in
                guard let pcmBuffer = AVAudioPCMBuffer(pcmFormat: format, bufferListNoCopy: audioBufferList.unsafePointer) else {
                    return
                }
                switch type {
                case .audio:
                    if !didLogSystemFormat {
                        didLogSystemFormat = true
                        print(String(format: "system audio: %.0f Hz, %u ch, %@interleaved",
                                     format.sampleRate, format.channelCount,
                                     format.isInterleaved ? "" : "non-"))
                    }
                    guard let data = pcmBuffer.floatChannelData else { return }
                    let count = Int(pcmBuffer.frameLength)
                    let samples = Array(UnsafeBufferPointer(start: data[0], count: count))
                    stereoWriter.appendSystem(samples: samples, pts: pts)
                case .microphone:
                    if !didLogMicFormat {
                        didLogMicFormat = true
                        print(String(format: "mic audio:    %.0f Hz, %u ch, %@interleaved",
                                     format.sampleRate, format.channelCount,
                                     format.isInterleaved ? "" : "non-"))
                    }
                    stereoWriter.appendMic(buffer: pcmBuffer, pts: pts)
                default:
                    return
                }
            }
        } catch {
            print("audio buffer error: \(error)")
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        print("stream stopped with error: \(error)")
    }
}
