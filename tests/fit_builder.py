"""The test fixtures' FIT builder lives in the garmin_fit_store package; this module keeps the tests' import."""
from garmin_fit_store.builder import build_fit  # noqa: F401
from garmin_fit_store.protocol import crc16 as fit_crc  # noqa: F401
