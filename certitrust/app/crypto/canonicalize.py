from datetime import date, datetime

def canonicalize(field_name: str, value) -> str:
    if value is None:
        return f"{field_name}:"

    # Convert to string and strip
    val_str = str(value).strip()

    if val_str == "":
        return f"{field_name}:"

    if field_name in ("CGPA", "GPA") or field_name.endswith("_GPA"):
        try:
            return f"{field_name}:{float(val_str):.2f}"
        except (ValueError, TypeError):
            return f"{field_name}:{val_str}"

    if isinstance(value, (date, datetime)):
        return f"{field_name}:{value.strftime('%Y-%m-%d')}"

    return f"{field_name}:{val_str}"