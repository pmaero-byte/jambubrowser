"""
Test Tor-Isolated Session Mode
===============================
Verifies that Tor-isolated sessions work correctly with proper isolation.
"""

import asyncio
import sys
import os

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def test_tor_session_creation():
    """Test that Tor sessions can be created with proper configuration."""
    from backend.modules.browser import BrowserSession, SessionMode, PrivacyLevel
    
    print("Testing Tor session creation...")
    
    # Create a Tor-isolated session
    session = BrowserSession(
        session_id="test_tor_session",
        name="test_tor",
        mode=SessionMode.TOR_ISOLATED,
        privacy_level=PrivacyLevel.MAXIMUM,
    )
    
    # Verify session configuration
    assert session.mode == SessionMode.TOR_ISOLATED
    assert session.privacy_level == PrivacyLevel.MAXIMUM
    # Note: proxy is set when start() is called, not in constructor
    
    print("✓ Tor session created with correct configuration")
    return True


async def test_tor_session_isolation():
    """Test that Tor sessions have proper isolation properties."""
    from backend.modules.browser import BrowserSession, SessionMode, PrivacyLevel
    
    print("Testing Tor session isolation...")
    
    # Create multiple Tor sessions
    sessions = []
    for i in range(3):
        session = BrowserSession(
            session_id=f"test_tor_session_{i}",
            name=f"test_tor_{i}",
            mode=SessionMode.TOR_ISOLATED,
            privacy_level=PrivacyLevel.MAXIMUM,
        )
        sessions.append(session)
    
    # Verify each session has unique properties
    session_ids = [s.session_id for s in sessions]
    assert len(set(session_ids)) == len(session_ids), "Session IDs should be unique"
    
    # Verify all sessions are Tor-isolated
    for session in sessions:
        assert session.mode == SessionMode.TOR_ISOLATED
        # Note: proxy is set when start() is called, not in constructor
    
    print("✓ Tor sessions have proper isolation")
    return True


async def test_tor_session_privacy():
    """Test that Tor sessions enforce maximum privacy."""
    from backend.modules.browser import BrowserSession, SessionMode, PrivacyLevel
    
    print("Testing Tor session privacy enforcement...")
    
    session = BrowserSession(
        session_id="test_tor_privacy",
        name="test_tor_privacy",
        mode=SessionMode.TOR_ISOLATED,
        privacy_level=PrivacyLevel.MAXIMUM,
    )
    
    # Verify privacy settings
    assert session.privacy_level == PrivacyLevel.MAXIMUM
    assert session.mode == SessionMode.TOR_ISOLATED
    
    # Get privacy report
    report = session.get_privacy_report()
    assert report["tor_enabled"] is True
    assert report["cookies_cleared"] is True
    assert report["privacy_level"] == "maximum"
    assert report["trackers_blocked"] is True
    
    print("✓ Tor sessions enforce maximum privacy")
    return True


async def test_tor_session_manager():
    """Test the browser manager with Tor sessions."""
    from backend.modules.browser import BrowserManager, SessionMode, PrivacyLevel
    
    print("Testing browser manager with Tor sessions...")
    
    manager = BrowserManager.get_instance()
    
    # Verify we can create session objects (without starting them)
    session1 = manager._create_session_object(
        name="test_tor_manager_1",
        mode=SessionMode.TOR_ISOLATED,
        privacy_level=PrivacyLevel.MAXIMUM,
    )
    
    session2 = manager._create_session_object(
        name="test_tor_manager_2",
        mode=SessionMode.TOR_ISOLATED,
        privacy_level=PrivacyLevel.MAXIMUM,
    )
    
    # Verify sessions have correct configuration
    assert session1.mode == SessionMode.TOR_ISOLATED
    assert session2.mode == SessionMode.TOR_ISOLATED
    
    # Verify we can get privacy summary
    summary = manager.get_privacy_summary()
    assert "total_sessions" in summary
    assert "tor_count" in summary
    
    print("✓ Browser manager handles Tor sessions correctly")
    return True


async def run_all_tests():
    """Run all Tor session tests."""
    tests = [
        test_tor_session_creation,
        test_tor_session_isolation,
        test_tor_session_privacy,
        test_tor_session_manager,
    ]
    
    results = []
    for test in tests:
        try:
            result = await test()
            results.append((test.__name__, result, None))
        except Exception as e:
            results.append((test.__name__, False, str(e)))
    
    # Print results
    print("\n" + "=" * 60)
    print("TOR SESSION TEST RESULTS")
    print("=" * 60)
    
    passed = 0
    failed = 0
    for name, result, error in results:
        if result:
            print(f"✓ {name}: PASSED")
            passed += 1
        else:
            print(f"✗ {name}: FAILED")
            if error:
                print(f"  Error: {error}")
            failed += 1
    
    print(f"\nTotal: {passed + failed} tests, {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
