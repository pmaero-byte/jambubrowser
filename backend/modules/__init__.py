"""
Intelligence Modules
====================
AI gateway, metasearch, web scraping, browser automation,
and other agentic capabilities.
"""

from backend.modules.ai_gateway import ask_ai, generate_hypothesis
from backend.modules.search import multi_engine_search, filter_trusted_results
from backend.modules.scraper import get_sovereign_crawler, get_scrape_config
from backend.modules.browser import (
    BrowserSession,
    BrowserManager,
    get_browser_manager,
    scrape_url,
    navigate,
    click_element,
    click_coordinates,
    type_text,
    fill_form,
    scroll_page,
    take_screenshot,
    get_page_content,
    cleanup_browser,
)
from backend.modules.missions import (
    MissionScheduler, Mission, get_scheduler, parse_cron, get_next_run,
)
from backend.modules.notifications import (
    Notifier, Notification, Urgency, get_notifier, send_notification,
)
from backend.modules.risk_shield import (
    RiskShield, get_shield, assess_url_risk, quick_url_check,
)
from backend.modules.shadow_browser import (
    ShadowBrowser, InterestTopic, get_shadow_browser,
)
from backend.modules.vision import (
    VisionModel, VisionGrounder, VisionAnalysis, UIElement, get_vision_model,
)
from backend.modules.form_filler import (
    FormFiller, FormDetector, DetectedForm, FormField, get_form_filler,
)
from backend.modules.local_connector import (
    ObsidianConnector, RemindersConnector, ClipboardConnector,
    FilesystemConnector, get_obsidian, get_reminders, get_clipboard, get_filesystem,
)
from backend.modules.knowledge_graph import (
    KnowledgeGraph, Entity, Relation, EntityExtractor, get_knowledge_graph,
)
from backend.modules.p2p_discovery import (
    P2PDiscovery, Peer, get_p2p,
)
from backend.modules.multimodal_input import (
    MultimodalProcessor, ProcessedInput, get_processor,
)
from backend.modules.skill_synthesizer import (
    SkillSynthesizer, SynthesizedSkill, FailureContext, get_synthesizer,
)
from backend.modules.fingerprint_rotator import (
    FingerprintRotator, BrowserFingerprint, get_rotator,
)
from backend.modules.federated_rag import (
    FederatedRAG, FederatedQuery, FederatedResult, get_federated_rag,
)
from backend.modules.youtube import (
    YouTubeAnalyzer, YouTubeVideo, YouTubeTranscript, get_youtube_analyzer,
)
from backend.modules.model_manager import (
    ModelManager, ModelInfo, get_model_manager, GEMMA4_MODELS,
)
