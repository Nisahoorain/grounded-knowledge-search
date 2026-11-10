"""Recompute a formula answer from cited inputs.

A figure such as 63.86 is accepted only when every input is copied from a
cited chunk or from the question, each shown step equals its expression,
and the formula equals the stated final. An average or a change has to be
a step: those amounts are not printed in the filing, so they cannot be
cited the way 7,772 can.
"""

import ast
import operator
import re
from decimal import Decimal, ROUND_HALF_UP

NUM_RE = re.compile(r"\d[\d,]*\.?\d*")
CALC_BLOCK_RE = re.compile(r"```\s*calc\b\s*(.*?)```", re.IGNORECASE | re.DOTALL)
CALC_OPEN_RE = re.compile(r"```\s*calc\b", re.IGNORECASE)
INPUT_RE = re.compile(
    r"^input:\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\(?-?\$?[\d,]+(?:\.\d+)?\)?%?)\s*\[(E\d+|Q)\]\s*$",
    re.IGNORECASE,
)
FORMULA_RE = re.compile(r"^formula:\s*(.+?)\s*$", re.IGNORECASE)
FINAL_RE = re.compile(
    r"^final:\s*(\(?-?\$?[\d,]+(?:\.\d+)?\)?%?)\s*$",
    re.IGNORECASE,
)
STEP_RE = re.compile(r"^step:\s*(.+?)\s*$", re.IGNORECASE)
NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
HEADER_RE = re.compile(r"^(inputs|steps)\s*:?\s*$", re.IGNORECASE)

# Count and scale factors. 365 is not here: it has to come from the question or a cited chunk.
SCALE_LITERALS = {1, 2, 3, 4, 10, 12, 52, 100, 1000, 1000000}

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}


def _canon(token):
    text = token.replace(",", "").rstrip(".")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _stated_canon(raw):
    """Canonical form of a number as written, without going through binary float."""
    text = raw.strip().replace(",", "").replace("$", "").replace("%", "")
    text = text.replace("−", "-").replace("–", "-")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    if "." in text.lstrip("-"):
        sign = "-" if text.startswith("-") else ""
        body = text.lstrip("-").rstrip("0").rstrip(".")
        text = sign + body
    return text


def _numbers(text):
    found = set()
    for token in NUM_RE.findall(text or ""):
        cleaned = token.replace(",", "").rstrip(".")
        if cleaned:
            found.add(_canon(cleaned))
    return found


def _parse_number(raw):
    text = raw.strip().replace(",", "").replace("$", "").replace("%", "")
    text = text.replace("−", "-").replace("–", "-")
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    if text.startswith("-"):
        negative = True
        text = text[1:]
    if not re.fullmatch(r"\d+(?:\.\d+)?", text or ""):
        raise ValueError(f"not a number: {raw}")
    value = float(text)
    if negative:
        value = -value
    return value


def _decimals(raw):
    text = raw.strip().replace(",", "").replace("$", "").replace("%", "")
    text = text.replace("−", "-").replace("–", "-")
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    if text.startswith("-"):
        text = text[1:]
    if "." not in text:
        return 0
    return len(text.split(".", 1)[1])


def _json_num(value):
    if value is None:
        return None
    if abs(value - round(value)) < 1e-9:
        return int(round(value))
    return value


def _show(value):
    if value is None:
        return "undefined"
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _within(computed, raw):
    stated = _parse_number(raw)
    places = _decimals(raw)
    if places == 0:
        tolerance = 1e-6
    else:
        tolerance = 0.51 * (10 ** (-places))
    return abs(computed - stated) <= tolerance + abs(stated) * 1e-12


def _has_negative(text, magnitude):
    compact = re.sub(r"[\s,$]", "", text or "").replace("−", "-").replace("–", "-")
    escaped = re.escape(magnitude)
    if re.search(rf"\({escaped}\)", compact):
        return True
    return re.search(rf"(?<![\d.])-{escaped}(?![\d.])", compact) is not None


