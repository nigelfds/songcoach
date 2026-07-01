// syscap — capture macOS system audio to an .m4a using ScreenCaptureKit.
//
//   syscap <output.m4a> [maxSeconds]
//
// Records until it receives SIGINT/SIGTERM (clean stop, finalises the file) or
// until maxSeconds elapses. Requires "Screen & System Audio Recording"
// permission (System Settings › Privacy & Security). No virtual audio device
// needed — this taps the system output directly via Core Audio / SCK.
//
// Build: swiftc -O syscap.swift -o syscap

import AVFoundation
import Foundation
import ScreenCaptureKit

func err(_ s: String) {
    FileHandle.standardError.write((s + "\n").data(using: .utf8)!)
}

final class Recorder: NSObject, SCStreamOutput, SCStreamDelegate {
    private let url: URL
    private var writer: AVAssetWriter?
    private var audioInput: AVAssetWriterInput?
    private var started = false
    private var stream: SCStream?
    private let sampleQueue = DispatchQueue(label: "syscap.samples")

    init(path: String) {
        self.url = URL(fileURLWithPath: path)
        try? FileManager.default.removeItem(at: url)
    }

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(
            false, onScreenWindowsOnly: false)
        guard let display = content.displays.first else {
            throw NSError(domain: "syscap", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "no display available"])
        }
        let filter = SCContentFilter(display: display,
                                     excludingApplications: [], exceptingWindows: [])

        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true
        config.sampleRate = 48_000
        config.channelCount = 2
        // SCK needs a video config even though we only consume audio; keep it tiny.
        config.width = 100
        config.height = 100
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1) // ~1 fps
        config.queueDepth = 6

        let writer = try AVAssetWriter(outputURL: url, fileType: .m4a)
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVSampleRateKey: 48_000,
            AVNumberOfChannelsKey: 2,
            AVEncoderBitRateKey: 256_000,
        ]
        let audioInput = AVAssetWriterInput(mediaType: .audio, outputSettings: settings)
        audioInput.expectsMediaDataInRealTime = true
        writer.add(audioInput)
        self.writer = writer
        self.audioInput = audioInput

        let stream = SCStream(filter: filter, configuration: config, delegate: self)
        try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: sampleQueue)
        try stream.addStreamOutput(self, type: .screen, sampleHandlerQueue: sampleQueue)
        self.stream = stream
        try await stream.startCapture()
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of type: SCStreamOutputType) {
        guard type == .audio, CMSampleBufferDataIsReady(sampleBuffer) else { return }
        guard let writer = writer, let audioInput = audioInput else { return }
        if !started {
            writer.startWriting()
            writer.startSession(atSourceTime: CMSampleBufferGetPresentationTimeStamp(sampleBuffer))
            started = true
        }
        if writer.status == .writing, audioInput.isReadyForMoreMediaData {
            audioInput.append(sampleBuffer)
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        err("stream stopped with error: \(error.localizedDescription)")
    }

    func stop() async {
        try? await stream?.stopCapture()
        audioInput?.markAsFinished()
        await writer?.finishWriting()
        err("saved \(url.path)")
    }
}

// ---- entry point --------------------------------------------------------
let args = CommandLine.arguments
guard args.count >= 2 else {
    err("usage: syscap <output.m4a> [maxSeconds]")
    exit(2)
}
let outputPath = args[1]
let maxSeconds: Double? = args.count >= 3 ? Double(args[2]) : nil

let recorder = Recorder(path: outputPath)

// Stop cleanly on SIGINT/SIGTERM so the .m4a is finalised.
var signalSources: [DispatchSourceSignal] = []
func installSignalHandler(_ sig: Int32) {
    signal(sig, SIG_IGN)
    let src = DispatchSource.makeSignalSource(signal: sig, queue: .global())
    src.setEventHandler { Task { await recorder.stop(); exit(0) } }
    src.resume()
    signalSources.append(src)
}
installSignalHandler(SIGINT)
installSignalHandler(SIGTERM)

do {
    try await recorder.start()
    err("recording…")
    if let maxSeconds = maxSeconds {
        try await Task.sleep(nanoseconds: UInt64(maxSeconds * 1_000_000_000))
        await recorder.stop()
        exit(0)
    }
    while true { try await Task.sleep(nanoseconds: 1_000_000_000) }
} catch {
    err("error: \(error.localizedDescription)")
    exit(1)
}
