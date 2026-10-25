# Magic DRC rule validation

Checks the DRC section of a magic tech file against the foundry design
rule manual, using magic alone.

For every rule listed in `<pdk>/tests.txt`, `run_magic_rule_tests.py`
draws a sweep of test structures around the rule value (a few grid steps
and a few percent on either side), runs magic's DRC on each one, and
finds the value at which magic stops flagging the rule.  The value of
each rule comes from `<pdk>/rules.csv`, which is generated from the rule
manual, so a wrong value in the tech file shows up as a mismatch instead
of being copied into the test.

Verdicts:

| verdict | meaning |
|---|---|
| OK | passes at the rule value, flagged one grid step below |
| LOOSE | magic passes structures that violate the rule |
| STRICT | magic flags structures that meet the rule |
| IRREGULAR | pass/fail changes more than once along the sweep |
| NODATA | the rule has no numeric value |

Only errors whose message names the rule under test count (magic ends
each message with the rule name in parentheses).  Other errors, such as
latchup or well-tap checks set off by the bare test structures, are
listed in a separate column.

## sky130

`sky130/rules.csv` is generated from `docs/rules/periphery-rules.rst` of
[google/skywater-pdk](https://github.com/google/skywater-pdk) (commit
9039be3779b9d27bae1aa20a29c6543b354067c0):

    python3 sky130/parse_skywater_rules.py periphery-rules.rst sky130/rules.csv

Run against a tech file generated from `sky130/magic/sky130.tech`:

    python3 common/preproc.py -DTECHNAME=sky130A -DMETAL5 -DMIM \
        -DREDISTRIBUTION sky130/magic/sky130.tech /tmp/sky130A.tech
    cd drc_validation
    python3 run_magic_rule_tests.py --tech /tmp/sky130A.tech \
        --rules sky130/rules.csv --tests sky130/tests.txt \
        --setup sky130/setup.tcl --report report.md

The exit status is 0 when every test is OK (or NODATA).

With magic versions from January 2026 up to 8.3.685, m1.7 and m2.7 (hole
area) come out LOOSE: the CIF "close" operator those rules are built on
did nothing (fixed in magic by restoring the unconditional push of the
starting tile in cifGatherFunc()).

Coverage so far: width, spacing, area and hole area of nwell, diff/tap,
poly, li and metal1 to metal5.  Contacts and vias (exact size,
enclosure, one-of-two-sides enclosure), wide-metal spacing and implant
rules are not covered yet.
