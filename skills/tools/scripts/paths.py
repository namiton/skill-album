"""Claude の設定フォルダとアルバムのデータフォルダの既定値。"""
import os


def claude_home():
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    return os.path.join(os.path.expanduser("~"), ".claude")


def data_dir():
    return os.path.join(claude_home(), "skill-album")
