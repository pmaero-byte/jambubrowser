"""
Comprehensive Backend Tests
==========================
Tests for all major backend components and endpoints.
"""

import pytest
import asyncio
import json
import os
import sys
from unittest.mock import Mock, patch, AsyncMock
from datetime import datetime

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestPrivacyModule:
    """Tests for privacy module."""
    
    def test_privacy_mode_enum(self):
        from backend.core.privacy import PrivacyMode
        assert PrivacyMode.STANDARD.value == "standard"
        assert PrivacyMode.ENHANCED.value == "enhanced"
        assert PrivacyMode.MAXIMUM.value == "maximum"
    
    def test_privacy_manager_singleton(self):
        from backend.core.privacy import get_privacy_manager
        manager1 = get_privacy_manager()
        manager2 = get_privacy_manager()
        assert manager1 is manager2
    
    def test_pii_detection(self):
        from backend.core.privacy import PIIDetector
        
        # Test email detection
        findings = PIIDetector.detect_pii("Contact me at john@example.com")
        assert "email" in findings
        
        # Test no PII
        findings = PIIDetector.detect_pii("This is normal text")
        assert len(findings) == 0
    
    def test_content_sanitization(self):
        from backend.core.privacy import sanitize_content_for_storage
        text = "Email: john@example.com, Phone: 555-1234"
        sanitized = sanitize_content_for_storage(text)
        assert "john@example.com" not in sanitized


class TestAuditModule:
    """Tests for audit module."""
    
    def test_audit_logger_singleton(self):
        from backend.core.audit import get_audit_logger
        logger1 = get_audit_logger()
        logger2 = get_audit_logger()
        assert logger1 is logger2
    
    def test_audit_log_entry(self):
        from backend.core.audit import get_audit_logger, ActionCategory
        logger = get_audit_logger()
        
        entry_id = logger.log(
            category=ActionCategory.SYSTEM,
            action="test_action",
            details={"test": "data"}
        )
        assert entry_id is not None
        assert isinstance(entry_id, int)
    
    def test_audit_chain_integrity(self):
        from backend.core.audit import get_audit_logger
        logger = get_audit_logger()
        
        is_valid, message = logger.verify_chain_integrity()
        assert isinstance(is_valid, bool)
        assert isinstance(message, str)


class TestVaultModule:
    """Tests for vault module."""
    
    def test_vault_singleton(self):
        from backend.core.vault import get_vault
        vault1 = get_vault()
        vault2 = get_vault()
        assert vault1 is vault2
    
    def test_vault_locked_by_default(self):
        from backend.core.vault import get_vault
        vault = get_vault()
        # vault.is_locked is a property, not a method
        assert vault.is_locked


class TestDatabaseModule:
    """Tests for database module."""
    
    def test_database_initialization(self):
        from backend.core.database import init_db
        # Should not raise
        init_db()
    
    def test_get_db_cursor(self):
        from backend.core.database import get_db_cursor
        with get_db_cursor() as cursor:
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            assert result[0] == 1


class TestBrowserModule:
    """Tests for browser module."""
    
    def test_session_mode_enum(self):
        from backend.modules.browser import SessionMode
        assert SessionMode.PERSISTENT.value == "persistent"
        assert SessionMode.EPHEMERAL.value == "ephemeral"
        assert SessionMode.TOR_ISOLATED.value == "tor_isolated"
        assert SessionMode.LOCAL_ONLY.value == "local_only"
    
    def test_privacy_level_enum(self):
        from backend.modules.browser import PrivacyLevel
        assert PrivacyLevel.STANDARD.value == "standard"
        assert PrivacyLevel.ENHANCED.value == "enhanced"
        assert PrivacyLevel.MAXIMUM.value == "maximum"
    
    def test_browser_manager_singleton(self):
        from backend.modules.browser import get_browser_manager
        manager1 = get_browser_manager()
        manager2 = get_browser_manager()
        assert manager1 is manager2


class TestVectorSearchModule:
    """Tests for vector search module."""
    
    def test_sqlite_vec_availability(self):
        from backend.core.vector_search import is_sqlite_vec_available
        result = is_sqlite_vec_available()
        assert isinstance(result, bool)
    
    def test_store_embedding(self):
        from backend.core.vector_search import store_embedding
        import numpy as np
        
        # Create a test embedding
        embedding = np.random.rand(384).astype(np.float32).tobytes()
        result = store_embedding(99999, embedding)
        assert result is True


class TestFingerprintModule:
    """Tests for fingerprint module."""
    
    def test_fingerprint_rotator_singleton(self):
        from backend.modules.fingerprint_rotator import get_rotator
        rotator1 = get_rotator()
        rotator2 = get_rotator()
        assert rotator1 is rotator2
    
    def test_fingerprint_generation(self):
        from backend.modules.fingerprint_rotator import get_rotator
        rotator = get_rotator()
        profile = rotator.generate_profile()
        assert profile is not None
        assert hasattr(profile, 'profile_id')


class TestConsensusModule:
    """Tests for consensus module."""
    
    def test_consensus_engine_exists(self):
        from backend.modules.consensus_engine import ConsensusEngine
        # Just test that the class exists
        assert ConsensusEngine is not None


class TestRateLimiter:
    """Tests for rate limiter."""
    
    def test_rate_limiter_singleton(self):
        from backend.core.rate_limiter import get_limiter
        limiter1 = get_limiter()
        limiter2 = get_limiter()
        assert limiter1 is limiter2


class TestSupplyChain:
    """Tests for supply chain verification."""
    
    def test_verifier_singleton(self):
        from backend.core.supply_chain import get_verifier
        verifier1 = get_verifier()
        verifier2 = get_verifier()
        assert verifier1 is verifier2
    
    def test_dependency_verification(self):
        from backend.core.supply_chain import get_verifier
        verifier = get_verifier()
        # Should not raise
        results = verifier.verify_system_components()
        assert isinstance(results, dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
