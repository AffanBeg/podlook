import XCTest

final class PodLookUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }
    func testSetupValidationAndUnavailableHeadphones() {
        let app = XCUIApplication()
        app.launchArguments = ["--uitest-reset"]
        app.launch()
        XCTAssertTrue(app.navigationBars["PodLook"].waitForExistence(timeout: 10))
        XCTAssertEqual(app.textFields["pcPort"].value as? String, "4243")

        let start = app.buttons["startStop"]
        start.tap()
        XCTAssertTrue(app.staticTexts["Enter the PC's IPv4 address, for example 192.168.1.20."].exists)

        let address = app.textFields["pcAddress"]
        address.tap()
        address.typeText("127.0.0.1")
        dismissKeyboard(app)
        let port = app.textFields["pcPort"]
        port.tap()
        port.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: 4) + "0")
        dismissKeyboard(app)
        start.tap()
        XCTAssertTrue(app.staticTexts["Enter a port from 1 to 65535. Windows PodLook normally uses 4243."].exists)

        port.tap()
        port.typeText(XCUIKeyboardKey.delete.rawValue + "4242")
        dismissKeyboard(app)
        start.tap()
        // Simulators have no AirPods motion hardware. This tests graceful refusal,
        // not sensor availability or network delivery on a physical iPhone.
        XCTAssertTrue(app.staticTexts["No motion-capable headphones available. Connect and wear your AirPods, then try again."].waitForExistence(timeout: 5))
        XCTAssertEqual(start.label, "Start tracking")

        // No hardware was started on the simulator. The new recovery control
        // must not claim sensor samples or a PC connection in this state.
        let restart = app.buttons["restartMotion"]
        revealBelow(restart, in: app)
        XCTAssertFalse(restart.isEnabled)
        let samples = app.staticTexts["receivedSamples"]
        revealBelow(samples, in: app)
        XCTAssertEqual(samples.label, "0 sensor samples received")
        let network = app.staticTexts["networkStatus"]
        revealBelow(network, in: app)
        XCTAssertEqual(network.label, "PC connection stopped.")
        let diagnostics = app.staticTexts["streamDiagnostics"]
        revealBelow(diagnostics, in: app)
        XCTAssertFalse(diagnostics.label.isEmpty)
    }

    private func revealBelow(_ element: XCUIElement, in app: XCUIApplication) {
        for _ in 0..<4 {
            if element.exists && element.isHittable { return }
            app.swipeUp()
        }
        XCTAssertTrue(element.exists && element.isHittable, "Expected tracking diagnostic/control to be reachable")
    }

    private func dismissKeyboard(_ app: XCUIApplication) {
        let done = app.buttons["dismissKeyboard"]
        XCTAssertTrue(done.waitForExistence(timeout: 3))
        done.tap()
    }
}
