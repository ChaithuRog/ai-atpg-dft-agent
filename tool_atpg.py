"""Run the Atalanta ATPG on an ISCAS'85/'89 .bench circuit.

* Sequential (ISCAS'89) circuits are full-scan converted first
  (tool_scan_convert), because Atalanta is combinational-only.
* Patterns go to output/<name>.test, the summary log to output/<name>.log.
* The summary is parsed: coverage, pattern counts, collapsed, redundant and
  aborted faults.

Atalanta is NOT bundled (see README). It is located via $ATALANTA_PATH,
$ATALANTA_BIN, ~/Atalanta/atalanta, ~/atalanta/atalanta or PATH.
Run on Linux or inside WSL on Windows.
"""
import json
import os
import re
import shutil
import subprocess

from tool_scan_convert import convert_file

HERE = os.path.abspath(os.path.dirname(__file__))


def find_atalanta():
    for cand in (os.environ.get("ATALANTA_PATH"), os.environ.get("ATALANTA_BIN"),
                 os.path.expanduser("~/Atalanta/atalanta"),
                 os.path.expanduser("~/atalanta/atalanta"), shutil.which("atalanta")):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def parse_summary(text):
    """Extract the key numbers from Atalanta's summary printout."""
    out = {}
    for key, pat, cast in [
        ("fault_coverage", r"Fault coverage\s*:\s*([\d.]+)", float),
        ("patterns_before_compaction", r"patterns before compaction\s*:\s*(\d+)", int),
        ("patterns_after_compaction", r"patterns after compaction\s*:\s*(\d+)", int),
        ("collapsed_faults", r"collapsed faults\s*:\s*(\d+)", int),
        ("redundant_faults", r"identified redundant faults\s*:\s*(\d+)", int),
        ("aborted_faults", r"aborted faults\s*:\s*(\d+)", int),
        ("backtrackings", r"backtrackings\s*:\s*(\d+)", int),
    ]:
        m = re.search(pat, text)
        if m:
            out[key] = cast(m.group(1))
    return out


def generate_test_vectors(circuit_path, backtrack_limit=None, tag=""):
    """Generate test vectors; returns a JSON string with files and summary."""
    try:
        out_dir = os.path.join(HERE, "output")
        os.makedirs(out_dir, exist_ok=True)
        circuit_abs = os.path.abspath(circuit_path)
        if not os.path.isfile(circuit_abs):
            return json.dumps({"status": "error", "message": f"circuit file not found: {circuit_abs}"})
        conv = json.loads(convert_file(circuit_abs, out_dir))
        if conv["status"] != "success":
            return json.dumps({"status": "error", "message": "scan conversion failed: " + conv["message"]})
        run_path = conv["bench_file"] if conv["converted"] else circuit_abs
        binary = find_atalanta()
        if binary is None:
            return json.dumps({"status": "error", "message":
                               "atalanta not found: build it (see README) or set ATALANTA_PATH"})
        base = os.path.splitext(os.path.basename(circuit_path))[0]
        test_file = os.path.join(out_dir, base + tag + ".test")
        log_file = os.path.join(out_dir, base + tag + ".log")
        extra = ["-b", str(backtrack_limit)] if backtrack_limit else []
        with open(log_file, "w") as log:
            subprocess.run([binary, *extra, "-t", test_file, run_path],
                           stdout=log, stderr=subprocess.STDOUT, timeout=1800, check=False)
        if not os.path.isfile(test_file):
            tail = open(log_file, errors="replace").read()[-400:]
            return json.dumps({"status": "error", "message": "atalanta produced no test file. Log tail: " + tail})
        return json.dumps({
            "status": "success", "test_vector_file": test_file, "log_file": log_file,
            "scan_converted": conv["converted"], "dffs_converted": conv.get("dffs", 0),
            "summary": parse_summary(open(log_file, errors="replace").read()),
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": f"unexpected error: {e}"})


if __name__ == "__main__":
    import sys
    print(generate_test_vectors(sys.argv[1] if len(sys.argv) > 1 else "benchmarks/c17.bench"))