def _in_source(raw, text):
    try:
        value = _parse_number(raw)
    except ValueError:
        return False
    magnitude = _canon(f"{abs(value):.10f}")
    if magnitude not in _numbers(text):
        return False
    if value < 0 and not _has_negative(text, magnitude):
        return False
    return True


def _prepare_expr(expr):
    text = expr.strip().replace("−", "-").replace("–", "-").replace("$", "")
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    text = re.sub(r"(?<=\d)%", "", text)
    return text


def _walk(node, names, literals):
    if isinstance(node, ast.Expression):
        _walk(node.body, names, literals)
        return
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("only plain numbers are allowed")
        literals.append(float(node.value))
        return
    if isinstance(node, ast.Name):
        names.append(node.id)
        return
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        if (
            isinstance(node.op, ast.USub)
            and isinstance(node.operand, ast.Constant)
            and isinstance(node.operand.value, (int, float))
            and not isinstance(node.operand.value, bool)
        ):
            literals.append(-float(node.operand.value))
            return
        _walk(node.operand, names, literals)
        return
    if isinstance(node, ast.BinOp):
        if type(node.op) not in _BINOPS:
            raise ValueError("only + - * / are allowed")
        _walk(node.left, names, literals)
        _walk(node.right, names, literals)
        return
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"abs", "nearest"}
        and len(node.args) == 1
        and not node.keywords
    ):
        _walk(node.args[0], names, literals)
        return
    raise ValueError("only + - * / , parentheses, abs(), and nearest() are allowed")


