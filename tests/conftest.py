import os
import sys
import tempfile
from pathlib import Path

import pytest

# 测试环境：零延迟、临时 SQLite 数据库（先于任何 omnilink 导入生效）
os.environ["MOCK_LATENCY_MS"] = "0"
os.environ.setdefault("DATABASE_URL",
                      f"sqlite+aiosqlite:///{Path(tempfile.gettempdir()) / 'omnilink_test.db'}")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from omnilink.services.database import configure  # noqa: E402

configure(str(Path(tempfile.gettempdir()) / "omnilink_test.db"))


@pytest.fixture(autouse=True)
def _clean_test_db(tmp_path):
    db_file = tmp_path / "case.db"
    configure(str(db_file))
    yield
