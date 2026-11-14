"""Fault simulator with pattern-file input.

Reads test patterns FROM A FILE and fault-simulates them on any ISCAS'85/'89
.bench circuit (sequential circuits are full-scan converted first). Reports
fault coverage, detected / undetected counts and the undetected fault list,
independently of Atalanta — so externally supplied or hand-written pattern
sets can be graded too.

Fault model: single stuck-at-0 / stuck-at-1 on every signal (primary inputs
and every gate output) of the scan-converted circuit. Fanout branches are not
modeled as separate fault sites (stem faults only), so the fault universe is
2 x (number of signals); Atalanta's collapsed fault list counts differently.

Pattern file format: one pattern per line as a string of 0/1 characters,
character j = value of scan input j in .bench INPUT() order. Lines starting
with '*' or '#' are ignored and Atalanta .test files ("  3: 01101 10") are
accepted as they are. 'x'/'X' bits are applied as 0 (and counted).

Simulation method: parallel-pattern simulation with Python big integers.
Each signal holds an N-bit integer, bit k = the signal's value under pattern
k, so one pass evaluates every pattern at once. Each fault re-evaluates only
its downstream cone.

Usage:
    python tool_fault_sim.py benchmarks/c432.bench output/c432.test
    from tool_fault_sim import simulate_patterns
"""
import json
import os
import re
import sys
from collections import deque

from tool_scan_convert import parse_bench, scan_convert


def read_pattern_file(path, n_inputs):
    """Return (patterns, x_bit_count); each pattern is a bit-per-input int."""
    pats, xs = [], 0
    with open(path, errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line[0] in "*#":
                continue
            if ":" in line:                          # Atalanta style "  12: 00111 00"
                line = line.split(":", 1)[1].strip()
            tok = line.split()[0] if line.split() else ""
            if not re.fullmatch(r"[01xX]+", tok):    # skip headers / prose
                continue
            xs += sum(1 for ch in tok if ch in "xX")
            tok = tok.replace("x", "0").replace("X", "0")
            if len(tok) < n_inputs:
                raise ValueError(f"pattern has {len(tok)} bits, circuit needs {n_inputs}: '{tok}'")
            v = 0
            for j in range(n_inputs):
                if tok[j] == "1":
                    v |= 1 << j
            pats.append(v)
    return pats, xs


def _topo_order(gates):
    """Topological order of gates (out, type, ins) by signal dependencies."""
    produced = {g[0]: i for i, g in enumerate(gates)}
    deps = [[produced[a] for a in g[2] if a in produced] for g in gates]
    indeg = [0] * len(gates)
    users = [[] for _ in gates]
    for i, ds in enumerate(deps):
        indeg[i] = len(ds)
        for d in ds:
            users[d].append(i)
    order, q = [], deque(i for i, d in enumerate(indeg) if d == 0)
    while q:
        i = q.popleft()
        order.append(i)
        for u in users[i]:
            indeg[u] -= 1
            if indeg[u] == 0:
                q.append(u)
    if len(order) != len(gates):
        raise ValueError("combinational loop detected in netlist")
    return order, users


def _eval_gate(gtype, ins, mask):
    v = ins[0]
    if gtype in ("and", "nand"):
        for x in ins[1:]:
            v &= x
    elif gtype in ("or", "nor"):
        for x in ins[1:]:
            v |= x
    elif gtype in ("xor", "xnor"):
        for x in ins[1:]:
            v ^= x
    if gtype in ("nand", "nor", "xnor", "not"):
        v = ~v & mask
    return v & mask


def simulate_patterns(bench_path, pattern_file):
    """Fault-simulate a pattern file on a .bench circuit. Returns a JSON string."""
    try:
        c = parse_bench(bench_path)
        inputs, outputs, gates, num_dffs = scan_convert(c)
        patterns, xbits = read_pattern_file(pattern_file, len(inputs))
        if not patterns:
            return json.dumps({"status": "error", "message": f"no patterns found in {pattern_file}"})
        npat = len(patterns)
        mask = (1 << npat) - 1

        # pack: signal -> integer, bit k = value under pattern k
        values = {}
        for j, name in enumerate(inputs):
            v = 0
            for k, p in enumerate(patterns):
                if (p >> j) & 1:
                    v |= 1 << k
            values[name] = v

        order, users = _topo_order(gates)

        def evaluate(vals, sub=None):
            """Evaluate gates in topo order; 'sub' = indices to (re)evaluate."""
            todo = order if sub is None else [i for i in order if i in sub]
            for i in todo:
                out, gtype, ins = gates[i]
                vals[out] = _eval_gate(gtype, [vals[a] for a in ins], mask)

        evaluate(values)
        good_out = {o: values.get(o, 0) for o in outputs}

        # downstream cone (gate indices) of every signal
        consumers = {}
        for i, (out, gtype, ins) in enumerate(gates):
            for a in ins:
                consumers.setdefault(a, []).append(i)

        def cone(sig):
            seen, q = set(), deque(consumers.get(sig, []))
            while q:
                i = q.popleft()
                if i in seen:
                    continue
                seen.add(i)
                q.extend(users[i])
            return seen

        signals = inputs + [g[0] for g in gates if g[0] not in inputs]
        detected, undetected, first_det = 0, [], {}
        for sig in signals:
            sub = cone(sig)
            for sa in (0, 1):
                forced = 0 if sa == 0 else mask
                if values.get(sig, 0) == forced and not sub:
                    undetected.append(f"{sig}/SA{sa}")
                    continue
                faulty = dict(values)
                faulty[sig] = forced
                evaluate(faulty, sub)          # the forced signal itself is never re-evaluated
                diff = 0
                for o in outputs:
                    diff |= (faulty.get(o, 0) ^ good_out[o])
                if diff:
                    detected += 1
                    first_det[f"{sig}/SA{sa}"] = (diff & -diff).bit_length()  # 1-based pattern
                else:
                    undetected.append(f"{sig}/SA{sa}")

        total = 2 * len(signals)
        cov = round(100.0 * detected / total, 3)
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
        os.makedirs(out_dir, exist_ok=True)
        und_file = os.path.join(out_dir, c["name"] + "_undetected.txt")
        with open(und_file, "w") as fh:
            fh.write("\n".join(undetected) + ("\n" if undetected else ""))
        return json.dumps({
            "status": "success",
            "circuit": c["name"],
            "scan_converted": num_dffs > 0,
            "dffs": num_dffs,
            "patterns_read": npat,
            "x_bits_applied_as_0": xbits,
            "fault_universe": total,
            "fault_model": "single stuck-at on every signal (stem faults)",
            "detected": detected,
            "undetected": len(undetected),
            "fault_coverage": cov,
            "undetected_file": und_file,
            "undetected_sample": undetected[:20],
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)[:400]})


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(json.dumps({"status": "error",
                          "message": "usage: python tool_fault_sim.py <circuit.bench> <patterns.txt>"}))
        sys.exit(1)
    print(simulate_patterns(sys.argv[1], sys.argv[2]))