def _eval_node(node, env):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, env)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("only plain numbers are allowed")
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise ValueError(f"unknown name {node.id}")
        return float(env[node.id])
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval_node(node.operand, env)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
        return _eval_node(node.operand, env)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        left = _eval_node(node.left, env)
        right = _eval_node(node.right, env)
        if isinstance(node.op, ast.Div) and right == 0:
            raise ValueError("division by zero")
        return _BINOPS[type(node.op)](left, right)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "abs"
        and len(node.args) == 1
        and not node.keywords
    ):
        return abs(_eval_node(node.args[0], env))
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "nearest"
        and len(node.args) == 1
        and not node.keywords
    ):
        value = _eval_node(node.args[0], env)
        return float(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    raise ValueError("only + - * / , parentheses, abs(), and nearest() are allowed")


def eval_arithmetic(expr):
    """A formula of numbers, names already removed, and + - * /."""
    tree, names, _literals = _compile(expr)
    if names:
        raise ValueError("unresolved name")
    return _eval_node(tree, {})


def _compile(expr):
    prepared = _prepare_expr(expr)
    tree = ast.parse(prepared, mode="eval")
    names = []
    literals = []
    _walk(tree, names, literals)
    return tree, names, literals


def _close(left, right):
    return abs(left - right) <= 1e-6 + abs(right) * 1e-9


def _literal_allowed(value, bound_values, question):
    for other in bound_values:
        if _close(value, other):
            return True
    if abs(value - round(value)) <= 1e-9 and int(round(value)) in SCALE_LITERALS:
        return True
    for token in NUM_RE.findall(question or ""):
        try:
            if _close(value, _parse_number(token)):
                return True
        except ValueError:
            continue
    return False


def _touches_evidence(literals, evidence_values):
    for literal in literals:
        for evidence in evidence_values:
            if _close(literal, evidence):
                return True
    return False


def _blank(error=None):
    return {
        "verified": False,
        "formula": None,
        "inputs": [],
        "steps": [],
        "final": None,
        "errors": [] if error is None else [error],
        "derived_numbers": [],
    }


def _parse_step(body):
    if "=" not in body:
        raise ValueError("step needs an expression and a result")
    left, raw_value = body.rsplit("=", 1)
    left = left.strip()
    raw_value = raw_value.strip()
    _parse_number(raw_value)
    name = None
    expr = left
    if "=" in left:
        head, tail = left.split("=", 1)
        if NAME_RE.fullmatch(head.strip()):
            name = head.strip()
            expr = tail.strip()
    if not expr:
        raise ValueError("step is missing an expression")
    return name, expr, raw_value


def extract_calculation_block(answer):
    """Split a fenced calc block off the prose. No fence leaves the answer unchanged."""
    matches = list(CALC_BLOCK_RE.finditer(answer or ""))
    if len(matches) > 1:
        prose = CALC_BLOCK_RE.sub(" ", answer).strip()
        return prose, "", "only one calculation block is allowed"
    if len(matches) == 1:
        match = matches[0]
        prose = (answer[:match.start()] + " " + answer[match.end():]).strip()
        return prose, match.group(1).strip(), None
    if CALC_OPEN_RE.search(answer or ""):
        return answer, "", "calculation block is missing its closing fence"
    return answer, None, None


def verify_calculation(body, question, citation_map, chunk_text):
    """Recompute inputs, steps, and the final value. Nothing is trusted as printed."""
    result = _blank()
    formula = None
    final_raw = None
    inputs = []
    steps = []
    errors = []
    seen_names = set()

    lines = [line.strip() for line in (body or "").splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        result["errors"] = ["calculation block is empty"]
        return result

    for line in lines:
        if HEADER_RE.fullmatch(line):
            continue
        input_match = INPUT_RE.fullmatch(line)
        formula_match = FORMULA_RE.fullmatch(line)
        final_match = FINAL_RE.fullmatch(line)
        step_match = STEP_RE.fullmatch(line)
        if input_match:
            name, raw_value, source = input_match.groups()
            source = source.upper()
            if name in seen_names:
                errors.append(f"duplicate name {name}")
            seen_names.add(name)
            try:
                value = _parse_number(raw_value)
            except ValueError:
                errors.append(f"input {name} is not a number")
                continue
            if source == "Q":
                in_source = _in_source(raw_value, question)
                if not in_source:
                    errors.append(f"input {name}={_show(value)} is not in the question")
            elif source not in citation_map or source not in chunk_text:
                in_source = False
                errors.append(f"input {name} cites unknown {source}")
            else:
                in_source = _in_source(raw_value, chunk_text[source])
                if not in_source:
                    errors.append(f"input {name}={_show(value)} is not in [{source}]")
            inputs.append({
                "name": name,
                "value": _json_num(value),
                "source": source,
                "in_source": in_source,
                "_value": value,
                "_raw": raw_value,
            })
            continue
        if formula_match:
            if formula is not None:
                errors.append("formula was already given")
                continue
            formula = formula_match.group(1).strip()
            continue
        if final_match:
            if final_raw is not None:
                errors.append("final was already given")
                continue
            final_raw = final_match.group(1)
            continue
        if step_match:
            try:
                name, expr, raw_value = _parse_step(step_match.group(1))
            except (ValueError, SyntaxError) as exc:
                errors.append(f"step could not be read: {exc}")
                continue
            if name:
                if name in seen_names:
                    errors.append(f"duplicate name {name}")
                seen_names.add(name)
            steps.append({
                "name": name,
                "expression": expr,
                "raw": raw_value,
            })
            continue
        errors.append(f"unrecognized line: {line}")

    if formula is None:
        errors.append("missing formula")
    if final_raw is None:
        errors.append("missing final")
    if not steps:
        errors.append("at least one calculation step is required")

    evidence_names = set()
    evidence_values = []
    env = {}
    public_inputs = []
    for item in inputs:
        public_inputs.append({
            "name": item["name"],
            "value": item["value"],
            "source": item["source"],
            "in_source": item["in_source"],
        })
        env[item["name"]] = item["_value"]
        if item["source"].startswith("E") and item["in_source"]:
            evidence_names.add(item["name"])
            evidence_values.append(item["_value"])
    if not evidence_names:
        errors.append("calculation needs at least one input copied from a cited chunk")

    derived_names = set(evidence_names)
    public_steps = []
    for step in steps:
        computed = None
        used_names = []
        literals = []
        try:
            tree, used_names, literals = _compile(step["expression"])
            for literal in literals:
                if not _literal_allowed(literal, list(env.values()), question):
                    raise ValueError(
                        f"literal {_show(literal)} is not an input, a prior step, or a number from the question"
                    )
            for name in used_names:
                if name not in env:
                    raise ValueError(f"unknown name {name}")
            computed = _eval_node(tree, env)
            if not _within(computed, step["raw"]):
                errors.append(
                    f"step {step['name'] or step['expression']} computed {_show(computed)} "
                    f"but stated {_show(_parse_number(step['raw']))}"
                )
        except (SyntaxError, ValueError, ZeroDivisionError) as exc:
            errors.append(f"step {step['name'] or step['expression']}: {exc}")
        if computed is not None and step["name"]:
            env[step["name"]] = computed
            if (set(used_names) & derived_names) or _touches_evidence(literals, evidence_values):
                derived_names.add(step["name"])
        public_steps.append({
            "name": step["name"],
            "expression": step["expression"],
            "stated": _json_num(_parse_number(step["raw"])) if _step_value_ok(step["raw"]) else step["raw"],
            "computed": None if computed is None else _json_num(computed) if _decimals(step["raw"]) == 0 else computed,
            "ok": computed is not None and _within(computed, step["raw"]),
        })

    final = None
    if formula is not None and final_raw is not None:
        computed = None
        try:
            tree, used_names, literals = _compile(formula)
            for literal in literals:
                if not _literal_allowed(literal, list(env.values()), question):
                    raise ValueError(
                        f"literal {_show(literal)} is not an input, a prior step, or a number from the question"
                    )
            for name in used_names:
                if name not in env:
                    raise ValueError(f"unknown name {name}")
            if not (set(used_names) & derived_names) and not _touches_evidence(literals, evidence_values):
                errors.append("formula does not use an evidence input")
            computed = _eval_node(tree, env)
            if not _within(computed, final_raw):
                errors.append(
                    f"formula computed {_show(computed)} but final stated {_show(_parse_number(final_raw))}"
                )
        except (SyntaxError, ValueError, ZeroDivisionError) as exc:
            errors.append(f"formula: {exc}")
        final = {
            "stated": _json_num(_parse_number(final_raw)),
            "computed": computed,
            "ok": computed is not None and _within(computed, final_raw),
        }
    elif final_raw is not None:
        try:
            stated = _json_num(_parse_number(final_raw))
        except ValueError:
            stated = final_raw
        final = {"stated": stated, "computed": None, "ok": False}

    verified = not errors
    derived = []
    if verified:
        for item in inputs:
            derived.append(_stated_canon(item["_raw"]))
        for step in steps:
            derived.append(_stated_canon(step["raw"]))
        if final_raw is not None:
            derived.append(_stated_canon(final_raw))

    result.update({
        "verified": verified,
        "formula": formula,
        "inputs": public_inputs,
        "steps": public_steps,
        "final": final,
        "errors": errors,
        "derived_numbers": derived,
    })
    return result


def _step_value_ok(raw):
    try:
        _parse_number(raw)
    except ValueError:
        return False
    return True


def review_supported_answer(answer, question, citation_map, chunk_text):
    """Claim check plus, when a calc block is present, an independent recompute.

    Answers with no calc block keep the existing number check only.
    """
    from src.answering.generator import _canon_num, _refs_in, verify_claims

    prose, body, extract_error = extract_calculation_block(answer)
    calculation = None
    derived = None
    if extract_error and not body:
        calculation = _blank(extract_error)
    elif body is not None:
        calculation = verify_calculation(body, question, citation_map, chunk_text)
        if extract_error:
            calculation["verified"] = False
            calculation["errors"].insert(0, extract_error)
            calculation["derived_numbers"] = []
        if calculation["verified"]:
            derived = {_canon_num(token) for token in calculation["derived_numbers"]}

    claims = verify_claims(
        prose,
        citation_map,
        chunk_text,
        derived_numbers=derived,
        extra_refs=_refs_in(answer),
    )
    unsupported = [c for c in claims if not c["supported"]]
    calc_failed = calculation is not None and not calculation["verified"]
    reviewed = {
        "status": "answered" if not unsupported and not calc_failed else "answered_unverified",
        "verification": claims,
        "unsupported_claims": unsupported,
    }
    if calculation is not None:
        calculation.pop("derived_numbers", None)
        reviewed["calculation"] = calculation
    return reviewed
