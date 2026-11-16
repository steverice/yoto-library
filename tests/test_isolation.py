"""Guards against tests touching the developer's real home directory or Claude CLI."""

import shutil

from conftest import REAL_HOME, TEST_HOME

from yoto_lib.billing import BILLING_FILE
from yoto_lib.icons.download import ICON_CACHE_DIR
from yoto_lib.icons.icon_catalog import _DEFAULT_CACHE_DIR


def test_home_is_redirected():
    assert TEST_HOME != REAL_HOME


def test_module_level_paths_live_under_test_home():
    for path in (ICON_CACHE_DIR, _DEFAULT_CACHE_DIR, BILLING_FILE):
        assert path.is_relative_to(TEST_HOME), path


def test_claude_cli_is_stubbed():
    assert shutil.which("claude").startswith(str(TEST_HOME))
