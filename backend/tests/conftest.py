import os

from hypothesis import settings

# No deadline: shared CI runners are slow enough to make timing-based failures flaky.
settings.register_profile("default", deadline=None)
settings.register_profile("thorough", deadline=None, max_examples=2000)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "default"))
