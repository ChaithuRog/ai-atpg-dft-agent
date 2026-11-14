"""Scan conversion for ISCAS'89 .bench circuits.

Atalanta is a combinational ATPG and rejects circuits that contain DFFs
("Error: N flip-flop exists in the circuit"). This tool applies the standard
full-scan assumption from DFT: every flip-flop is treated as a scan cell, so
its output Q becomes a pseudo primary input and its input D becomes a pseudo
primary output. The result is a combinational circuit Atalanta accepts.
ISCAS'85 circuits have no DFFs and pass through unchanged.

Usage:
    python tool_scan_convert.py benchmarks/s27.bench            # writes output/s27_scan.bench
    from tool_scan_convert import parse_bench, scan_convert, write_bench
"""
import json
import os
import re
import sys

GATE_TYPES = {"and", "nand", "or", "nor", "not", "buf", "buff", "xor", "xnor", "dff"}
IO_RE = re.compile(r"^(INPUT|OUTPUT)\s*\(\s*([^)\s]+)\s*\)\s*$", re.I)
GATE_RE = re.compile(r"^([^=\s]+)\s*=\s*(\w+)\s*\((.*)\)\s*$")


def parse_bench(path):
    """Parse a .bench file -> dict with inputs, outputs, gates, dffs."""
    c = {"name": re.sub(r"\.bench$", "", os.path.basename(path), flags=re.I),
         "inputs": [], "outputs": [], "gates": [], "dffs": []}
    with open(path, errors="replace") as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            m = IO_RE.match(line)
            if m:
                key = "inputs" if m.group(1).upper() == "INPUT" else "outputs"
                c[key].append(m.group(2))
                continue
            m = GATE_RE.match(line)
            if not m:
                raise ValueError(f"cannot parse line: {line[:80]}")
            out, gtype = m.group(1), m.group(2).lower()
            args = [a.strip() for a in m.group(3).split(",") if a.strip()]
            if gtype not in GATE_TYPES:
                raise ValueError(f"unsupported gate type '{gtype}': {line[:80]}")
            if gtype == "dff":
                c["dffs"].append((out, args[0]))
            else:
                c["gates"].append((out, gtype, args))
    if not c["inputs"]:
        raise ValueError(f"no INPUT lines found in {path}")
    return c


def scan_convert(c):
    """Full-scan conversion. Returns (inputs, outputs, gates, num_dffs)."""
    inputs = list(dict.fromkeys(c["inputs"] + [q for q, _ in c["dffs"]]))
    outputs = list(dict.fromkeys(c["outputs"] + [d for _, d in c["dffs"]]))
    gates = list(c["gates"])
    fixed = []
    for o in outputs:               # an output fed straight from an input needs a buffer
        if o in inputs:
            gates.append((o + "_po", "buff", [o]))
            fixed.append(o + "_po")
        else:
            fixed.append(o)
    return inputs, fixed, gates, len(c["dffs"])


def write_bench(name, inputs, outputs, gates, path, num_dffs=0):
    L = [f"# {name} (full-scan conversion, {num_dffs} DFFs -> pseudo PI/PO)"]
    L += [f"INPUT({n})" for n in inputs]
    L += [f"OUTPUT({n})" for n in outputs]
    for out, gtype, args in gates:
        g = "BUFF" if gtype in ("buf", "buff") else gtype.upper()
        L.append(f"{out} = {g}({', '.join(args)})")
    with open(path, "w") as fh:
        fh.write("\n".join(L) + "\n")


def convert_file(bench_path, out_dir="output"):
    """Convert one .bench file; returns JSON string with the result paths."""
    try:
        c = parse_bench(bench_path)
        os.makedirs(out_dir, exist_ok=True)
        if not c["dffs"]:
            return json.dumps({"status": "success", "converted": False,
                               "message": "combinational circuit, no conversion needed",
                               "bench_file": bench_path,
                               "inputs": len(c["inputs"]), "outputs": len(c["outputs"]),
                               "gates": len(c["gates"]), "dffs": 0})
        ins, outs, gates, nd = scan_convert(c)
        out_path = os.path.join(out_dir, c["name"] + "_scan.bench")
        write_bench(c["name"], ins, outs, gates, out_path, nd)
        return json.dumps({"status": "success", "converted": True,
                           "bench_file": out_path, "dffs": nd,
                           "scan_inputs": len(ins), "scan_outputs": len(outs),
                           "gates": len(gates),
                           "message": f"{nd} DFFs converted to pseudo PI/PO (full scan)"})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)[:400]})


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "message": "usage: python tool_scan_convert.py <file.bench>"}))
        sys.exit(1)
    print(convert_file(sys.argv[1]))
