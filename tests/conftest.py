import os
import sys
import tempfile

_test_root = tempfile.mkdtemp(prefix="amazing-kimi-tests-")
os.environ['SIGNAL_TEST_DATA']='1'
os.environ["AMAZING_KIMI_DB_PATH"] = os.path.join(_test_root, "test.db")
os.environ["SIGNAL_SEARCH_CONFIG_PATH"] = os.path.join(_test_root, "search_provider_config.json")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
