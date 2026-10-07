import Foundation

@testable import AVSoundDemoCore

/// Result payloads in the shapes of PROTOCOL.md. Values are synthetic; recipe, feature,
/// validation and nonlexical values come from the engine (renderer 0.1.0).
enum Fixtures {
    /// A canonical 5-sample WAV `[0, 1, -1, 32767, -32768]` written by the engine's
    /// `wav_bytes`, with its `file_sha256` and `pcm_sha256` from the engine.
    static let smallWAVBase64 = "UklGRi4AAABXQVZFZm10IBAAAAABAAEAgLsAAAB3AQACABAAZGF0YQoAAAAAAAEA////fwCA"
    static let smallWAVFileSHA256 = "7a541ded0b5fab81c6c9e9bfb8d83f26491f22362a754a497d23ada20fd49e3d"
    static let smallWAVPCMSHA256 = "b9e94d97ecf4a49c39ef6481b1f8c35640ec3c9040053f25268b0140ae60c10b"
    static let smallWAVSamples: [Int16] = [0, 1, -1, 32767, -32768]

    static let recipeJSON =
        #"{"total_ms":600,"pitches":[-3,0,4],"rhythm_weights":[2,1,3],"gaps_ms":[40,20],"amplitudes":[1.0,0.6,0.8]}"#
    static let hash64 = String(repeating: "ab", count: 32)

