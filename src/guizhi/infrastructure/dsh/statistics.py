"""Compose user-visible statistics directly from verified operation receipts."""
import re

from guizhi.domain.area_units import AREA_LABELS, M2_PER_UNIT


def display(value):
    if value is None:
        return "—"
    if value and abs(value) < 0.01:
        return format(value, ".4g")
    return format(value, ",.2f").removesuffix(".00")


def statistic_block(step, unit):
    stats = step.get("statistics", {})
    summary = stats.get("summary")
    if summary:
        is_area = summary["measure"] == "area"
        has_value = summary["measure"] != "count"
        fields = summary["group_by"]
        if not fields:
            total = summary["total"]
            count = f'{display(total["count"])} 个要素'
            if is_area:
                return f'{count}，面积合计 {display(total["area"][unit])} {AREA_LABELS[unit]}。'
            if has_value:
                return f'{count}，{summary["field"]}合计 {display(total["value"])}。'
            return count + "。"
        headers = [*fields, "要素数"]
        if has_value:
            headers.append("面积（" + AREA_LABELS[unit] + "）" if is_area else summary["field"] + "合计")

        def cells(record):
            values = [display(record["count"])]
            if has_value:
                amount = record["area"][unit] if is_area else record["value"]
                values.append(display(amount))
                if record["valid"] < record["count"]:
                    values[-1] += "（有效 " + str(record["valid"]) + " 条）"
            return values

        def row(items):
            return "| " + " | ".join(str(item).replace("|", r"\|").replace("\n", " ") for item in items) + " |"

        lines = [row(headers), row(["---"] * len(headers))]
        for group in summary["groups"]:
            lines.append(row([*(group["keys"][field] if group["keys"][field] is not None else "空值" for field in fields),
                              *cells(group)]))
        lines.append(row([*(["合计", *([""] * (len(fields) - 1))] if fields else []), *cells(summary["total"])]))
        if summary.get("next_offset") is not None:
            lines.append("\n共 " + str(summary["group_count"]) + " 组，合计包含全部匹配记录。")
        return "\n".join(lines)
    if step.get("operation") == "vector.area" and "area" in stats:
        return f'{stats["feature_count"]} 个要素，面积合计 {display(stats["area"][unit])} {AREA_LABELS[unit]}。'
    return None


ALIASES = {"平方米": "square_meters", "㎡": "square_meters", "m²": "square_meters", "m2": "square_meters",
           "亩": "mu", "公顷": "hectares", "ha": "hectares", "hm²": "hectares", "hm2": "hectares",
           "平方千米": "square_kilometers", "平方公里": "square_kilometers", "km²": "square_kilometers",
           "km2": "square_kilometers", "㎢": "square_kilometers"}
_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_UNIT = "(?:" + "|".join(map(re.escape, sorted(ALIASES, key=len, reverse=True))) + ")"
_EQUIVALENT = r"(?:折合(?:为)?|换算(?:为|成)|相当于|合约|即|≈|=)"
_PAIR = re.compile(
    rf"(?<![\d.,\-])(?P<first>{_NUMBER})\s*(?P<from_unit>{_UNIT})\s*"
    rf"(?P<link>(?:[（(]\s*(?:{_EQUIVALENT}\s*)?(?:大?约(?:为)?\s*)?|{_EQUIVALENT}\s*(?:大?约(?:为)?\s*)?))"
    rf"(?P<second>{_NUMBER})\s*(?P<to_unit>{_UNIT})(?P<end>[）)]|(?=\s|[，。；,;]|$))", re.IGNORECASE)


def verified_square_metres(steps):
    values = set()
    for step in steps:
        stats = step.get("statistics", {})
        if stats.get("area", {}).get("square_meters") is not None:
            values.add(stats["area"]["square_meters"])
        summary = stats.get("summary", {})
        if summary.get("measure") == "area":
            for row in [*summary["groups"], summary["total"]]:
                if row["area"]["square_meters"] is not None:
                    values.add(row["area"]["square_meters"])
    return values


def correct_equivalents(text, evidence):
    """Correct equivalent units using verified areas and the application unit definitions."""
    def checked(match):
        source = ALIASES[match["from_unit"].lower()]
        target = ALIASES[match["to_unit"].lower()]
        if source == target or (match["link"].lstrip().startswith(("(", "（")) and not match["end"]):
            return match[0]
        first = match["first"].replace(",", "")
        second = match["second"].replace(",", "")
        places = len(second.partition(".")[2])
        resolution = 0.5 * 10 ** -len(first.partition(".")[2])
        candidates = {round(value / M2_PER_UNIT[target], places) for value in evidence
                      if abs(value / M2_PER_UNIT[source] - float(first)) <= resolution + 1e-10}
        if not candidates and float(first) == 1 and "=" in match["link"]:
            candidates.add(round(M2_PER_UNIT[source] / M2_PER_UNIT[target], places))
        if len(candidates) != 1:
            return match[0]
        expected, = candidates
        # Allow deliberately coarse approximations and all correctly rounded displayed values.
        tolerance = max(0.5 * 10 ** -places, abs(expected) * 0.05 if "约" in match["link"] else 0)
        if abs(expected - float(second)) <= tolerance:
            return match[0]
        rendered = format(expected, f"{',' if ',' in match['second'] else ''}.{places}f")
        start = match.start("second") - match.start()
        return match[0][:start] + rendered + match[0][start + len(match["second"]):]
    return _PAIR.sub(checked, text) if evidence else text


def compose_statistics(steps, answer, unit):
    blocks = [(step, statistic_block(step, unit)) for step in steps]
    if not answer and len(blocks) == 1:
        return blocks[0][1] or ""
    for step, block in blocks:
        if block:
            answer = answer.replace("{{统计:" + step["id"] + "}}", "\n\n" + block + "\n\n")
    return correct_equivalents(answer, verified_square_metres(steps))
