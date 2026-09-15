"""Start the local dashboard and open its page once the server is ready."""
import sys
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from streamlit.web import cli

URL = "http://127.0.0.1:8501"


def open_when_ready():
    for _ in range(30):
        try:
            with urllib.request.urlopen(URL + "/_stcore/health", timeout=2) as response:
                if response.status == 200:
                    webbrowser.open(URL)
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(1)


def main():
    threading.Thread(target=open_when_ready, daemon=True).start()
    sys.argv = [
        "streamlit", "run", str(Path(__file__).with_name("app.py")),
        "--server.address", "127.0.0.1", "--server.port", "8501",
        "--server.headless", "true", "--browser.gatherUsageStats", "false",
    ]
    return cli.main()


if __name__ == "__main__":
    sys.exit(main())