    static let hello = #"""
        {"bridge_version":1,"renderer_version":"0.1.0",
         "renderer_hash":"03a99200e8546fc113d320d499a071159afa219d914b54e98c88449791fd151d",
         "renderer_recipe_schema_hash":"936cd8b5f2754535a67d88bf35102de9f2a03804c576d2076cfb67724a21acd1",
         "validator_version":"0.1.0","validator_hash":"\#(hash64)",
         "python":"3.11.15","numpy":"2.4.6","sample_rate":48000,"gap_samples":9600,
         "threshold":"0.1","threshold_float":0.1,
         "profiles":[{"id":"P1","f0_hz":300},{"id":"P2","f0_hz":450},{"id":"P3","f0_hz":675}],
         "domain":{"total_ms":[450,600,750,900],"pitches":[-6,-5,-4,-3,-2,-1,0,1,2,3,4,5,6],
                   "rhythm_weights":[1,2,3,4],"gaps_ms":[20,40,60],"amplitudes":[0.6,0.8,1.0]},
         "reason_codes":["E_JSON","E_SCHEMA","E_DOMAIN","E_EVENT_SHORT","E_NONFINITE","E_CLIP",
                         "E_DUPLICATE","E_RESERVED","E_SEPARATION"],
         "feature_names":["pitch_1","pitch_2","pitch_3","total","proportion_1","proportion_2",
                          "proportion_3","gap_1","gap_2","amplitude_1","amplitude_2","amplitude_3"]}
        """#

    static let selfTest = #"{"ok":true,"message":"2 reference vectors reproduced"}"#

    static let render = #"""
        {"recipe":\#(recipeJSON),
         "recipe_sha256":"224775bc696fa696ae2a43cc27d2919e8089ca43517022db902305274df7dd61",
         "profile":"P2","n_samples":28800,"duration_ms":600.0,
         "event_samples":[8640,4320,12960],"event_onsets":[0,10560,15840],"gap_samples":[1920,960],
         "peak":13865,"peak_dbfs":-7.47033614455637,"rms":7336.001157386806,
         "short_event":false,"overflow":false,"nonfinite":false,"renderer_version":"0.1.0",
         "wav_b64":"\#(smallWAVBase64)","file_sha256":"\#(smallWAVFileSHA256)",
         "pcm_sha256":"\#(smallWAVPCMSHA256)"}
        """#

    /// An overflowing render: no audio, and the non-finite token Python writes for -inf.
    static let renderOverflow = #"""
        {"recipe":\#(recipeJSON),"recipe_sha256":"\#(hash64)",
         "profile":"P3","n_samples":28800,"duration_ms":600,
         "event_samples":[8640,4320,12960],"event_onsets":[0,10560,15840],"gap_samples":[1920,960],
         "peak":40000,"peak_dbfs":-Infinity,"rms":7336.0,
         "short_event":false,"overflow":true,"nonfinite":false,"renderer_version":"0.1.0",
         "wav_b64":null,"file_sha256":null,"pcm_sha256":null}
        """#

    static let randomRecipe = #"{"recipe":\#(recipeJSON)}"#

    static let features = #"""
        {"names":["pitch_1","pitch_2","pitch_3","total","proportion_1","proportion_2","proportion_3",
                  "gap_1","gap_2","amplitude_1","amplitude_2","amplitude_3"],
         "exact":["0.25","0.5","5/6","1/3","0.4","0.1","0.7","0.5","0","1","0","0.5"],
         "values":[0.25,0.5,0.8333333333333334,0.3333333333333333,0.4,0.1,0.7,0.5,0.0,1.0,0.0,0.5]}
        """#

    static let distance = #"{"distance":0.4279581149460168,"sum_sq":"989/450","separated":true}"#

    /// The engine's `ValidationResult.to_dict()` for the README's short-event example.
    static let validationRejected = #"""
        {"result_version": 1, "ok": false, "codes": ["E_EVENT_SHORT"],
         "messages": ["event 1 is 1760 samples (36.7 ms); minimum 2880 samples (60.0 ms)"],
         "profile": "P2", "threshold": "0.1",
         "recipe": {"total_ms": 450, "pitches": [0, 0, 0], "rhythm_weights": [1, 4, 4], "gaps_ms": [60, 60], "amplitudes": [1.0, 0.8, 0.6]},
         "recipe_sha256": "ae7fda76a7f93c88ec5764b6cfd1a6dda547150656dfe282b49c7ceb37977bee",
         "features": ["1/2", "1/2", "1/2", "0", "0", "3/5", "3/5", "1", "1", "1", "1/2", "0"],
         "event_samples": [1760, 7040, 7040],
         "pcm_sha256": "a88b842cf40b930e0f3859470f8911a746d24895941eb4111dd17b3964dfbe51",
         "nearest_id": "K-a1", "nearest_index": 0, "nearest_distance": 0.4522833019084224,
         "validator_version": "0.1.0", "renderer_version": "0.1.0"}
        """#

    /// The engine's result for text that is not JSON: only `E_JSON`, every other field null.
    static let validationBadJSON = #"""
        {"result_version": 1, "ok": false, "codes": ["E_JSON"],
         "messages": ["invalid JSON: Expecting ',' delimiter: line 1 column 16 (char 15)"],
         "profile": "P2", "threshold": "0.1", "recipe": null, "recipe_sha256": null, "features": null,
         "event_samples": null, "pcm_sha256": null, "nearest_id": null, "nearest_index": null,
         "nearest_distance": null, "validator_version": "0.1.0", "renderer_version": "0.1.0"}
        """#

    static let nearest = #"{"ref_id":"K-a1","index":0,"distance":0.4522833019084224,"sum_sq":"491/200"}"#

    static let grammar = #"""
        {"families":["K","Q"],
         "atom_ids":["K-a1","K-a2","K-a3","K-a4","K-r1","K-r2","K-r3","K-r4",
                     "Q-a1","Q-a2","Q-a3","Q-a4","Q-r1","Q-r2","Q-r3","Q-r4"],
         "messages":[
           {"message_id":"K-a1-r1","family":"K","action":"K-a1","referent":"K-r1","status":"Train V1",
            "training_wave":1,"heldout_set":null,"is_heldout":false},
           {"message_id":"K-a1-r2","family":"K","action":"K-a1","referent":"K-r2","status":"H-V1",
            "training_wave":null,"heldout_set":"H-V1","is_heldout":true},
           {"message_id":"K-a1-r3","family":"K","action":"K-a1","referent":"K-r3","status":"Train V2",
            "training_wave":2,"heldout_set":null,"is_heldout":false}]}
        """#

    static let compose = #"""
        {"message_id":"K-a1-r1","n_samples":52800,"duration_s":1.1,"action_samples":21600,
         "referent_samples":21600,"referent_onset":31200,
         "wav_b64":"\#(smallWAVBase64)","file_sha256":"\#(smallWAVFileSHA256)",
         "pcm_sha256":"\#(smallWAVPCMSHA256)"}
        """#

    static let compositeHash = #"""
        {"message_id":"K-a1-r2","composite_sha256":"\#(hash64)","n_samples":52800,"duration_s":1.1,
         "is_heldout":true}
        """#

    static let syntheticBook = #"""
        {"book_id":"DEMO-P1","atoms":[
          {"atom_id":"K-a1","recipe":{"amplitudes":[1.0,0.8,0.6],"gaps_ms":[20,40],"pitches":[-6,0,5],
           "rhythm_weights":[1,2,3],"total_ms":450},
           "pcm_sha256":"909e93b54b06d45a69d70afa0a57b86344acd67556a35659a1dfa3a0c3a74db6","n_samples":21600}]}
        """#

    static let calibrationAsset = #"""
        "id":"calibration-P1","kind":"calibration","profile":"P1","n_samples":96000,"duration_ms":2000.0,
        "peak_dbfs":-9.944713172908152,"rms_dbfs":-12.99954946019891,"active_rms_dbfs":-12.99954946019891,
        "pcm_sha256":"e50e47627a73137392e2cfdd0dac2253adda3c8b10aea49174c9506c3fb90211",
        "file_sha256":"f5a1a6d526bb5685e8a26217c5313e0afd18c3824c9adee8643fe99e2af8bd90",
        "description":"Calibration example for P1: 2.000 s steady tone at f0 = 300 Hz."
        """#

    static let nonlexicalList = #"""
        {"assets":[{\#(calibrationAsset)},
          {"id":"ready-cue","kind":"ready_cue","profile":null,"n_samples":15360,"duration_ms":320.0,
           "peak_dbfs":-6.0,"rms_dbfs":-14.5,"active_rms_dbfs":-12.0,"pcm_sha256":"\#(hash64)",
           "file_sha256":"\#(hash64)","description":"READY cue."}]}
        """#

    static let nonlexicalGet = #"""
        {\#(calibrationAsset),"wav_b64":"\#(smallWAVBase64)"}
        """#

    static let vectorsCheck = #"""
        {"renderer":{"checked":21,"mismatches":[]},
         "composition":{"checked":48,"mismatches":[{"name":"K-a1","field":"pcm_sha256"}]},"ok":false}
        """#

    static let goldenCheck = #"{"items":117,"mismatches":[],"ok":true,"digest":"\#(hash64)"}"#

    /// Variant: `items` as a list and `digest` as an object of digests.
    static let goldenCheckVariant = #"""
        {"items":[{"id":"recipe/a/P1"},{"id":"recipe/a/P2"}],"mismatches":[],"ok":true,
         "digest":{"all":"\#(hash64)","recipe":"\#(hash64)"}}
        """#

    static let storeReset = #"{"root":"/tmp/av-sound-demo-store"}"#
    static let storeCreate = #"{"book_id":"DEMO-STORE-1","chain_head":"\#(hash64)"}"#

    static let storeEntry = #"""
        {"atom_id":"K-a1","commit_index":0,"pcm_sha256":"\#(hash64)","file_sha256":"\#(hash64)",
         "recipe":\#(recipeJSON)}
        """#

    static let storeCommit = #"{"entry":\#(storeEntry),"chain_head":"\#(hash64)","outcome":"recommit_noop"}"#
    static let storeList = #"{"entries":[\#(storeEntry)],"chain_head":"\#(hash64)","frozen":true,"void":false}"#

    static let storeRecords = #"""
        {"records":[{"record_version":1,"seq":0,"prev_sha256":"\#(String(repeating: "0", count: 64))",
          "record_sha256":"\#(hash64)","event":"create_book","book_id":"DEMO-STORE-1",
          "timestamp":"2026-01-01T00:00:00.000Z","profile":"P1","kind":"synthetic","threshold":"0.1"}]}
        """#

    static let storeFreeze = #"{"chain_head":"\#(hash64)"}"#

    static let storeVerify = #"""
        {"ok":false,"issues":[{"code":"E_BLOB_HASH","line":3,"message":"blob does not match its hash"},
                              {"code":"E_CHAIN","line":null,"message":"chain head differs"},
                              {"code":"E_TRUNCATED","seq":7,"message":"log ends early"}]}
        """#

    static let storeTamper = #"{"done":"flipped one byte of a blob"}"#

    static let fallbackDemo = #"""
        {"seed_label":"DEMO-fallback-v1","fallback_bank_hash":"\#(hash64)",
         "bank":[{"index":0,"recipe":\#(recipeJSON),"pcm_sha256":"\#(hash64)"}],
         "book":[{"atom_id":"K-a1","recipe":\#(recipeJSON),"pcm_sha256":"\#(hash64)"}]}
        """#

    static let fallbackScan = #"""
        {"scan_version":1,"profile":"P1","bank_sha256":"\#(hash64)","bank_threshold":"0.1","threshold":"0.1",
         "reserved_sha256":"\#(hash64)","validator_version":"0.1.0","renderer_version":"0.1.0",
         "reference_ids":["K-a1","K-a2"],"references_sha256":"\#(hash64)","used":[0],
         "outcome":"selected","selected_index":2,"selected_source":"fallback-bank-P1-02",
         "selected_recipe_sha256":"\#(hash64)","selected_pcm_sha256":"\#(hash64)",
         "log":[{"index":0,"recipe_sha256":"\#(hash64)","pcm_sha256":"\#(hash64)","outcome":"used","codes":[],"messages":[]},
                {"index":1,"recipe_sha256":"\#(hash64)","pcm_sha256":"\#(hash64)","outcome":"rejected",
                 "codes":["E_SEPARATION"],"messages":["distance below 0.1 to K-a1 at 0.050000"]},
                {"index":2,"recipe_sha256":"\#(hash64)","pcm_sha256":"\#(hash64)","outcome":"selected","codes":[],"messages":[]}]}
        """#

    static let packageDemo = #"""
        {"package_sha256":"\#(hash64)",
         "files":[{"path":"manifest.json","sha256":"\#(hash64)","bytes":2048},
                  {"path":"atoms/K-a1.wav","sha256":"\#(hash64)","bytes":43244}],
         "counts":{"atom_wavs":16,"message_wavs":18,"heldout_ids":14},"loader_ok":true,
         "leak_report":{"ok":true,"files_scanned":40,"heldout_hashes":14,"heldout_audio":0,"method_strings":0,"findings":[]},
         "answers_preview":[{"message_id":"K-a1-r1","action":"ADD_ONE","referent":"A"}],
         "dir":"/tmp/av-sound-demo-package"}
        """#

    static let empty = "{}"

    /// Wraps a result in a success envelope.
    static func success(id: Int = 1, _ result: String) -> Data {
        Data(#"{"id":\#(id),"ok":true,"result":\#(result)}"#.utf8)
    }

    static func decode<T: Decodable>(_ type: T.Type, _ result: String, cmd: String = "test") throws -> T {
        try BridgeCoding.decodeResult(T.self, from: success(result), cmd: cmd)
    }
}
