"""Filter, order and group a provider's model list for the person choosing a model.

Nothing here invents data. Kind, maker, price and score are the ones the catalog rows carry;
a row that lacks a value is still listed, after the rows that have it. Every function returns
new rows and leaves the catalog untouched.
"""

from typing import Any

Json = dict[str, Any]

KINDS = ("all", "text", "image")
SORTS = ("recommended", "price_low", "price_high", "capability")
#: Section and group keys that are not a maker's name; the interface gives them their titles.
QUICK = "__quick__"
TEXT_SECTION = "__text__"
IMAGE_SECTION = "__image__"
#: Below this many models a list needs no organizing aids.
MIN_USEFUL = 9
MAX_QUERY = 80


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return 0.0
    return float(value) if 0 < value < 1e9 else 0.0


def kind(row: Json) -> str:
    """What the model produces: pictures or text."""
    capabilities = row.get("capabilities")
    return "image" if isinstance(capabilities, dict) and capabilities.get("image_generation") is True else "text"


def group(row: Json) -> str:
    """The maker (or model family) a row is listed under; quick choices have a group of their own."""
    if row.get("recommended") is True:
        return QUICK
    for key in ("maker", "family"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:60]
    identity = row.get("id")
    # Catalogs that name models "maker/model" state the maker in the identifier itself.
    if isinstance(identity, str) and "/" in identity.strip("/"):
        return identity.split("/", 1)[0].strip()[:60]
    return ""


def price(row: Json) -> float:
    """One comparable price per row, 0 when the catalog states none.

    Pictures are compared by their price per image. Text is compared by the usual blend of
    three parts read to one part written, the proportion of a working conversation.
    """
    pricing = row.get("pricing")
    if not isinstance(pricing, dict):
        return 0.0
    if kind(row) == "image":
        return _number(pricing.get("per_image"))
    read, written = _number(pricing.get("input_per_1m")), _number(pricing.get("output_per_1m"))
    return (3 * read + written) / 4 if read or written else 0.0


def score(row: Json) -> float:
    """The independent capability score the catalog reports for this kind of model, 0 if none."""
    scores = row.get("scores")
    if not isinstance(scores, dict):
        return 0.0
    return _number(scores.get("image" if kind(row) == "image" else "intelligence"))


def _matches(row: Json, words: list[str]) -> bool:
    label = group(row)
    haystack = " ".join(str(part) for part in (row.get("name"), row.get("id"), "" if label == QUICK else label)
                        if part).casefold()
    return all(word in haystack for word in words)


def view(rows: Any, *, kind_filter: str = "all", group_filter: str = "", sort: str = "recommended",
         query: str = "") -> Json:
    """The choices that make sense for these rows, with the requested ones made valid.

    A filter kept from another provider (a maker it does not have, pictures it cannot make)
    is dropped instead of emptying the list.
    """
    models = [row for row in rows if isinstance(row, dict) and row.get("id")] if isinstance(rows, list) else []
    kinds = {name: sum(1 for row in models if kind(row) == name) for name in ("text", "image")}
    kind_filter = kind_filter if kind_filter in KINDS and kinds.get(kind_filter, 1) else "all"
    of_kind = [row for row in models if kind_filter == "all" or kind(row) == kind_filter]
    groups: dict[str, int] = {}
    for row in of_kind:
        label = group(row)
        if label:
            groups[label] = groups.get(label, 0) + 1
    group_filter = group_filter if group_filter in groups else ""
    sorts = ["recommended"]
    if sum(1 for row in models if price(row)) >= 2:
        sorts += ["price_low", "price_high"]
    if sum(1 for row in models if score(row)) >= 2:
        sorts.append("capability")
    sort = sort if sort in sorts else "recommended"
    both = bool(kinds["text"] and kinds["image"])
    return {
        "total": len(models),
        "kind": kind_filter, "group": group_filter, "sort": sort,
        "query": " ".join(str(query).split())[:MAX_QUERY] if isinstance(query, str) else "",
        "kinds": ([{"id": "all", "count": len(models)}, {"id": "text", "count": kinds["text"]},
                   {"id": "image", "count": kinds["image"]}] if both else []),
        "groups": ([{"id": label, "count": count} for label, count in groups.items()] if len(groups) > 1 else []),
        "sorts": sorts if len(sorts) > 1 else [],
        # Aids are offered when there is something to choose between and enough rows to get lost in.
        "useful": len(models) >= MIN_USEFUL and (both or len(groups) > 1 or len(sorts) > 1),
    }


def organize(rows: Any, *, kind_filter: str = "all", group_filter: str = "", sort: str = "recommended",
             query: str = "", keep: str = "") -> list[Json]:
    """The rows to show, in order. ``keep`` (the model in use) stays listed even when filtered out."""
    state = view(rows, kind_filter=kind_filter, group_filter=group_filter, sort=sort, query=query)
    models = [row for row in rows if isinstance(row, dict) and row.get("id")] if isinstance(rows, list) else []
    words = state["query"].casefold().split()
    shown = [{**row, "kind": kind(row), "group": group(row)} for row in models
             if (state["kind"] == "all" or kind(row) == state["kind"])
             and (not state["group"] or group(row) == state["group"])
             and (not words or _matches(row, words))]
    if state["sort"] in {"price_low", "price_high"}:
        cheapest_first = state["sort"] == "price_low"
        # Text and pictures are priced in different units: each kind keeps its own ranking.
        shown.sort(key=lambda row: (row["kind"] == "image", not price(row),
                                    price(row) if cheapest_first else -price(row)))
    elif state["sort"] == "capability":
        shown.sort(key=lambda row: (row["kind"] == "image", not score(row), -score(row)))
    if keep and not any(row["id"] == keep for row in shown):
        current = next((row for row in models if row.get("id") == keep), None)
        if current is not None:
            shown.insert(0, {**current, "kind": kind(current), "group": group(current), "outside_filter": True})
    return shown


def sections(rows: Any, **state: Any) -> list[Json]:
    """The same rows as consecutive titled groups, for a page of cards.

    The recommended order is the catalog's own, read maker by maker; a ranking by price or
    capability is read as one list, split only between text and pictures.
    """
    shown = organize(rows, **state)
    ranked = view(rows, **{key: value for key, value in state.items() if key != "keep"})["sort"] != "recommended"
    result: list[Json] = []
    for row in shown:
        if ranked:
            key = IMAGE_SECTION if row["kind"] == "image" else TEXT_SECTION
        else:
            key = row["group"]
        if not result or result[-1]["id"] != key:
            # A maker listed in two places of the catalog continues its earlier section.
            earlier = next((section for section in result if section["id"] == key), None) if not ranked else None
            if earlier is not None:
                earlier["models"].append(row)
                continue
            result.append({"id": key, "models": []})
        result[-1]["models"].append(row)
    if ranked and len(result) == 1:
        result[0]["id"] = ""
    return result
