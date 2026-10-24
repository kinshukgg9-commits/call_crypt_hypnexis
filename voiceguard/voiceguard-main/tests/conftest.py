"""Shared fixtures: an app wired to fake models, so tests run fast and offline."""
from __future__ import annotations

from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.pipeline import Analyzer
from backend.storage import Storage, StorageSettings
from tests.helpers import FakeDetector, FakeTranscriber


@pytest.fixture
def make_client(tmp_path):
    """make_client(texts=[...], detector=..., transcriber=..., storage=..., **settings) -> (client, transcriber, detector).

    Storage is off unless a test passes one, whatever DATABASE_URL says."""
    stack = ExitStack()

    def factory(texts=(), detector=None, transcriber=None, storage=None, **overrides):
        overrides.setdefault("preload_models", False)
        overrides.setdefault("frontend_dir", tmp_path / "no-frontend")
        settings = Settings(**overrides)
        transcriber = transcriber or FakeTranscriber(texts)
        detector = detector or FakeDetector()
        storage = storage or Storage(StorageSettings())
        app = create_app(settings, Analyzer(settings, transcriber, detector), storage)
        client = stack.enter_context(TestClient(app))
        return client, transcriber, detector

    yield factory
    stack.close()
