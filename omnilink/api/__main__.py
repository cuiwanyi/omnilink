"""允许 python -m omnilink.api.main 直接启动 API（调试用）。"""
import uvicorn

from omnilink.config import get_settings

if __name__ == "__main__":
    uvicorn.run("omnilink.api.main:app", host="127.0.0.1",
                port=get_settings().api_port, log_level="info")
