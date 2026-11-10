"""Declared skill-forge op plugin (L3, restricted namespace).

It only transforms values already collected into the runner's `ctx`;
it cannot open files, import os/subprocess/socket, or reach the network.
See references/benchmark-and-plugins.md for the trust model.
"""
SPEC = {
    "name": "risk_score",
    "args": ["weight"],
    "note": "把清单中含 todo/warn/risk 的条目数乘以权重，得到风险分",
}


def run(ctx, **kwargs):
    import re

    weight = int(kwargs.get("weight", 1))
    names = []
    for value in ctx.values():
        if isinstance(value, list) and all(isinstance(x, str) for x in value):
            names.extend(value)
    hits = sum(1 for name in names
               if re.search(r"(todo|warn|risk)", str(name), re.I))
    return hits * weight
