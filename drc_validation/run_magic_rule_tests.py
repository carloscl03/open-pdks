#!/usr/bin/env python3
#
# run_magic_rule_tests.py --
#
#	Check the DRC section of a magic tech file against the design rule
#	manual.  For every rule listed in a tests file, draw a sweep of test
#	structures around the rule value (a few grid steps and a few percent
#	on either side), run magic's own DRC on each one, and find the value
#	at which magic stops flagging the rule.  That threshold is then
#	compared with the value in the rule manual:
#
#	    OK         magic passes the structure at the rule value and
#	               flags it one grid step below
#	    LOOSE      magic passes structures that violate the rule
#	    STRICT     magic flags structures that meet the rule
#	    IRREGULAR  pass/fail does not change once along the sweep
#	    NODATA     no usable test point (rule has no numeric value, ...)
#
#	Only errors whose message names the rule under test count; magic
#	puts the rule name in parentheses at the end of each message, with
#	its own spelling ("met1.1" for the manual's "m1.1"), which the tests
#	file can give with "magic=<name>".  Other errors (latchup, well taps,
#	...) are listed separately.
#
#	Only magic is used; no other DRC tool is involved.
#
# Usage:
#	run_magic_rule_tests.py --tech sky130A.tech --rules sky130/rules.csv
#		--tests sky130/tests.txt [--setup sky130/setup.tcl]
#		[--magic magic] [--grid 0.005] [--style "drc(full)"]
#		[--report report.md] [--keep dir]

import argparse
import csv
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile


