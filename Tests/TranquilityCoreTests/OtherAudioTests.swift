import XCTest
@testable import TranquilityCore

/// tb-media-aware (26 Sep 12:35): hands-free turns name-only while another app plays.
final class OtherAudioTests: XCTestCase {
    func testOnAfterTwoSecondsOfSoundOffAfterFourOfSilence() {
        var d = OtherAudio.Debounce()
        let t0 = Date(timeIntervalSince1970: 1000)
        XCTAssertFalse(d.update(true, now: t0))
        XCTAssertFalse(d.update(true, now: t0 + 1))
        XCTAssertTrue(d.update(true, now: t0 + 2), "two seconds of sound")
        XCTAssertTrue(d.on)
        XCTAssertFalse(d.update(false, now: t0 + 3))
        XCTAssertFalse(d.update(true, now: t0 + 4), "a gap under four seconds changes nothing")
        XCTAssertFalse(d.update(false, now: t0 + 5))
        XCTAssertFalse(d.update(false, now: t0 + 8))
        XCTAssertTrue(d.update(false, now: t0 + 9), "four seconds of silence")
        XCTAssertFalse(d.on)
    }

    func testAChimeIsNotMedia() {
        var d = OtherAudio.Debounce()
        let t0 = Date(timeIntervalSince1970: 1000)
        _ = d.update(true, now: t0)
        _ = d.update(true, now: t0 + 1)
        _ = d.update(false, now: t0 + 1.5)
        XCTAssertFalse(d.update(true, now: t0 + 2.5), "the run restarted after the break")
        XCTAssertFalse(d.on)
    }

    func testTheStateFileIsWhatTheVoiceReads() throws {
        let data = OtherAudio.stateJSON(playing: true, who: ["com.apple.WebKit.GPU"], at: Date(timeIntervalSince1970: 5))
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(object["playing"] as? Bool, true)
        XCTAssertEqual(object["t"] as? Double, 5)
    }

    func testOurOwnFamilyIsNeverOther() {
        let me = getpid()
        XCTAssertTrue(OtherAudio.family(of: me).contains(me))
        XCTAssertFalse(OtherAudio.players(excluding: OtherAudio.family(of: me)).contains { $0.pid == me })
    }
}
