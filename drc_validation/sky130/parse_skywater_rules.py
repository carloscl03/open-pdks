#!/usr/bin/env python3
#
# parse_skywater_rules.py --
#
#	Convert the rule tables of the SkyWater sky130 design rule manual
#	(docs/rules/periphery-rules.rst in github.com/google/skywater-pdk)
#	into a CSV file with one row per rule:
#
#	    rule,section,description,flags,value,unit
#
#	The tables are reStructuredText "list-table" blocks whose rows look
#	like
#
#	    * - :drc_rule:`(m1.1)`
#	      - Width of metal1
#	      -
#	      - 0.140
#	      - µm
#
#	Usage:  parse_skywater_rules.py periphery-rules.rst rules.csv

import csv
import re
import sys

ROW_START = re.compile(r'^\s*\* - (.*)$')
CELL_START = re.compile(r'^\s*- ?(.*)$')
RULE_NAME = re.compile(r':drc_rule:`\(([^)`]+)\)`')
SECTION = re.compile(r'^:drc_rule:`\(([^)`]+)\)`\s*$')
ROLE = re.compile(r':drc_[a-z]+:`([^`]*)`')


def clean(text):
    """Strip the reST roles and the line-block bars, collapse spaces."""
    text = ROLE.sub(r'\1', text)
    text = text.replace('| ', '').strip()
    if text == '|':
        text = ''
    return re.sub(r'\s+', ' ', text)


def bare(name):
    """The manual writes some names with placeholder suffixes ("li.1.-",
    section "li.-.-"); drop them so names match the rest ("li.1", "li")."""
    return re.sub(r'(\.-)+$', '', name)


def parse(lines):
    rows = []
    section = ''
    row = None
    for line in lines:
        m = SECTION.match(line)
        if m:
            section = bare(m.group(1))
            continue
        m = ROW_START.match(line)
        if m:
            if row:
                rows.append((section, row))
            row = [m.group(1)]
            continue
        if row is None:
            continue
        m = CELL_START.match(line)
        if m and line.startswith('     -'):
            row.append(m.group(1))
        elif line.strip() == '' or not line.startswith('    '):
            rows.append((section, row))
            row = None
        else:
            row[-1] += ' ' + line.strip()
    if row:
        rows.append((section, row))

    rules = []
    for section, row in rows:
        m = RULE_NAME.search(row[0])
        if not m or len(row) < 5:
            continue
        name = bare(m.group(1))
        if name == 'Name':
            continue
        desc, flags, value, unit = (clean(c) for c in row[1:5])
        rules.append([name, section, desc, flags, value, unit])
    return rules


def main():
    if len(sys.argv) != 3:
        sys.exit('Usage: parse_skywater_rules.py periphery-rules.rst rules.csv')
    with open(sys.argv[1], encoding='utf-8') as f:
        rules = parse(f.read().splitlines())
    with open(sys.argv[2], 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['rule', 'section', 'description', 'flags', 'value', 'unit'])
        w.writerows(rules)
    print(f'{len(rules)} rules written to {sys.argv[2]}')


if __name__ == '__main__':
    main()
