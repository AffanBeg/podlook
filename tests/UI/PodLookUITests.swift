import XCTest

final class PodLookUITests: XCTestCase {
    func testSetupValidationAndUnavailableHeadphones() {
        let app = XCUIApplication()
        app.launchArguments = ["--uitest-reset"]
        app.launch()
        XCTAssertTrue(app.navigationBars["PodLook"].waitForExistence(timeout: 10))

        let start = app.buttons["startStop"]
        start.tap()
        XCTAssertTrue(app.staticTexts["Enter the PC's IPv4 address, for example 192.168.1.20."].exists)

        let address = app.textFields["pcAddress"]
        address.tap()
        address.typeText("127.0.0.1")
        dismissKeyboard(app)
        let port = app.textFields["pcPort"]
        port.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.5)).tap()
        port.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: 4) + "0")
        dismissKeyboard(app)
        start.tap()
        XCTAssertTrue(app.staticTexts["Enter a port from 1 to 65535. OpenTrack normally uses 4242."].exists)

        port.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.5)).tap()
        port.typeText(XCUIKeyboardKey.delete.rawValue + "4242")
        dismissKeyboard(app)
        start.tap()
        // Simulators have no AirPods motion hardware. This tests graceful refusal,
        // not sensor availability or network delivery on a physical iPhone.
        XCTAssertTrue(app.staticTexts["No motion-capable headphones available. Connect and wear your AirPods, then try again."].waitForExistence(timeout: 5))
        XCTAssertEqual(start.label, "Start tracking")
    }

    private func dismissKeyboard(_ app: XCUIApplication) {
        let done = app.buttons["dismissKeyboard"]
        XCTAssertTrue(done.waitForExistence(timeout: 3))
        done.tap()
    }
}
