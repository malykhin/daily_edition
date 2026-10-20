"""Strict generation contract. The public edition retains src/lib/editions.ts fields."""
S = {"type": "string"}
N = {"type": "number"}
def obj(**fields):
    return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}
def arr(item):
    return {"type": "array", "items": item}
ITEM = obj(date=S, title=S, description=S, url=S)
EDITION = obj(
    date=S, status={"type": "string", "enum": ["published"]}, intro=S,
    weather=obj(title=S, summary=S, high=N, low=N),
    stories=arr(obj(section=S, title=S, summary=S, url=S, tag=S)),
    markets=arr(obj(name=S, value=S, change=S)),
    events=arr(ITEM), meetups=arr(ITEM), dining=arr(ITEM),
    watchers=arr(obj(name=S, status=S)))
EVIDENCE = obj(
    path=S, url=S, checked_at=S, as_of=S,
    kind={"type": "string", "enum": ["weather", "news", "market", "event", "meetup", "dining", "promotion"]},
    event_date=S, venue=S, registration_url=S)
SCHEMA = obj(edition=EDITION, evidence=arr(EVIDENCE),
             weather_days=arr(obj(date=S, high=N, low=N, url=S)),
             omissions=arr(obj(section=S, reason=S)))

# This validator deliberately handles only keywords used above, without runtime dependencies.
def check(value, schema, path="$"):
    typ = schema["type"]
    good = {"object": type(value) is dict, "array": type(value) is list,
            "string": type(value) is str,
            "number": type(value) in (int, float)}[typ]
    if not good:
        raise ValueError(f"{path}: expected {typ}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: invalid enum")
    if typ == "object":
        if set(value) != set(schema["required"]):
            raise ValueError(f"{path}: missing or unexpected fields")
        for key, child in schema["properties"].items():
            check(value[key], child, f"{path}/{key}")
    if typ == "array":
        for i, item in enumerate(value):
            check(item, schema["items"], f"{path}/{i}")
