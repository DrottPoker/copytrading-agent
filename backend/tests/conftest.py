import os

import pytest
from hypothesis import settings

from app.services.copy_signal_service import SOURCE_ACCOUNT_MODE_CACHE

# No deadline: shared CI runners are slow enough to make timing-based failures flaky.
settings.register_profile("default", deadline=None)
settings.register_profile("thorough", deadline=None, max_examples=2000)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "default"))


@pytest.fixture(autouse=True)
def reset_source_account_mode_cache() -> None:
    # The mode cache is process-wide; a test must never see another test's wallet.
    SOURCE_ACCOUNT_MODE_CACHE.clear()
