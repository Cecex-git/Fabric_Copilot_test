#!/usr/bin/env python3
"""
Report Visual BPA (Best Practice Analyzer)
Validates Games Report.Report/report.json against rules defined in ReportBPARules.json.
Exits with code 1 if any rule with severity >= 2 is violated.
"""

import json
import sys
import os
from itertools import combinations

SEVERITY_LABELS = {1: "INFO", 2: "WARNING", 3: "ERROR"}


def load_rules(rules_path):
    with open(rules_path, "r", encoding="utf-8") as f:
        rules = json.load(f)
    return {r["ID"]: r for r in rules if r.get("Enabled", True)}


def load_report(report_path):
    with open(report_path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_visual_config(container):
    """Parse the stringified config JSON of a visual container."""
    try:
        return json.loads(container.get("config", "{}"))
    except (json.JSONDecodeError, TypeError):
        return {}


def get_position(container):
    """Return (x, y, width, height) from the outer container fields."""
    return (
        float(container.get("x", 0)),
        float(container.get("y", 0)),
        float(container.get("width", 0)),
        float(container.get("height", 0)),
    )


def rectangles_overlap(a, b):
    """Return True if two (x, y, w, h) rectangles overlap."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return (ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by)


def collect_text_sizes(obj, found=None):
    """Recursively find all 'textSize' values in a nested dict/list."""
    if found is None:
        found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "textSize" and isinstance(v, (int, float)):
                found.append(v)
            else:
                collect_text_sizes(v, found)
    elif isinstance(obj, list):
        for item in obj:
            collect_text_sizes(item, found)
    return found


def get_alt_text(single_visual):
    """
    Try to extract alt text from singleVisual.objects.general[0].properties.altText.
    Returns the value string if found and non-empty, else None.
    """
    try:
        general = single_visual.get("objects", {}).get("general", [])
        if not general:
            return None
        props = general[0].get("properties", {})
        alt = props.get("altText", {})
        # Two formats observed: {expr: {Literal: {Value: "'text'"}}} or {value: "text"}
        if "expr" in alt:
            val = alt["expr"].get("Literal", {}).get("Value", "")
            val = val.strip("'\"")
            return val if val else None
        if "value" in alt:
            val = str(alt["value"]).strip("'\"")
            return val if val else None
        return None
    except (AttributeError, IndexError, TypeError):
        return None


def validate(report, rules):
    violations = []
    sections = report.get("sections", [])

    # REPORT_MAX_PAGES
    rule = rules.get("REPORT_MAX_PAGES")
    if rule:
        max_pages = rule.get("MaxPages", 7)
        if len(sections) > max_pages:
            violations.append({
                "rule": rule,
                "page": None,
                "visual": None,
                "detail": f"Report has {len(sections)} pages (max allowed: {max_pages})."
            })

    for section in sections:
        page_name = section.get("displayName", "")
        page_w = float(section.get("width", 0))
        page_h = float(section.get("height", 0))
        containers = section.get("visualContainers", [])

        # REPORT_PAGE_HAS_DISPLAY_NAME
        rule = rules.get("REPORT_PAGE_HAS_DISPLAY_NAME")
        if rule and not page_name.strip():
            violations.append({
                "rule": rule,
                "page": page_name or "(unnamed)",
                "visual": None,
                "detail": "Page has no display name."
            })

        # REPORT_MAX_VISUALS_PER_PAGE
        rule = rules.get("REPORT_MAX_VISUALS_PER_PAGE")
        if rule:
            max_v = rule.get("MaxVisuals", 6)
            if len(containers) > max_v:
                violations.append({
                    "rule": rule,
                    "page": page_name,
                    "visual": None,
                    "detail": f"Page has {len(containers)} visuals (max allowed: {max_v})."
                })

        positions = []
        for container in containers:
            cfg = parse_visual_config(container)
            single = cfg.get("singleVisual", {})
            visual_name = cfg.get("name", "(unknown)")
            visual_type = single.get("visualType", "unknown")
            x, y, w, h = get_position(container)
            positions.append((x, y, w, h, visual_name, visual_type))

            # REPORT_VISUAL_WITHIN_BOUNDS
            rule = rules.get("REPORT_VISUAL_WITHIN_BOUNDS")
            if rule:
                if x + w > page_w or y + h > page_h:
                    violations.append({
                        "rule": rule,
                        "page": page_name,
                        "visual": visual_name,
                        "detail": (
                            f"Visual '{visual_type}' ({visual_name}) extends to "
                            f"x={x+w:.0f}, y={y+h:.0f} but page is {page_w:.0f}x{page_h:.0f}."
                        )
                    })

            # REPORT_VISUAL_HAS_PROJECTIONS
            rule = rules.get("REPORT_VISUAL_HAS_PROJECTIONS")
            if rule:
                projections = single.get("projections", {})
                has_fields = any(len(v) > 0 for v in projections.values() if isinstance(v, list))
                if not has_fields:
                    violations.append({
                        "rule": rule,
                        "page": page_name,
                        "visual": visual_name,
                        "detail": f"Visual '{visual_type}' ({visual_name}) has no fields or measures assigned."
                    })

            # REPORT_VISUAL_HAS_ALT_TEXT
            rule = rules.get("REPORT_VISUAL_HAS_ALT_TEXT")
            if rule:
                alt = get_alt_text(single)
                if alt is None:
                    violations.append({
                        "rule": rule,
                        "page": page_name,
                        "visual": visual_name,
                        "detail": f"Visual '{visual_type}' ({visual_name}) has no alt text configured."
                    })

            # REPORT_TEXT_SIZE_MIN_12PX
            rule = rules.get("REPORT_TEXT_SIZE_MIN_12PX")
            if rule:
                min_size = rule.get("MinTextSize", 12)
                objects = single.get("objects", {})
                text_sizes = collect_text_sizes(objects)
                for ts in text_sizes:
                    if ts < min_size:
                        violations.append({
                            "rule": rule,
                            "page": page_name,
                            "visual": visual_name,
                            "detail": (
                                f"Visual '{visual_type}' ({visual_name}) has a text size of {ts}px "
                                f"(minimum: {min_size}px)."
                            )
                        })

        # REPORT_NO_OVERLAPPING_VISUALS
        rule = rules.get("REPORT_NO_OVERLAPPING_VISUALS")
        if rule:
            for (ax, ay, aw, ah, an, at), (bx, by, bw, bh, bn, bt) in combinations(positions, 2):
                if rectangles_overlap((ax, ay, aw, ah), (bx, by, bw, bh)):
                    violations.append({
                        "rule": rule,
                        "page": page_name,
                        "visual": f"{an} & {bn}",
                        "detail": (
                            f"'{at}' ({an}) overlaps with '{bt}' ({bn}) on page '{page_name}'."
                        )
                    })

    return violations


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    rules_path = os.path.join(script_dir, "ReportBPARules.json")

    if len(sys.argv) < 2:
        print("Usage: validate_report_bpa.py <path-to-report.json>")
        sys.exit(1)

    report_path = sys.argv[1]

    if not os.path.exists(rules_path):
        print(f"❌ Rules file not found: {rules_path}")
        sys.exit(1)

    if not os.path.exists(report_path):
        print(f"❌ Report file not found: {report_path}")
        sys.exit(1)

    rules = load_rules(rules_path)
    report = load_report(report_path)
    violations = validate(report, rules)

    if not violations:
        print("✅ Report BPA passed — no violations found.")
        sys.exit(0)

    # Group violations by severity
    errors = [v for v in violations if v["rule"]["Severity"] >= 3]
    warnings = [v for v in violations if v["rule"]["Severity"] == 2]
    infos = [v for v in violations if v["rule"]["Severity"] == 1]

    def print_group(items, label):
        if not items:
            return
        print(f"\n── {label} ──────────────────────────────────────")
        for v in items:
            rule = v["rule"]
            sev = SEVERITY_LABELS.get(rule["Severity"], "?")
            page = v["page"] or "(unnamed page)"
            print(f"  [{sev}] {rule['ID']}: {rule['Name']}")
            print(f"         Page: {page}")
            if v["visual"]:
                print(f"         Visual: {v['visual']}")
            print(f"         {v['detail']}")

    print_group(errors, "ERRORS")
    print_group(warnings, "WARNINGS")
    print_group(infos, "INFO")

    print(f"\nReport BPA summary: {len(errors)} error(s), {len(warnings)} warning(s), {len(infos)} info(s).")

    if errors or warnings:
        print("❌ Report BPA failed.")
        sys.exit(1)
    else:
        print("✅ Report BPA passed (info-only violations).")
        sys.exit(0)


if __name__ == "__main__":
    main()
