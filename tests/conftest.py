from hypothesis import settings

settings.register_profile(
    "deterministic", derandomize=True, max_examples=200, deadline=None, database=None
)
settings.load_profile("deterministic")
