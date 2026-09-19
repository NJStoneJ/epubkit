"""Drive the MCP server over a fake stdio pipe and print every response.

Run:  python tests/mcp_probe.py [book.epub]
"""

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from epubkit.mcp_server import serve  # noqa: E402

BOOK = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(HERE), "examples", "pg11.epub")

REQUESTS = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "probe", "version": "0"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "ping"},
    {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
     "params": {"name": "epub_info", "arguments": {"book": BOOK}}},
    {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
     "params": {"name": "epub_outline", "arguments": {"book": BOOK, "depth": 1}}},
    {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
     "params": {"name": "epub_search",
                "arguments": {"book": BOOK, "query": "Rabbit", "limit": 2}}},
    {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
     "params": {"name": "epub_read",
                "arguments": {"book": BOOK, "anchor": "c1", "max_tokens": 90}}},
    {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
     "params": {"name": "epub_read", "arguments": {"book": BOOK, "anchor": "zzz"}}},
    {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
     "params": {"name": "epub_chunks",
                "arguments": {"book": BOOK, "max_tokens": 800}}},
    {"jsonrpc": "2.0", "id": 10, "method": "tools/call",
     "params": {"name": "epub_markdown",
                "arguments": {"book": BOOK, "anchors": True}}},
    {"jsonrpc": "2.0", "id": 11, "method": "tools/call",
     "params": {"name": "epub_list", "arguments": {}}},
    {"jsonrpc": "2.0", "id": 12, "method": "tools/call",
     "params": {"name": "epub_info", "arguments": {"book": "nope.epub"}}},
    {"jsonrpc": "2.0", "id": 13, "method": "tools/call",
     "params": {"name": "epub_not_a_tool", "arguments": {}}},
    {"jsonrpc": "2.0", "id": 14, "method": "totally/unknown"},
]


def main():
    stdin = io.StringIO("".join(json.dumps(r, ensure_ascii=False) + "\n"
                                for r in REQUESTS))
    stdout = io.StringIO()
    serve(stdin=stdin, stdout=stdout)

    for raw in stdout.getvalue().splitlines():
        message = json.loads(raw)
        result = message.get("result")
        if isinstance(result, dict) and "content" in result:
            payload = result["content"][0]["text"]
            print("[id=%s] isError=%-5s %d chars"
                  % (message["id"], result.get("isError"), len(payload)))
            print("        " + payload.replace("\n", "\n        ")[:300])
        else:
            body = result if result is not None else message.get("error")
            print("[id=%s] %s" % (message.get("id"),
                                  json.dumps(body, ensure_ascii=False)[:300]))
        print()


if __name__ == "__main__":
    main()
