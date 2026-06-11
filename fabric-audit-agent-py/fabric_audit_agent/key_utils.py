"""Key helpers. Faithful port of the Node ``core/key-utils.js``."""


def domain_of(key):
    """Extract the domain prefix from a finding key ('type::resource' -> 'type' -> 'domain')."""
    if not isinstance(key, str):
        return "other"
    type_ = key.split("::")[0]
    return type_.split(".")[0] if "." in type_ else "other"