def load_rules(path):
    rules = {}
    with open(path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            rules[row['rule']] = row
    return rules


def magic_name(rule):
    """Magic's usual spelling of a manual rule name."""
    rule = re.sub(r'^m(\d)\.', r'met\1.', rule)
    return re.sub(r'^difftap\.', 'diff/tap.', rule)


def load_tests(path):
    tests = []
    with open(path, encoding='utf-8') as f:
        for lineno, line in enumerate(f, 1):
            line = line.split('#', 1)[0].split()
            if not line:
                continue
            if len(line) < 3:
                sys.exit(f'{path}:{lineno}: expected "rule kind layer"')
            rule, kind, layer = line[:3]
            opts = dict(o.split('=', 1) for o in line[3:])
            if kind not in ('width', 'space', 'area', 'hole'):
                sys.exit(f'{path}:{lineno}: unknown kind "{kind}"')
            tests.append({'rule': rule, 'kind': kind, 'layer': layer,
                          'in': opts.get('in'),
                          'magic': opts.get('magic', magic_name(rule))})
    names = [t['rule'] for t in tests]
    for t in tests:
        t['name'] = t['rule'] if names.count(t['rule']) == 1 \
            else f"{t['rule']}/{t['layer']}"
    return tests


def number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


class Geometry:
    """Draws the test structures.  All values in microns, on the grid."""

    def __init__(self, grid):
        self.grid = grid

    def snap(self, v):
        return round(round(v / self.grid) * self.grid, 6)

    def snap_up(self, v):
        return round(math.ceil(v / self.grid - 1e-6) * self.grid, 6)

    def sweep(self, base):
        """Test points around base: +-3 grid steps and +-10/20/50 %."""
        pts = {self.snap(base + k * self.grid) for k in range(-3, 4)}
        for f in (0.5, 0.8, 0.9, 1.1, 1.2, 1.5):
            pts.add(self.snap(base * f))
        return sorted(p for p in pts if p > 0)


def build_cases(tests, rules, geo):
    """Return (cases, notes).  Each case is one cell to draw and check."""
    minwidth, minarea = {}, {}
    for t in tests:
        v = number(rules.get(t['rule'], {}).get('value'))
        if v is None:
            continue
        if t['kind'] == 'width':
            minwidth.setdefault(t['layer'], v)
        elif t['kind'] == 'area':
            minarea.setdefault(t['layer'], v)

    cases, notes = [], {}
    for t in tests:
        row = rules.get(t['rule'])
        v = number(row['value']) if row else None
        if v is None:
            notes[t['name']] = 'no numeric value in rules file' if row \
                else 'rule not in rules file'
            continue
        t['value'] = v
        layer = t['layer']
        minw = minwidth.get(layer, v)
        area = minarea.get(layer, 0.0)
        g = geo

        if t['kind'] == 'width':
            base = v
            def shapes(x, area=area):
                ln = g.snap_up(max(2.0, 10 * x, 2 * area / x))
                return [('paint', 0, 0, x, ln)], x
        elif t['kind'] == 'space':
            base = v
            w = g.snap_up(2 * minw)
            ln = g.snap_up(max(2.0, 10 * minw, 2 * area / w))
            def shapes(x, w=w, ln=ln):
                return [('paint', 0, 0, w, ln),
                        ('paint', round(w + x, 6), 0,
                         round(2 * w + x, 6), ln)], x
        elif t['kind'] == 'area':
            w = g.snap_up(max(minw, math.sqrt(v)))
            base = g.snap_up(v / w)
            def shapes(x, w=w):
                return [('paint', 0, 0, w, x)], round(w * x, 6)
        else:  # hole
            base = g.snap_up(math.sqrt(v))
            ring = g.snap_up(max(2 * minw, 0.5))
            def shapes(x, ring=ring):
                outer = round(x + 2 * ring, 6)
                return [('paint', 0, 0, outer, outer),
                        ('erase', ring, ring, round(ring + x, 6),
                         round(ring + x, 6))], round(x * x, 6)

        for x in geo.sweep(base):
            if t['kind'] in ('area', 'hole') and x < minw:
                continue        # would also break the width rule
            ops, metric = shapes(x)
            cases.append({'test': t['name'], 'x': x, 'metric': metric,
                          'layer': layer, 'in': t['in'], 'ops': ops})
    return cases, notes


def tcl_script(cases, style, setup):
    out = [setup, 'drc off', f'drc style {style}', 'units microns',
           # Report any box that magic could not place exactly as asked
           'proc chk {i want} {',
           '    foreach a [box values] b $want {',
           '        if {abs($a - $b) > 1e-6} {puts "OFFGRID $i"; return}',
           '    }',
           '}', '']
    for i, c in enumerate(cases):
        cell = f't{i}'
        xs = [o[1] for o in c['ops']] + [o[3] for o in c['ops']]
        ys = [o[2] for o in c['ops']] + [o[4] for o in c['ops']]
        llx, lly, urx, ury = min(xs), min(ys), max(xs), max(ys)
        out += [f'cellname create {cell}', f'load {cell} -silent']
        if c['in']:
            out += [f'box {llx-2}um {lly-2}um {urx+2}um {ury+2}um',
                    f'paint {c["in"]}']
        for op, x1, y1, x2, y2 in c['ops']:
            out += [f'box {x1}um {y1}um {x2}um {y2}um',
                    f'chk {i} {{{x1} {y1} {x2} {y2}}}',
                    f'{op} {c["layer"]}']
        out += [f'box {llx-5}um {lly-5}um {urx+5}um {ury+5}um',
                'drc check', 'drc catchup',
                'foreach {msg boxes} [drc listall why] '
                f'{{puts "MSG {i} [llength $boxes] $msg"}}',
                f'puts "DONE {i}"', '']
    out.append('quit -noprompt')
    return '\n'.join(out) + '\n'


def run_magic(magic, tech, script, workdir):
    path = os.path.join(workdir, 'rule_tests.tcl')
    with open(path, 'w') as f:
        f.write(script)
    cmd = [magic, '-dnull', '-noconsole', '-norcfile', '-T', tech, path]
    p = subprocess.run(cmd, cwd=workdir, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=3600)
    with open(os.path.join(workdir, 'magic.log'), 'w') as f:
        f.write(p.stdout + p.stderr)
    results, offgrid = {}, set()
    for line in p.stdout.splitlines():
        m = re.match(r'MSG (\d+) (\d+) (.*)$', line)
        if m:
            results.setdefault(int(m.group(1)), []).append(
                (int(m.group(2)), m.group(3).strip()))
        elif line.startswith('DONE '):
            results.setdefault(int(line.split()[1]), [])
        elif line.startswith('OFFGRID '):
            offgrid.add(int(line.split()[1]))
    return results, offgrid


def judge(points, value, eps):
    """points: [(metric, nerrors, why)] sorted by metric."""
    if not points:
        return 'NODATA', None, None
    flags = [n > 0 for _, n, _ in points]
    changes = sum(1 for a, b in zip(flags, flags[1:]) if a != b)
    passing = [m for m, n, _ in points if n == 0]
    failing = [m for m, n, _ in points if n > 0]
    hi = min(passing, default=None)
    lo = max([m for m in failing if hi is None or m < hi], default=None)
    at = [p for p in points if p[0] >= value - eps]
    below = [p for p in points if p[0] < value - eps]
    if changes > 1 or (changes == 1 and not flags[0]):
        return 'IRREGULAR', lo, hi
    if below and below[-1][1] == 0:
        return 'LOOSE', lo, hi
    if at and at[0][1] > 0:
        return 'STRICT', lo, hi
    return 'OK', lo, hi


def fmt(v):
    return '-' if v is None else f'{v:g}'


def main():
    ap = argparse.ArgumentParser(
        description='Check a magic tech file DRC against the rule manual.')
    ap.add_argument('--tech', required=True)
    ap.add_argument('--rules', required=True)
    ap.add_argument('--tests', required=True)
    ap.add_argument('--setup', help='Tcl file to source before the tests')
    ap.add_argument('--magic', default='magic')
    ap.add_argument('--grid', type=float, default=0.005)
    ap.add_argument('--style', default='drc(full)')
    ap.add_argument('--report')
    ap.add_argument('--keep', help='keep the work files in this directory')
    args = ap.parse_args()

    rules = load_rules(args.rules)
    tests = load_tests(args.tests)
    geo = Geometry(args.grid)
    cases, notes = build_cases(tests, rules, geo)

    workdir = args.keep or tempfile.mkdtemp(prefix='magic_rule_tests_')
    os.makedirs(workdir, exist_ok=True)
    setup = open(args.setup).read() if args.setup else ''
    results, offgrid = run_magic(args.magic, os.path.abspath(args.tech),
                                 tcl_script(cases, args.style, setup),
                                 workdir)
    if len(results) != len(cases):
        sys.exit(f'magic returned {len(results)} of {len(cases)} results; '
                 f'see {workdir}/magic.log')
    if offgrid:
        bad = sorted({cases[i]['test'] for i in offgrid})
        sys.exit(f'test structures off the magic grid ({", ".join(bad)}); '
                 f'wrong --grid or missing --setup?')

    lines = ['| rule | kind | layer | manual | magic threshold | verdict | '
             'magic message | other errors |',
             '|---|---|---|---|---|---|---|---|']
    counts = {}
    for t in tests:
        tag = f"({t['magic']})"
        pts, other = [], set()
        for i, c in enumerate(cases):
            if c['test'] != t['name']:
                continue
            mine = [(n, w) for n, w in results[i] if w.endswith(tag)]
            other.update(w for n, w in results[i] if not w.endswith(tag))
            pts.append((c['metric'], sum(n for n, _ in mine),
                        mine[0][1] if mine else ''))
        pts.sort()
        if t['name'] in notes:
            verdict, lo, hi = 'NODATA', None, None
        else:
            eps = args.grid * args.grid if t['kind'] in ('area', 'hole') \
                else args.grid / 2
            verdict, lo, hi = judge(pts, t['value'], eps / 10)
        counts[verdict] = counts.get(verdict, 0) + 1
        why = next((w for _, n, w in pts if n > 0), '')
        thr = notes.get(t['name']) or f'fails {fmt(lo)}, passes {fmt(hi)}'
        lines.append(f"| {t['name']} | {t['kind']} | {t['layer']} | "
                     f"{fmt(t.get('value'))} | {thr} | {verdict} | {why} | "
                     f"{'; '.join(sorted(other))} |")

    summary = ', '.join(f'{k} {v}' for k, v in sorted(counts.items()))
    text = '\n'.join(lines) + f'\n\n{len(tests)} tests: {summary}\n'
    print(text)
    if args.report:
        with open(args.report, 'w', encoding='utf-8') as f:
            f.write(f'# Magic DRC vs rule manual\n\ntech: {args.tech}\n'
                    f'style: {args.style}\n\n' + text)
    if not args.keep:
        shutil.rmtree(workdir)
    sys.exit(0 if set(counts) <= {'OK', 'NODATA'} else 1)


if __name__ == '__main__':
    main()
