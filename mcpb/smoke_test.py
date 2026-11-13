"""Smoke test an MCPB bundle: unpack it, start the server exactly as manifest.json says, list its tools.

    python mcpb/smoke_test.py <bundle.mcpb> [--expect-tools N]

Standard library only. user_config values are filled from their defaults, or a fresh temp folder for
directory settings, so the test needs no setup.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile


def substitute(value, mapping):
    return re.sub(r"\$\{([^}]+)\}", lambda m: mapping.get(m.group(1), m.group(0)), value)


def main(argv):
    bundle = argv[0]
    expect = int(argv[argv.index("--expect-tools") + 1]) if "--expect-tools" in argv else None
    with tempfile.TemporaryDirectory() as unpack_dir, tempfile.TemporaryDirectory() as work_dir:
        with zipfile.ZipFile(bundle) as archive:
            archive.extractall(unpack_dir)
        manifest = json.load(open(os.path.join(unpack_dir, "manifest.json"), encoding="utf-8"))
        mapping = {"__dirname": unpack_dir, "HOME": os.path.expanduser("~")}
        for key, spec in manifest.get("user_config", {}).items():
            default = spec.get("default")
            if spec.get("type") == "directory" or default is None:
                default = work_dir
            mapping["user_config." + key] = str(default)
        config = manifest["server"]["mcp_config"]
        command = [sys.executable if config["command"] in ("python", "python3") else config["command"]]
        command += [substitute(arg, mapping) for arg in config.get("args", [])]
        env = dict(os.environ)
        env.update({k: substitute(v, mapping) for k, v in config.get("env", {}).items()})
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        stdin = "".join(json.dumps(r) + "\n" for r in requests)
        result = subprocess.run(command, input=stdin, capture_output=True, text=True, env=env, cwd=work_dir, timeout=60)
        replies = {}
        for line in result.stdout.splitlines():
            if line.strip().startswith("{"):
                message = json.loads(line)
                if "id" in message:
                    replies[message["id"]] = message
        if 1 not in replies or 2 not in replies:
            print("server did not answer initialize/tools/list", result.stderr[-2000:], file=sys.stderr)
            return 1
        server = replies[1]["result"]["serverInfo"]
        tools = [tool["name"] for tool in replies[2]["result"]["tools"]]
        print(f"{server['name']} {server.get('version')}: {len(tools)} tools: {', '.join(tools)}")
        if server.get("version") and server["version"] != manifest["version"]:
            print(f"version mismatch: server {server['version']} != manifest {manifest['version']}", file=sys.stderr)
            return 1
        if expect is not None and len(tools) != expect:
            print(f"expected {expect} tools", file=sys.stderr)
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
